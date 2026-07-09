"""섹터 로테이션 + 시황 대시보드 — 기존 리포트·센티먼트·지수·무역 데이터 합성."""

from __future__ import annotations

import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
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
from app.schemas import MarketOverview, SectorFlowRow, SectorRow
from app.services import sector_flow
from reporter import us_market

router = APIRouter(prefix="/api", tags=["market"])

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
