"""기술적 추세 오케스트레이션 — 국면·상대강도 계산에 필요한 봉을 로드해 도메인에 넘긴다.

순수 계산은 domain/stage·relative_strength 가 맡고, 여기서는 종목·벤치마크 지수 봉을
candle_service 로 확보(DB 우선, 일봉)해 조립한다. 국면은 지평별 봉단위(단기 일/중기 주/장기 월)로
일봉을 도메인에서 리샘플해 분류한다. 벤치마크는 종목 시장(KOSPI/KOSDAQ)으로 자동 선택.

엘리엇 파동 분석은 부적절 배치가 잦아 노출에서 제거했다(연구 과제). domain/elliott 는 zigzag 를
market_structure 가 재사용하므로 보존하되, 여기서 analyze 를 호출하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import TrendCache, UniverseSnapshot
from app.domain import market_structure, relative_strength, stage
from app.schemas import (
    CompanyTrend,
    RelStrengthPoint,
    SecularView,
    StageFrame,
    StageSegment,
)
from app.services import candle_service

# 종목 시장 → 벤치마크 지수 심볼(price_candles 에 지수 봉이 이 코드로 저장됨).
_BENCHMARK = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ"}
_DEFAULT_BENCHMARK = "KOSPI"
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
    box_by_frame: dict[str, market_structure.BoxSignal]  # frame → 박스권 지지/저항·돌파 이벤트
    rs: relative_strength.RelativeStrength
    benchmark: str  # 사용한 벤치마크 지수
    secular: stage.SecularContext  # 장기 평균(secular) 대비 위치 — 전환 프레임과 직교

    @property
    def stage_segments(self) -> list[dict]:
        """하위호환: 대표 프레임(중기) 국면 구간."""
        return self.segments_by_frame.get(_SEGMENT_FRAME, [])


def compute_trend(db: Session, code: str, market: str | None) -> TrendResult:
    """종목 일봉을 지평별 봉단위(OHLCV)로 리샘플해 국면(단/중/장)을, 일봉으로 RS 를 계산한다."""
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
    box_by_frame: dict[str, market_structure.BoxSignal] = {}
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
        struct = market_structure.analyze(b.dates, b.closes, frame.bar)
        structure_by_frame[name] = struct
        box_by_frame[name] = market_structure.box_signal(struct.pivots, b.closes, b.volumes)

    # secular 오버레이 — 데이터 허락 최장 월봉 MA 대비 위치(프레임과 별개, 항상 월봉 기준).
    monthly = stage.resample_ohlcv(dates, highs, lows, closes, volumes, "month")
    secular = stage.secular_context(monthly.closes)

    benchmark = _BENCHMARK.get(market or "", _DEFAULT_BENCHMARK)
    bench_rows = candle_service.ensure_periodic(db, benchmark, "day")
    rs = relative_strength.compute(
        [(r.bar_date.isoformat(), r.close) for r in stock_rows],
        [(r.bar_date.isoformat(), r.close) for r in bench_rows],
    )
    return TrendResult(
        stages=stages,
        low_confidence=low_confidence,
        segments_by_frame=segments_by_frame,
        structure_by_frame=structure_by_frame,
        box_by_frame=box_by_frame,
        rs=rs,
        benchmark=benchmark,
        secular=secular,
    )


def build_company_trend(code: str, result: TrendResult, rs_rating: int | None) -> CompanyTrend:
    """TrendResult(+장중 rs_rating)를 응답 DTO 로 조립. 라우터·배치·폴백이 공유하는 단일 변환."""
    return CompanyTrend(
        stock_code=code,
        benchmark=result.benchmark,
        stages=[
            StageFrame(
                frame=frame,
                bar=stage.FRAMES[frame].bar,
                period=stage.FRAMES[frame].ma_period,
                stage=result.stages[frame].stage,
                label=result.stages[frame].label,
                ma_dir=result.stages[frame].ma_dir,
                quality=result.stages[frame].quality,
                volume_signal=result.stages[frame].volume_signal,
                volatility=result.stages[frame].volatility,
                low_confidence=result.low_confidence[frame],
                channel_pos=result.stages[frame].channel_pos,
                breakout=result.stages[frame].breakout,
                structure=result.structure_by_frame[frame].trend,
                last_high=result.structure_by_frame[frame].last_high,
                last_low=result.structure_by_frame[frame].last_low,
                setup=result.structure_by_frame[frame].setup,
                box_support=result.box_by_frame[frame].support,
                box_resistance=result.box_by_frame[frame].resistance,
                box_event=result.box_by_frame[frame].event,
                box_vol_confirmed=result.box_by_frame[frame].vol_confirmed,
            )
            for frame in ("short", "mid", "long")
        ],
        stage_segments=[
            StageSegment(stage=s["stage"], from_date=s["from"], to_date=s["to"])
            for s in result.stage_segments
        ],
        segments_by_frame={
            frame: [
                StageSegment(stage=s["stage"], from_date=s["from"], to_date=s["to"])
                for s in segs
            ]
            for frame, segs in result.segments_by_frame.items()
        },
        rs_series=[RelStrengthPoint(date=p.date, value=p.value) for p in result.rs.series],
        rs_latest=result.rs.latest,
        rs_outperforming=result.rs.outperforming,
        rs_rating=rs_rating,
        elliott=None,  # 엘리엇 파동 노출 제거(부적절 배치 잦음). 필드는 하위호환 유지.
        secular=SecularView(
            ma_months=result.secular.ma_months,
            position=result.secular.position,
            ma_dir=result.secular.ma_dir,
            ratio=result.secular.ratio,
        ),
    )


# ── 사전계산 캐시 (rich JSON) ────────────────────────────────────────────
# rs_rating 은 장중 갱신되는 스칼라라 페이로드에 넣지 않고 조회 시 스냅샷에서 붙인다(아래 _RS_KEY).
_RS_KEY = "rs_rating"


def _latest_day(db: Session, code: str) -> date | None:
    """종목 최신 일봉 확정 날짜(캐시 신선도 기준). 봉 없으면 None."""
    from app.db.models import PriceCandle, Timeframe

    return db.scalar(
        select(PriceCandle.bar_date)
        .where(PriceCandle.stock_code == code, PriceCandle.timeframe == Timeframe.DAY)
        .order_by(PriceCandle.bar_date.desc())
        .limit(1)
    )


def store_trend(db: Session, code: str, result: TrendResult) -> date | None:
    """이미 계산된 TrendResult 를 CompanyTrend JSON 으로 TrendCache 에 upsert. as_of(최신봉) 반환.

    rs_rating 은 저장하지 않는다(장중 갱신 → 조회 시 스냅샷에서). 최신봉 없으면 저장 생략."""
    as_of = _latest_day(db, code)
    if as_of is None:
        return None
    payload = build_company_trend(code, result, rs_rating=None).model_dump()
    payload.pop(_RS_KEY, None)  # 장중 갱신값이라 캐시에서 제외
    stmt = insert(TrendCache).values(stock_code=code, as_of=as_of, payload=payload)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_trend_cache_code",
        set_={"as_of": as_of, "payload": payload, "updated_at": func.now()},
    )
    db.execute(stmt)
    db.commit()
    return as_of


def precompute_and_store(db: Session, code: str, market: str | None) -> date | None:
    """compute_trend 계산 + 저장(배치용). cache-aside 폴백은 store_trend 를 직접 쓴다."""
    if _latest_day(db, code) is None:
        return None
    return store_trend(db, code, compute_trend(db, code, market))


def run_trend_precompute_batch(db: Session) -> dict:
    """전 유니버스의 /trend 응답을 사전계산해 TrendCache 에 적재(야간 candle_batch 직후).

    상세페이지 trend 엔드포인트가 매 요청 재계산(warm 1초+)하던 것을 배치가 미리 채운다.
    종목당 compute_trend(~250ms)라 순차 처리, 한 종목 실패는 배치를 막지 않는다. 무네트워크
    (DB 봉만). 반환: {done, failed}.
    """
    import logging

    logger = logging.getLogger(__name__)
    as_of = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
    if as_of is None:
        return {"done": 0, "failed": 0}
    rows = db.execute(
        select(UniverseSnapshot.stock_code, UniverseSnapshot.market).where(
            UniverseSnapshot.snapshot_date == as_of,
            UniverseSnapshot.stock_type == "stock",
            UniverseSnapshot.market_cap.is_not(None),
        )
    ).all()
    done = failed = 0
    for code, market in rows:
        try:
            if precompute_and_store(db, code, market):
                done += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            failed += 1
            logger.warning("trend precompute failed for %s: %s", code, e)
    logger.info("trend precompute batch: done=%d failed=%d", done, failed)
    return {"done": done, "failed": failed}


def get_cached_trend(db: Session, code: str) -> CompanyTrend | None:
    """TrendCache 에서 최신 확정봉 기준 캐시를 읽어 CompanyTrend 로 복원(rs_rating 은 스냅샷에서 주입).

    캐시가 없거나 최신봉보다 오래됐으면(신규 확정봉 생김) None → 호출측이 재계산·저장."""
    row = db.scalar(select(TrendCache).where(TrendCache.stock_code == code))
    if row is None:
        return None
    latest = _latest_day(db, code)
    if latest is not None and row.as_of < latest:
        return None  # 새 확정봉 있음 → stale
    snap = db.scalar(
        select(UniverseSnapshot.rs_rating)
        .where(UniverseSnapshot.stock_code == code)
        .order_by(UniverseSnapshot.snapshot_date.desc())
        .limit(1)
    )
    return CompanyTrend.model_validate({**row.payload, _RS_KEY: snap})
