"""Mansfield 상대강도(RS) — 지수 대비 초과성과. RSI(오실레이터)와 무관.

RP(t)  = 종목종가/벤치마크종가 * 100         (Relative Performance, 비율선)
MRP(t) = (RP(t) / SMA(RP, n)(t) - 1) * 100    (Mansfield RS, 0중심)
  n = 200(일봉). MRP>0 = 지수 아웃퍼폼, <0 = 언더퍼폼. 0선 상향 돌파 = 주도주 후보.

종목·벤치마크 종가를 날짜로 정렬·교집합해 같은 날끼리 비율을 낸다. I/O 없음(순수).
"""

from __future__ import annotations

from dataclasses import dataclass

SMA_PERIOD = 200  # RP 정규화 이동평균(일봉 기준)


@dataclass
class RelativeStrengthPoint:
    date: str  # YYYY-MM-DD
    value: float  # MRP (0중심)


@dataclass
class RelativeStrength:
    series: list[RelativeStrengthPoint]  # 시계열(차트 오버레이용)
    latest: float | None  # 최신 MRP
    outperforming: bool | None  # latest > 0


def compute(
    stock: list[tuple[str, float]], benchmark: list[tuple[str, float]], period: int = SMA_PERIOD
) -> RelativeStrength:
    """(날짜, 종가) 쌍 리스트 두 개로 Mansfield RS 시계열을 만든다.

    두 시계열을 날짜 교집합으로 정렬해 같은 날끼리 비율을 낸다. 공통일이 period 미만이면 빈 결과.
    """
    bench_by_date = {d: c for d, c in benchmark if c > 0}
    rp: list[tuple[str, float]] = []
    for d, c in sorted(stock):
        b = bench_by_date.get(d)
        if b and c > 0:
            rp.append((d, c / b * 100))

    if len(rp) < period:
        return RelativeStrength(series=[], latest=None, outperforming=None)

    values = [v for _, v in rp]
    series: list[RelativeStrengthPoint] = []
    running = sum(values[:period])
    for i in range(period - 1, len(values)):
        if i >= period:
            running += values[i] - values[i - period]
        sma = running / period
        if sma > 0:
            mrp = (values[i] / sma - 1) * 100
            series.append(RelativeStrengthPoint(date=rp[i][0], value=round(mrp, 2)))

    if not series:
        return RelativeStrength(series=[], latest=None, outperforming=None)
    latest = series[-1].value
    return RelativeStrength(series=series, latest=latest, outperforming=latest > 0)
