"""기술적 추세 오케스트레이션 — 국면·상대강도·엘리엇 계산에 필요한 봉을 로드해 도메인에 넘긴다.

순수 계산은 domain/stage·relative_strength·elliott 가 맡고, 여기서는 종목·벤치마크 지수 봉을
candle_service 로 확보(DB 우선, 일봉)해 조립한다. 국면은 지평별 봉단위(단기 일/중기 주/장기 월)로
일봉을 도메인에서 리샘플해 분류한다. 벤치마크는 종목 시장(KOSPI/KOSDAQ)으로 자동 선택.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain import elliott, market_structure, relative_strength, stage
from app.services import candle_service

# 종목 시장 → 벤치마크 지수 심볼(price_candles 에 지수 봉이 이 코드로 저장됨).
_BENCHMARK = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ"}
_DEFAULT_BENCHMARK = "KOSPI"
_ELLIOTT_BARS = 500  # 엘리엇 파동 분석 구간(최근 ~2년) — 과거 스윙 노이즈 배제
# 배경밴드로 쓸 대표 프레임(중기=주봉 30주, 와인스타인 정통). 일봉 차트에도 주봉 종가 날짜가
# 존재하므로 그대로 배경밴드로 얹힌다.
_SEGMENT_FRAME = "mid"


# 프레임 국면을 '고신뢰'로 볼 최소 리샘플 봉 수(MA기간 + 기울기창 + 여유 문맥). 이 미만이면
# 국면은 내되 low_confidence 로 표시(특히 장기 월봉은 이력 부족 종목이 많음).
def _min_bars(frame: stage.Frame) -> int:
    return frame.ma_period + frame.slope_lookback + frame.ma_period // 2


@dataclass
class TrendResult:
    stages: dict[str, stage.StageResult]  # frame(short/mid/long) → 국면
    low_confidence: dict[str, bool]  # frame → 이력 부족(리샘플 봉 < 최소치)이면 True
    segments_by_frame: dict[str, list[dict]]  # frame → 국면 구간 [{stage, from, to}] (배경밴드)
    structure_by_frame: dict[str, market_structure.SwingStructure]  # frame → 스윙 구조·전환 조짐
    rs: relative_strength.RelativeStrength
    benchmark: str  # 사용한 벤치마크 지수
    elliott: elliott.ElliottResult  # 엘리엇 파동 추정(실험적)
    secular: stage.SecularContext  # 장기 평균(secular) 대비 위치 — 전환 프레임과 직교

    @property
    def stage_segments(self) -> list[dict]:
        """하위호환: 대표 프레임(중기) 국면 구간."""
        return self.segments_by_frame.get(_SEGMENT_FRAME, [])


def compute_trend(db: Session, code: str, market: str | None) -> TrendResult:
    """종목 일봉을 지평별 봉단위(OHLCV)로 리샘플해 국면(단/중/장)을, 일봉으로 RS·엘리엇을 계산한다."""
    stock_rows = candle_service.ensure_periodic(db, code, "day")
    closes = [r.close for r in stock_rows]
    dates = [r.bar_date.isoformat() for r in stock_rows]
    highs = [r.high for r in stock_rows]
    lows = [r.low for r in stock_rows]
    volumes = [int(r.volume or 0) for r in stock_rows]

    stages: dict[str, stage.StageResult] = {}
    low_confidence: dict[str, bool] = {}
    segments_by_frame: dict[str, list[dict]] = {}
    structure_by_frame: dict[str, market_structure.SwingStructure] = {}
    secular = stage.SecularContext(None, None, None, None)
    for name, frame in stage.FRAMES.items():
        b = stage.resample_ohlcv(dates, highs, lows, closes, volumes, frame.bar)
        stages[name] = stage.classify(
            b.closes, frame.ma_period, frame.slope_lookback, b.volumes, b.highs, b.lows
        )
        low_confidence[name] = len(b.closes) < _min_bars(frame)
        segments_by_frame[name] = stage.segments(
            b.closes, b.dates, frame.ma_period, frame.slope_lookback, frame.min_run,
            b.volumes, b.highs, b.lows,
        )
        structure_by_frame[name] = market_structure.analyze(b.dates, b.closes, frame.bar)

    # secular 오버레이 — 데이터 허락 최장 월봉 MA 대비 위치(프레임과 별개, 항상 월봉 기준).
    monthly = stage.resample_ohlcv(dates, highs, lows, closes, volumes, "month")
    secular = stage.secular_context(monthly.closes)

    benchmark = _BENCHMARK.get(market or "", _DEFAULT_BENCHMARK)
    bench_rows = candle_service.ensure_periodic(db, benchmark, "day")
    rs = relative_strength.compute(
        [(r.bar_date.isoformat(), r.close) for r in stock_rows],
        [(r.bar_date.isoformat(), r.close) for r in bench_rows],
    )
    # 엘리엇은 최근 구조만 의미 있음 — 최근 500봉으로 한정(과거 노이즈 배제).
    recent = list(zip(dates, closes, strict=True))[-_ELLIOTT_BARS:]
    wave = elliott.analyze(recent)
    return TrendResult(
        stages=stages,
        low_confidence=low_confidence,
        segments_by_frame=segments_by_frame,
        structure_by_frame=structure_by_frame,
        rs=rs,
        benchmark=benchmark,
        elliott=wave,
        secular=secular,
    )
