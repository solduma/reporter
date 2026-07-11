"""US 종목 라우터 — SEC EDGAR 재무 + 네이버 시세. 개별종목 상세페이지용.

데이터 접근·계산은 us_company_service 가 담당하고 여기선 쿼리 파라미터→DTO 매핑만 한다.
차트는 기존 /api/chart?market=US 를 재사용(응답의 naver_symbol 사용).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import UsFinancialOut, UsQuoteOut
from app.services import us_company_service

router = APIRouter(prefix="/api/us/companies", tags=["us"])


@router.get("/{ticker}/quote", response_model=UsQuoteOut)
def us_quote(ticker: str = Path(..., pattern=r"^[A-Za-z.\-]{1,10}$")) -> UsQuoteOut:
    """US 티커 현재 시세 + 네이버 차트 심볼. 없으면 404."""
    q = us_company_service.quote(ticker)
    if q is None:
        raise HTTPException(status_code=404, detail="US 종목 시세 없음")
    return UsQuoteOut(
        ticker=q.ticker, naver_symbol=q.naver_symbol, name=q.name,
        close=q.close, change_ratio=q.change_ratio, rising=q.rising,
    )


@router.get("/{ticker}/financials", response_model=UsFinancialOut)
def us_financials(
    ticker: str = Path(..., pattern=r"^[A-Za-z.\-]{1,10}$"),
    db: Session = Depends(get_session),
) -> UsFinancialOut:
    """US 종목 재무 지표(PER/PBR/ROE/PSR) — DB 우선, TTL 만료 시 SEC 재계산. 미등록이면 404."""
    row = us_company_service.get_financials(db, ticker)
    if row is None:
        raise HTTPException(status_code=404, detail="US 종목 재무 없음(SEC 미등록)")
    return UsFinancialOut(
        ticker=row.ticker, name=row.name,
        ttm_revenue=row.ttm_revenue, ttm_net_income=row.ttm_net_income,
        ttm_operating_income=row.ttm_operating_income, ttm_eps=row.ttm_eps,
        equity=row.equity, shares=row.shares, market_cap=row.market_cap,
        per=row.per, pbr=row.pbr, psr=row.psr, roe=row.roe,
    )
