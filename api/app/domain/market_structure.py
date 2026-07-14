"""시장 구조(스윙) 분석 — LL/HL/LH/HH 스윙 시퀀스 + 국면 전환 조짐. 순수 도메인(I/O 없음).

와인스타인/다우 구조 관점: 추세는 스윙 고·저점의 관계로 정의된다.
- HH(고점 상향)+HL(저점 상향) = 상승 구조(Stage2 유지).
- LH(고점 하향)+LL(저점 하향) = 하락 구조(Stage4).
- 하락 뒤 첫 HL(저점 상향 반전) = Stage1→2 조짐(바닥 다지기 끝).
- 상승 뒤 첫 LH(고점 하향 반전) = Stage3→4 조짐(천정 롤오버).

elliott.zigzag(반전 임계 필터)로 스윙 피벗을 뽑아 재사용한다. 프레임별 변동성에 맞춰 임계(%)를
다르게 준다(일봉 작게·주봉 크게). 국면 판별의 보조 축이자, 매수/매도 타점 근거로 노출한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.elliott import Pivot, zigzag


@dataclass
class SwingStructure:
    """최근 스윙 구조 요약."""

    trend: str  # up | down | range | none — 스윙 고·저 관계로 본 구조 추세
    last_high: str | None  # HH | LH | none — 최근 고점이 직전 고점 대비
    last_low: str | None  # HL | LL | none — 최근 저점이 직전 저점 대비
    setup: str | None  # stage1_to_2 | stage3_to_4 | None — 전환 조짐
    pivots: list[Pivot]  # 스윙 피벗(고/저 교대). 지지·저항·박스권 산출에도 재사용


# 프레임별 ZigZag 반전 임계(비율). 일봉은 잔파동이 많아 작게, 주봉은 크게(스윙만).
SWING_THRESHOLD = {"day": 0.06, "week": 0.10, "month": 0.15}


def _swing_threshold(bar: str) -> float:
    return SWING_THRESHOLD.get(bar, 0.08)


def _last_two(pivots: list[Pivot], kind: str) -> tuple[Pivot, Pivot] | None:
    """해당 종류(high|low) 피벗 중 최근 2개(직전, 최근). 2개 미만이면 None."""
    same = [p for p in pivots if p.kind == kind]
    if len(same) < 2:
        return None
    return same[-2], same[-1]


def analyze(
    dates: list[str], closes: list[float], bar: str = "day"
) -> SwingStructure:
    """스윙 구조 분석. (날짜, 종가) 시계열 → SwingStructure. 데이터 부족 시 none 구조."""
    empty = SwingStructure("none", None, None, None, [])
    if len(closes) != len(dates) or len(closes) < 4:
        return empty
    pivots = zigzag(list(zip(dates, closes, strict=True)), _swing_threshold(bar))
    if len(pivots) < 3:
        return SwingStructure("none", None, None, None, pivots)

    highs = _last_two(pivots, "high")
    lows = _last_two(pivots, "low")
    last_high = None if highs is None else ("HH" if highs[1].price > highs[0].price else "LH")
    last_low = None if lows is None else ("HL" if lows[1].price > lows[0].price else "LL")

    # 구조 추세: 고·저 둘 다 상향=up, 둘 다 하향=down, 엇갈리면 range.
    if last_high == "HH" and last_low == "HL":
        trend = "up"
    elif last_high == "LH" and last_low == "LL":
        trend = "down"
    elif last_high is None and last_low is None:
        trend = "none"
    else:
        trend = "range"

    # 전환 조짐: 마지막 스윙이 무엇을 새로 만들었나(피벗 시간순 마지막 종류로 판단).
    #  - 하락 구조에서 저점이 상향 반전(HL) = 바닥 다지기 끝 → Stage1→2 조짐.
    #  - 상승 구조에서 고점이 하향 반전(LH) = 천정 롤오버 → Stage3→4 조짐.
    setup = None
    recent_kind = pivots[-1].kind  # 가장 최근 확정/잠정 피벗 종류
    if recent_kind == "low" and last_low == "HL" and last_high != "HH":
        setup = "stage1_to_2"  # 저점을 높이기 시작(하락→바닥 탈출 조짐)
    elif recent_kind == "high" and last_high == "LH" and last_low != "LL":
        setup = "stage3_to_4"  # 고점을 낮추기 시작(상승→천정 이탈 조짐)

    return SwingStructure(
        trend=trend, last_high=last_high, last_low=last_low, setup=setup, pivots=pivots
    )


# ── 박스권(수평 지지/저항) + 거래량 동반 돌파/이탈 ─────────────────────────
# 스윙 고점 군집 = 저항, 저점 군집 = 지지. 여러 번 눌린 레벨이라 단일 최고/최저(Donchian)보다
# 신뢰도가 높다. 박스 상단 거래량 돌파 = Stage1→2 진입 타점, 하단 이탈 = Stage3→4 매도 타점.
BOX_CLUSTER = 0.04  # 이 비율 이내 스윙점들은 같은 레벨(박스 경계)로 군집
BOX_VOL_MULT = 1.5  # 돌파/이탈 봉 거래량이 최근 평균의 이 배수↑면 '확정'


@dataclass
class BoxSignal:
    """박스권(수평 지지/저항)과 최신봉의 경계 돌파/이탈 신호."""

    support: float | None  # 박스 하단(지지, 저점 군집 대표값)
    resistance: float | None  # 박스 상단(저항, 고점 군집 대표값)
    event: str  # breakout(상단 돌파) | breakdown(하단 이탈) | inside | none
    vol_confirmed: bool  # 돌파/이탈 봉 거래량이 평균 대비 확정 배수↑인지


def _cluster_level(prices: list[float], anchor: str) -> float | None:
    """스윙점 가격들에서 대표 레벨. anchor='high'면 상단 군집(최고 근처), 'low'면 하단 군집.

    최고(또는 최저)에서 BOX_CLUSTER 이내에 든 점들의 평균 — '여러 번 닿은' 레벨을 대표값으로.
    """
    if not prices:
        return None
    if anchor == "high":
        top = max(prices)
        near = [p for p in prices if p >= top * (1 - BOX_CLUSTER)]
    else:
        bot = min(prices)
        near = [p for p in prices if p <= bot * (1 + BOX_CLUSTER)]
    return sum(near) / len(near) if near else None


def box_signal(
    pivots: list[Pivot],
    closes: list[float],
    volumes: list[int] | None = None,
    lookback_pivots: int = 6,
) -> BoxSignal:
    """최근 스윙 피벗으로 박스(지지/저항)를 만들고 최신 종가의 돌파/이탈을 판정한다.

    최근 lookback_pivots 개 피벗의 고점 군집=저항, 저점 군집=지지. 최신 종가가 저항 초과=breakout,
    지지 미만=breakdown, 그 사이=inside. 거래량을 주면 돌파/이탈 봉이 평균의 BOX_VOL_MULT 배↑인지
    확인(vol_confirmed). 피벗·데이터 부족 시 none.
    """
    none = BoxSignal(None, None, "none", False)
    if len(pivots) < 3 or len(closes) < 5:
        return none
    # 박스는 '직전까지 확립된' 레인지여야 한다. 마지막 피벗은 지금 돌파/이탈 중인 잠정 극점이라
    # (zigzag 이 최신 극점을 참고용으로 넣음) 박스 경계에서 제외한다 — 안 그러면 돌파봉이 스스로
    # 저항을 끌어올려 영원히 inside 로 잡힌다.
    established = pivots[:-1]
    recent = established[-lookback_pivots:]
    highs = [p.price for p in recent if p.kind == "high"]
    lows = [p.price for p in recent if p.kind == "low"]
    if not highs or not lows:
        return none
    resistance = _cluster_level(highs, "high")
    support = _cluster_level(lows, "low")
    if resistance is None or support is None or resistance <= support:
        return none

    last = closes[-1]
    event = "breakout" if last > resistance else "breakdown" if last < support else "inside"
    vol_confirmed = False
    if volumes and len(volumes) == len(closes) and len(volumes) >= 6 and event in ("breakout", "breakdown"):
        base = sum(volumes[-6:-1]) / 5
        vol_confirmed = base > 0 and volumes[-1] >= base * BOX_VOL_MULT
    return BoxSignal(
        support=round(support, 2), resistance=round(resistance, 2),
        event=event, vol_confirmed=vol_confirmed,
    )
