"""기술적 추세 오케스트레이션 — 국면·상대강도 계산에 필요한 봉을 로드해 도메인에 넘긴다.

순수 계산은 domain/stage·relative_strength 가 맡고, 여기서는 종목·벤치마크 지수 봉을
candle_service 로 확보(DB 우선)해 조립한다. 벤치마크는 종목 시장(KOSPI/KOSDAQ)으로 자동 선택.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain import elliott, relative_strength, stage
from app.services import candle_service

# 종목 시장 → 벤치마크 지수 심볼(price_candles 에 지수 봉이 이 코드로 저장됨).
_BENCHMARK = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ"}
_DEFAULT_BENCHMARK = "KOSPI"
_ELLIOTT_BARS = 500  # 엘리엇 파동 분석 구간(최근 ~2년) — 과거 스윙 노이즈 배제


@dataclass
class TrendResult:
    stages: dict[str, stage.StageResult]  # frame(short/mid/long) → 국면
    stage_segments: list[dict]  # 중기(150) 국면 구간 [{stage, from, to}] — 차트 배경밴드용
    rs: relative_strength.RelativeStrength
    benchmark: str  # 사용한 벤치마크 지수
    elliott: elliott.ElliottResult  # 엘리엇 파동 추정(실험적)


def compute_trend(db: Session, code: str, market: str | None) -> TrendResult:
    """종목 일봉 + 벤치마크 지수로 와인스타인 국면·Mansfield RS·엘리엇 파동 추정을 계산한다."""
    stock_rows = candle_service.ensure_periodic(db, code, "day")
    closes = [r.close for r in stock_rows]
    dates = [r.bar_date.isoformat() for r in stock_rows]

    stages = {
        frame: stage.classify(closes, period) for frame, period in stage.FRAME_PERIODS.items()
    }
    stage_segments = stage.segments(closes, dates, stage.FRAME_PERIODS["mid"])

    benchmark = _BENCHMARK.get(market or "", _DEFAULT_BENCHMARK)
    bench_rows = candle_service.ensure_periodic(db, benchmark, "day")
    rs = relative_strength.compute(
        [(r.bar_date.isoformat(), r.close) for r in stock_rows],
        [(r.bar_date.isoformat(), r.close) for r in bench_rows],
    )
    # 엘리엇은 최근 구조만 의미 있음 — 최근 ~2년(500봉)으로 피벗·파동을 한정한다(과거 노이즈 배제).
    recent = list(zip(dates, closes, strict=True))[-_ELLIOTT_BARS:]
    wave = elliott.analyze(recent)
    return TrendResult(
        stages=stages, stage_segments=stage_segments, rs=rs, benchmark=benchmark, elliott=wave
    )
