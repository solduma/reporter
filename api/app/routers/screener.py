"""종목 스크리너 라우터 — 종합·성장·가치·추세·탑다운 5전략.

쿼리·전략 엔진은 services/screener_service 에 있고, 여기선 쿼리 파라미터를 받아 위임한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import schemas
from app.db.session import get_session
from app.schemas import ScreenerResult
from app.services import screener_service

router = APIRouter(prefix="/api/screener", tags=["screener"])


@router.get("", response_model=ScreenerResult)
def screen(
    strategy: str = Query(default="overall", pattern="^(overall|growth|value|trend|topdown)$"),
    mktcap_max: int | None = Query(default=None, description="시총 상한(원). None=전체"),
    mktcap_min: int | None = Query(default=None, description="시총 하한(원)"),
    liq_min: int | None = Query(default=100_000_000, description="거래대금 최소(원). 기본 1억"),
    # 성장 전략 필터
    rev_yoy_min: float | None = Query(default=None, description="매출 YoY 최소(0.15=+15%)"),
    op_growth: str | None = Query(default=None, pattern="^(turnaround|growth)$"),
    mom_min: float | None = Query(default=None, description="3개월 모멘텀 최소%"),
    mom_max: float | None = Query(default=None, description="3개월 모멘텀 최대%(과열 컷)"),
    # 가치 전략 필터
    per_max: float | None = Query(default=None, description="PER 상한"),
    pbr_max: float | None = Query(default=None, description="PBR 상한"),
    roe_min: float | None = Query(default=None, description="ROE 하한(%)"),
    div_min: float | None = Query(default=None, description="시가배당률 하한(%)"),
    # 공통
    market: str | None = Query(default=None, pattern="^(KOSPI|KOSDAQ)$"),
    sector: str | None = Query(default=None, description="섹터명(judal 테마 매칭 종목만)"),
    include_etf: bool = Query(default=False, description="ETF/ETN 포함(기본 제외)"),
    coverage: str | None = Query(default=None, pattern="^(has|none)$", description="리포트 커버리지 유무"),
    recent_buy: bool = Query(default=False, description="최근 90일 BUY 리포트 있는 종목만"),
    sort: str = Query(default="score", description="score|market_cap|momentum|rev_yoy|trading_value|change|coverage"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> ScreenerResult:
    return screener_service.screen(
        db,
        strategy=strategy,
        mktcap_max=mktcap_max,
        mktcap_min=mktcap_min,
        liq_min=liq_min,
        rev_yoy_min=rev_yoy_min,
        op_growth=op_growth,
        mom_min=mom_min,
        mom_max=mom_max,
        per_max=per_max,
        pbr_max=pbr_max,
        roe_min=roe_min,
        div_min=div_min,
        market=market,
        sector=sector,
        include_etf=include_etf,
        coverage=coverage,
        recent_buy=recent_buy,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/filters", response_model=list[schemas.ScreenerFilterMeta])
def screener_filters() -> list[schemas.ScreenerFilterMeta]:
    """스크리너 필터 메타데이터 — 온톨로지 정준 ID 기준 라벨·설명 단일 출처(D1)."""
    return screener_service.filter_meta()


@router.get("/sectors", response_model=list[str])
def screener_sectors() -> list[str]:
    """섹터 필터용 섹터명 목록(국내 섹터 ETF 기준)."""
    from reporter import sector_etf

    return [e.sector for e in sector_etf.KR_SECTOR_ETFS]
