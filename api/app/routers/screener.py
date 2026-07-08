"""스몰캡 성장 스크리너 — 유니버스 스냅샷에 시총·모멘텀·유동성 필터.

1단계: universe_snapshot 만으로 시총 상한·3개월 모멘텀·거래대금 필터 + 정렬.
성장지표(YoY)·센티먼트는 후속 단계에서 조인 추가.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import UniverseSnapshot
from app.db.session import get_session
from app.schemas import ScreenerResult, ScreenerRow

router = APIRouter(prefix="/api/screener", tags=["screener"])

_SORTS = {
    "market_cap": UniverseSnapshot.market_cap.asc(),  # 작은 스몰캡 우선
    "momentum": UniverseSnapshot.three_month_rate.desc().nulls_last(),
    "trading_value": UniverseSnapshot.trading_value.desc().nulls_last(),
    "change": UniverseSnapshot.change_pct.desc().nulls_last(),
}


def _latest_date(db: Session) -> date | None:
    return db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))


@router.get("", response_model=ScreenerResult)
def screen(
    mktcap_max: int | None = Query(default=500_000_000_000, description="시총 상한(원). 기본 5천억"),
    mktcap_min: int | None = Query(default=None, description="시총 하한(원)"),
    mom_min: float | None = Query(default=None, description="3개월 수익률 최소 %"),
    mom_max: float | None = Query(default=None, description="3개월 수익률 최대 %(과열 컷)"),
    liq_min: int | None = Query(default=100_000_000, description="거래대금 최소(원). 기본 1억(초저유동 제외)"),
    market: str | None = Query(default=None, pattern="^(KOSPI|KOSDAQ)$"),
    include_etf: bool = Query(default=False, description="ETF/ETN 포함 여부(기본 제외)"),
    sort: str = Query(default="market_cap"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> ScreenerResult:
    as_of = _latest_date(db)
    if not as_of:
        return ScreenerResult(as_of=None, total=0, items=[])

    conds = [UniverseSnapshot.snapshot_date == as_of]
    if mktcap_max is not None:
        conds.append(UniverseSnapshot.market_cap <= mktcap_max)
    if mktcap_min is not None:
        conds.append(UniverseSnapshot.market_cap >= mktcap_min)
    if mom_min is not None:
        conds.append(UniverseSnapshot.three_month_rate >= mom_min)
    if mom_max is not None:
        conds.append(UniverseSnapshot.three_month_rate <= mom_max)
    if liq_min is not None:
        conds.append(UniverseSnapshot.trading_value >= liq_min)
    if market:
        conds.append(UniverseSnapshot.market == market)
    if not include_etf:  # 성장주 스크리닝은 일반 주식만 (ETF/ETN 제외)
        conds.append(UniverseSnapshot.stock_type == "stock")
        # 우선주(이름이 '우'·'우B'·'우C' 로 끝남)는 성장 발굴 대상이 아니라 제외.
        conds.append(~UniverseSnapshot.stock_name.op("~")(r"우[A-C]?$"))
    # 시총 0/결측은 스몰캡 판정 불가라 제외
    conds.append(UniverseSnapshot.market_cap.is_not(None))
    # 거래대금 0(사실상 거래정지·초저유동)은 성장주 발굴 대상이 아니라 기본 제외.
    conds.append(UniverseSnapshot.trading_value > 0)

    total = db.scalar(select(func.count()).select_from(UniverseSnapshot).where(*conds)) or 0
    order = _SORTS.get(sort, _SORTS["market_cap"])
    # 2차 키(stock_code)로 동률 시 페이지네이션 순서를 결정적으로 고정.
    rows = db.scalars(
        select(UniverseSnapshot)
        .where(*conds)
        .order_by(order, UniverseSnapshot.stock_code)
        .limit(limit)
        .offset(offset)
    ).all()

    return ScreenerResult(
        as_of=as_of,
        total=total,
        items=[
            ScreenerRow(
                stock_code=r.stock_code,
                stock_name=r.stock_name,
                market=r.market,
                close_price=r.close_price,
                change_pct=r.change_pct,
                market_cap=r.market_cap,
                trading_value=r.trading_value,
                three_month_rate=r.three_month_rate,
            )
            for r in rows
        ],
    )
