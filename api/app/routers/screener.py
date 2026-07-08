"""스몰캡 성장 스크리너 — 유니버스 스냅샷 + 성장지표(YoY·모멘텀) + 성장스코어.

시총·유동성으로 스몰캡을 좁히고, 매출/영업이익 YoY·흑자전환·3개월 모멘텀으로
성장주를 가려낸다. 성장스코어는 필터 통과 집합 내 백분위로 산출해 정렬한다.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import GrowthMetric, UniverseSnapshot
from app.db.session import get_session
from app.schemas import ScreenerResult, ScreenerRow

router = APIRouter(prefix="/api/screener", tags=["screener"])


def _latest_date(db: Session) -> date | None:
    return db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))


def _percentile_ranker(values: list[float]):
    """값 리스트에 대해 백분위(0~1) 함수를 만든다. 결측·소표본에 강건."""
    clean = sorted(v for v in values if v is not None)
    n = len(clean)
    if n <= 1:
        return lambda v: 0.5 if v is not None else 0.0

    def rank(v: float | None) -> float:
        if v is None:
            return 0.0
        lo = sum(1 for c in clean if c < v)
        return lo / (n - 1)

    return rank


def _growth_score(u, g, rev_rank, op_rank, mom_rank) -> float:
    """성장스코어(0~100). 매출·영익 YoY 백분위 + 모멘텀 + 흑자전환 보너스."""
    rev = rev_rank(g.revenue_yoy if g else None)
    op = op_rank(g.op_yoy if g else None)
    mom = mom_rank(u.momentum_3m)
    turn_bonus = 0.15 if (g and g.op_turnaround) else 0.0
    score = 0.35 * rev + 0.30 * op + 0.20 * mom + turn_bonus
    return round(min(score, 1.0) * 100, 1)


@router.get("", response_model=ScreenerResult)
def screen(
    mktcap_max: int | None = Query(default=500_000_000_000, description="시총 상한(원). 기본 5천억"),
    mktcap_min: int | None = Query(default=None, description="시총 하한(원)"),
    liq_min: int | None = Query(default=100_000_000, description="거래대금 최소(원). 기본 1억"),
    rev_yoy_min: float | None = Query(default=None, description="매출 YoY 최소(0.15=+15%)"),
    op_growth: str | None = Query(default=None, pattern="^(turnaround|growth)$"),
    mom_min: float | None = Query(default=None, description="3개월 모멘텀 최소%"),
    mom_max: float | None = Query(default=None, description="3개월 모멘텀 최대%(과열 컷)"),
    market: str | None = Query(default=None, pattern="^(KOSPI|KOSDAQ)$"),
    include_etf: bool = Query(default=False, description="ETF/ETN 포함(기본 제외)"),
    sort: str = Query(default="score", description="score|market_cap|momentum|rev_yoy|trading_value"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> ScreenerResult:
    as_of = _latest_date(db)
    if not as_of:
        return ScreenerResult(as_of=None, total=0, items=[])

    U, G = UniverseSnapshot, GrowthMetric
    conds = [
        U.snapshot_date == as_of,
        U.market_cap.is_not(None),
        U.trading_value > 0,
    ]
    if mktcap_max is not None:
        conds.append(U.market_cap <= mktcap_max)
    if mktcap_min is not None:
        conds.append(U.market_cap >= mktcap_min)
    if liq_min is not None:
        conds.append(U.trading_value >= liq_min)
    if market:
        conds.append(U.market == market)
    if not include_etf:
        conds.append(U.stock_type == "stock")
        conds.append(~U.stock_name.op("~")(r"우[A-C]?$"))  # 우선주 제외
    if rev_yoy_min is not None:
        conds.append(G.revenue_yoy >= rev_yoy_min)
    if op_growth == "turnaround":
        conds.append(G.op_turnaround.is_(True))
    elif op_growth == "growth":
        conds.append(G.op_yoy > 0)
    if mom_min is not None:
        conds.append(U.momentum_3m >= mom_min)
    if mom_max is not None:
        conds.append(U.momentum_3m <= mom_max)

    base = select(U, G).outerjoin(G, G.stock_code == U.stock_code).where(*conds)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    # 성장스코어 정렬은 전체 통과 집합에 대한 백분위가 필요 → 전량 로드 후 파이썬 정렬.
    # 그 외 정렬은 DB 정렬 + 페이지네이션(대량에 효율적).
    if sort == "score":
        rows = [(u, g) for u, g in db.execute(base).all()]
        rev_rank = _percentile_ranker([g.revenue_yoy for _, g in rows if g])
        op_rank = _percentile_ranker([g.op_yoy for _, g in rows if g])
        mom_rank = _percentile_ranker([u.momentum_3m for u, _ in rows])
        scored = [(u, g, _growth_score(u, g, rev_rank, op_rank, mom_rank)) for u, g in rows]
        scored.sort(key=lambda x: (-x[2], x[0].stock_code))
        page = scored[offset : offset + limit]
        items = [_to_row(u, g, score) for u, g, score in page]
    else:
        db_sort = {
            "market_cap": U.market_cap.asc(),
            "momentum": U.momentum_3m.desc().nulls_last(),
            "rev_yoy": G.revenue_yoy.desc().nulls_last(),
            "trading_value": U.trading_value.desc().nulls_last(),
            "change": U.change_pct.desc().nulls_last(),
        }.get(sort, U.market_cap.asc())
        rows = db.execute(
            base.order_by(db_sort, U.stock_code).limit(limit).offset(offset)
        ).all()
        items = [_to_row(u, g, None) for u, g in rows]

    return ScreenerResult(as_of=as_of, total=total, items=items)


def _to_row(u, g, score: float | None) -> ScreenerRow:
    return ScreenerRow(
        stock_code=u.stock_code,
        stock_name=u.stock_name,
        market=u.market,
        close_price=u.close_price,
        change_pct=u.change_pct,
        market_cap=u.market_cap,
        trading_value=u.trading_value,
        momentum_3m=u.momentum_3m,
        revenue_yoy=g.revenue_yoy if g else None,
        op_yoy=g.op_yoy if g else None,
        op_turnaround=bool(g.op_turnaround) if g else False,
        growth_score=score,
    )
