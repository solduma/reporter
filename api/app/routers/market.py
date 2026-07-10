"""섹터 로테이션 + 시황 대시보드 — 기존 리포트·센티먼트·지수·무역 데이터 합성."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import requests
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
from app.services import candle_service, chart, sector_flow
from reporter import sector_etf, us_market

router = APIRouter(prefix="/api", tags=["market"])

# tf 별 조회 범위: 일=2년, 주=10년, 월=3년(종목 상세와 통일).
_CHART_RANGE_DAYS = {"day": 365 * 2 + 10, "week": 365 * 10 + 30, "month": 365 * 3 + 30}

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
def market_overview(db: Session = Depends(get_session)) -> MarketOverview:
    """시황 대시보드 통합 — 미국지수 + 국내시황 요약 + 핫섹터 + 무역 스파크."""
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

    indices = _index_dicts(us_market.fetch_us_indices())
    # 국내 지수(코스피·코스닥) 옆에 원/달러 환율을 함께 노출한다.
    kr_indices = _index_dicts(us_market.fetch_kr_indices()) + _index_dicts(
        us_market.fetch_exchange_rates()
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


def _us_chart(symbol: str, tf: str) -> list[CandlePoint]:
    """미국 ETF/종목 봉 — foreign 실시간 조회(저장 안 함, 심볼이 6자리 초과라 PriceCandle 부적합)."""
    session = requests.Session()
    end = datetime.now()
    start = end - timedelta(days=_CHART_RANGE_DAYS[tf])
    fresh = chart.fetch_periodic_foreign(symbol, tf, start, end, session)
    return [
        CandlePoint(t=c.ts.date().isoformat(), o=c.open, h=c.high, low=c.low, c=c.close, v=c.volume)
        for c in fresh
    ]


@router.get("/chart", response_model=list[CandlePoint])
def chart_candles(
    bg: BackgroundTasks,
    symbol: str = Query(..., description="국내 6자리 코드 또는 미국 네이버 심볼"),
    market: str = Query(default="KR", pattern="^(KR|US)$"),
    tf: str = Query(default="day", pattern="^(day|week|month)$"),
    db: Session = Depends(get_session),
) -> list[CandlePoint]:
    """범용 봉 차트 — 섹터 ETF·지수·종목 공용. 국내=DB우선+백그라운드증분, 미국=실시간 foreign."""
    return _kr_chart(db, symbol, tf, bg) if market == "KR" else _us_chart(symbol, tf)


# 로그인 진입 시 미리 데워 둘 지수 심볼(일봉). 대시보드·비교 차트에서 가장 먼저 쓰인다.
_WARM_INDICES = ("KOSPI", "KOSDAQ")


def _warm() -> None:
    """지수 시세 캐시(120s TTL) 워밍 + 지수 일봉 증분 갱신. 실패는 흡수(백그라운드)."""
    try:
        # 대시보드 지수·환율 타일용 인메모리 캐시를 미리 채운다(첫 조회의 네이버 왕복 제거).
        us_market.fetch_us_indices()
        us_market.fetch_kr_indices()
        us_market.fetch_exchange_rates()
    except Exception:
        pass
    for sym in _WARM_INDICES:
        candle_service.refresh_periodic(sym, "day")


@router.post("/warm")
def warm(bg: BackgroundTasks) -> dict:
    """로그인 진입 시 프론트가 fire-and-forget 로 호출 — 지수 캐시·일봉을 백그라운드로 데운다."""
    bg.add_task(_warm)
    return {"ok": True}
