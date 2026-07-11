"""섹터 로테이션 + 시황 대시보드 — 기존 리포트·센티먼트·지수·무역 데이터 합성."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyMarketInfo,
    Report,
    ReportAnalysis,
    Sentiment,
    TradeStat,
)
from app.db.session import get_session
from app.schemas import (
    CandlePoint,
    ChartRef,
    MarketOverview,
    SectorChartMeta,
    SectorFlowDetail,
    SectorFlowRow,
    SectorRow,
)
from app.services import candle_service, market_quote, sector_flow
from reporter import sector_etf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["market"])

# tf 별 조회 범위: 일·주·월 모두 10년(종목 상세와 통일). 저장은 DB, 조회는 date-range 로 제한.
_CHART_RANGE_DAYS = {"day": 365 * 10 + 30, "week": 365 * 10 + 30, "month": 365 * 10 + 30}

# 섹터 ETF 로테이션은 시장당 12~15회 차트 조회라 무거워 프로세스 캐시(TTL)로 반복 부하를 막는다.
_FLOW_TTL = 300.0  # 초
_flow_cache: dict[str, tuple[float, list[SectorFlowRow]]] = {}

_SENT_CASE = case(
    (ReportAnalysis.sentiment == Sentiment.BUY, 1.0),
    (ReportAnalysis.sentiment == Sentiment.SELL, -1.0),
    else_=0.0,
)


def _sector_rows(db: Session, since: date) -> list[tuple[str, int, float]]:
    """산업별 (섹터, 리포트수, 평균 센티먼트). since 이후 발행 industry 리포트."""
    return list(
        db.execute(
            select(
                Report.industry_name,
                func.count(Report.id),
                func.avg(_SENT_CASE),
            )
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(
                Report.category == "industry",
                Report.industry_name.is_not(None),
                Report.published_date >= since,
            )
            .group_by(Report.industry_name)
        ).all()
    )


@router.get("/sectors", response_model=list[SectorRow])
def sectors(db: Session = Depends(get_session)) -> list[SectorRow]:
    """섹터 로테이션 랭킹 — 센티먼트·리포트 수 기반. 최근 30일."""
    rows = _sector_rows(db, date.today() - timedelta(days=30))
    if not rows:
        return []
    max_count = max(c for _, c, _ in rows) or 1
    out: list[SectorRow] = []
    for name, count, sent in rows:
        avg = float(sent or 0)
        # 로테이션 스코어: 센티먼트(0~1 정규화) 70% + 커버리지 비중 30%.
        rotation = (0.7 * (avg + 1) / 2 + 0.3 * count / max_count) * 100
        out.append(
            SectorRow(
                sector=name,
                report_count=count,
                avg_sentiment=round(avg, 2),
                rotation_score=round(rotation, 1),
            )
        )
    out.sort(key=lambda s: s.rotation_score, reverse=True)
    return out


@router.get("/sectors/flow", response_model=list[SectorFlowRow])
def sector_flow_rotation(
    market: str = Query(default="KR", pattern="^(KR|US)$"),
) -> list[SectorFlowRow]:
    """수급 기반 섹터 로테이션 — 섹터 ETF 일봉의 모멘텀·거래량·신고가·외국인 순증.

    flow_score 높은 순. 리포트 기반 /api/sectors 와 별개(실제 자금 흐름 관점).
    """
    return _flow_rows(market)


def _flow_rows(market: str) -> list[SectorFlowRow]:
    """시장의 섹터 flow 목록을 SectorFlowRow 로 변환(프로세스 캐시)."""
    cached = _flow_cache.get(market)
    if cached and time.monotonic() - cached[0] < _FLOW_TTL:
        return cached[1]
    rows = [
        SectorFlowRow(
            sector=f.sector,
            market=f.market,
            symbol=f.symbol,
            flow_score=f.flow_score,
            return_3m=f.return_3m,
            near_high_pct=f.near_high_pct,
            vol_ratio=f.vol_ratio,
            foreign_delta=f.foreign_delta,
        )
        for f in sector_flow.compute_flows(market)
    ]
    if rows:
        _flow_cache[market] = (time.monotonic(), rows)
    return rows


@router.get("/sectors/flow/detail", response_model=SectorFlowDetail)
def sector_flow_detail(industry: str = Query(...)) -> SectorFlowDetail:
    """산업명 → 국내 섹터 ETF flow + 대응 미국 섹터 ETF flow(선행). 섹터 상세 페이지용."""
    kr_sector = sector_etf.themes_to_kr_sector([industry])
    us_sector = sector_etf.kr_sector_to_us(kr_sector)
    kr_by_sector = {r.sector: r for r in _flow_rows("KR")}
    us_by_sector = {r.sector: r for r in _flow_rows("US")}
    return SectorFlowDetail(
        industry=industry,
        kr=kr_by_sector.get(kr_sector) if kr_sector else None,
        us=us_by_sector.get(us_sector) if us_sector else None,
    )


@router.get("/sectors/{industry}/charts", response_model=SectorChartMeta)
def sector_chart_meta(industry: str) -> SectorChartMeta:
    """섹터 상세 차트 구성 — 지수 쌍 + 국내/미국 섹터 추종 ETF 심볼(프론트가 /api/chart 로 조회)."""
    kr_sector = sector_etf.themes_to_kr_sector([industry])
    us_sector = sector_etf.kr_sector_to_us(kr_sector)
    kr_etf = sector_etf.kr_sector_etf(kr_sector) if kr_sector else None
    us_etf = sector_etf.us_sector_etf(us_sector)
    # 지수는 한·미 각각 그려야 하므로 쌍을 개별 ChartRef 로 펼친다.
    index_refs: list[ChartRef] = []
    for kr_name, kr_sym, us_name, us_sym in sector_etf.INDEX_PAIRS:
        index_refs.append(ChartRef(label=kr_name, symbol=kr_sym, market="KR"))
        index_refs.append(ChartRef(label=us_name, symbol=us_sym, market="US"))
    return SectorChartMeta(
        industry=industry,
        indices=index_refs,
        kr_etf=ChartRef(label=f"{kr_etf.sector}(국내)", symbol=kr_etf.symbol, market="KR")
        if kr_etf
        else None,
        us_etf=ChartRef(label=f"{us_etf.sector}(미국)", symbol=us_etf.symbol, market="US")
        if us_etf
        else None,
    )


@router.get("/market/overview", response_model=MarketOverview)
def market_overview(
    bg: BackgroundTasks, db: Session = Depends(get_session)
) -> MarketOverview:
    """시황 대시보드 통합 — 미국지수 + 국내시황 요약 + 핫섹터 + 무역 스파크.

    지수·환율은 DB 스냅샷(market_quote) 우선. 없으면 최초 1회 동기 스냅샷, 오래됐으면 백그라운드.
    """
    brief = db.scalars(
        select(DailyMarketInfo).order_by(DailyMarketInfo.market_date.desc()).limit(1)
    ).first()

    def _index_dicts(quotes) -> list[dict]:
        return [
            {
                "name": q.name,
                "close": q.close,
                "change": q.change,
                "change_ratio": q.change_ratio,
                "rising": q.rising,
            }
            for q in quotes
        ]

    # DB 우선 시세. 비어 있으면 최초 1회 동기 스냅샷, 신선하지 않으면 백그라운드 갱신.
    if not market_quote.latest_quotes(db, "us"):
        market_quote.snapshot_quotes(db)
    elif market_quote.is_stale(db):
        bg.add_task(_snapshot_quotes_bg)
    indices = _index_dicts(market_quote.latest_quotes(db, "us"))
    kr_indices = _index_dicts(market_quote.latest_quotes(db, "kr")) + _index_dicts(
        market_quote.latest_quotes(db, "fx")
    )

    hot = [
        {"sector": s.sector, "report_count": s.report_count, "avg_sentiment": s.avg_sentiment}
        for s in sectors(db)[:8]
    ]

    # 무역 스파크: HS 품목별 최신 수출액(있으면).
    trade_rows = db.execute(
        select(TradeStat.hs_code, func.max(TradeStat.period))
        .group_by(TradeStat.hs_code)
    ).all()
    trade_spark = []
    for hs, period in trade_rows[:5]:
        latest = db.scalar(
            select(TradeStat.export_usd).where(
                TradeStat.hs_code == hs, TradeStat.period == period
            )
        )
        trade_spark.append({"hs": hs, "period": period, "export_usd": latest})

    return MarketOverview(
        market_date=brief.market_date if brief else None,
        us_indices=indices,
        kr_indices=kr_indices,
        brief_summary=brief.summary if brief else "",
        hot_sectors=hot,
        trade_spark=trade_spark,
    )


def _kr_chart(db: Session, code: str, tf: str, bg: BackgroundTasks) -> list[CandlePoint]:
    """국내 종목/ETF 봉 — DB 우선 즉시 반환. 뒤처졌으면 백그라운드 증분 갱신을 예약한다."""
    rows = candle_service.ensure_periodic(db, code, tf)
    if candle_service.is_stale(db, code, tf):
        bg.add_task(candle_service.refresh_periodic, code, tf)
    return [
        CandlePoint(t=r.bar_date.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume)
        for r in rows
    ]


def _us_chart(db: Session, symbol: str, tf: str, bg: BackgroundTasks) -> list[CandlePoint]:
    """미국 ETF/지수 봉 — DB 우선. 미국 심볼도 PriceCandle 에 저장(stock_code 16자로 확장)."""
    rows = candle_service.ensure_periodic(db, symbol, tf, market="US")
    if candle_service.is_stale(db, symbol, tf):
        bg.add_task(candle_service.refresh_periodic, symbol, tf, "US")
    return [
        CandlePoint(t=r.bar_date.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume)
        for r in rows
    ]


@router.get("/chart", response_model=list[CandlePoint])
def chart_candles(
    bg: BackgroundTasks,
    symbol: str = Query(..., description="국내 6자리 코드 또는 미국 네이버 심볼"),
    market: str = Query(default="KR", pattern="^(KR|US)$"),
    tf: str = Query(default="day", pattern="^(day|week|month)$"),
    db: Session = Depends(get_session),
) -> list[CandlePoint]:
    """범용 봉 차트 — 섹터 ETF·지수·종목 공용. 국내·미국 모두 DB 우선 + 백그라운드 증분."""
    return _kr_chart(db, symbol, tf, bg) if market == "KR" else _us_chart(db, symbol, tf, bg)


# 로그인 진입 시 미리 데워 둘 지수 심볼(일봉). 대시보드·비교 차트에서 가장 먼저 쓰인다.
_WARM_INDICES = ("KOSPI", "KOSDAQ")


# 스냅샷 단일 실행 가드 — 시세 TTL(30s)이 짧아 폴링마다 is_stale 이 True 가 되면 매 요청이
# 백그라운드 스냅샷을 예약한다. 동시 뷰어 N 명이면 스냅샷 스레드가 우르르 떠 네이버를 몰아치므로,
# 이미 도는 스냅샷이 있으면 새 요청은 건너뛴다(single-flight).
_snapshot_inflight = False
_snapshot_lock = threading.Lock()


def _snapshot_quotes_bg() -> None:
    """백그라운드 지수·환율 스냅샷 적재 — 자체 세션. 이미 실행 중이면 건너뛴다. 실패 흡수."""
    global _snapshot_inflight
    from app.db.session import SessionLocal

    with _snapshot_lock:
        if _snapshot_inflight:
            return
        _snapshot_inflight = True
    db = SessionLocal()
    try:
        market_quote.snapshot_quotes(db)
    except Exception as e:
        db.rollback()
        logger.warning("market quote snapshot failed: %s", e)
    finally:
        db.close()
        with _snapshot_lock:
            _snapshot_inflight = False


def _warm() -> None:
    """지수·환율 시세를 DB 스냅샷으로 적재 + 지수 일봉 증분 갱신. 실패는 흡수(백그라운드)."""
    _snapshot_quotes_bg()  # 대시보드 타일용 시세를 DB 에 미리 채운다(첫 조회 지연 제거)
    for sym in _WARM_INDICES:
        candle_service.refresh_periodic(sym, "day")


@router.post("/warm")
def warm(bg: BackgroundTasks) -> dict:
    """로그인 진입 시 프론트가 fire-and-forget 로 호출 — 지수 캐시·일봉을 백그라운드로 데운다."""
    bg.add_task(_warm)
    return {"ok": True}
