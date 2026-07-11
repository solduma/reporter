"""US 종목 라우터 — SEC EDGAR 재무 + 네이버 시세. 개별종목 상세페이지용.

데이터 접근·계산은 us_company_service 가 담당하고 여기선 쿼리 파라미터→DTO 매핑만 한다.
차트는 기존 /api/chart?market=US 를 재사용(응답의 naver_symbol 사용).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import UsDisclosureOut, UsFinancialOut, UsQuoteOut, UsScreenerResult
from app.services import us_company_service, us_disclosure_ingest, us_screener_service

router = APIRouter(prefix="/api/us/companies", tags=["us"])
screener_router = APIRouter(prefix="/api/us/screener", tags=["us"])


@screener_router.get("", response_model=UsScreenerResult)
def us_screen(
    mktcap_min: float | None = Query(default=None, description="시총 하한(USD)"),
    mktcap_max: float | None = Query(default=None, description="시총 상한(USD)"),
    liq_min: float | None = Query(default=None, description="거래대금 최소(USD)"),
    per_max: float | None = Query(default=None, description="PER 상한"),
    pbr_max: float | None = Query(default=None, description="PBR 상한"),
    mom_min: float | None = Query(default=None, description="3개월 모멘텀 최소%"),
    exchange: str | None = Query(default=None, pattern="^(NASDAQ|NYSE)$"),
    sector: str | None = Query(default=None),
    has_event: bool = Query(default=False, description="최근 14일 8-K 있는 종목만"),
    sort: str = Query(default="score", description="score|market_cap|momentum|per|trading_value|change"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> UsScreenerResult:
    """US 스크리너 — S&P500(+보충) 유니버스 필터·저평가·모멘텀 랭킹."""
    return us_screener_service.screen(
        db, mktcap_min=mktcap_min, mktcap_max=mktcap_max, liq_min=liq_min,
        per_max=per_max, pbr_max=pbr_max, mom_min=mom_min, exchange=exchange,
        sector=sector, has_event=has_event, sort=sort, limit=limit, offset=offset,
    )


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


@router.get("/{ticker}/disclosures", response_model=list[UsDisclosureOut])
def us_disclosures(
    ticker: str = Path(..., pattern=r"^[A-Za-z.\-]{1,10}$"),
    db: Session = Depends(get_session),
) -> list[UsDisclosureOut]:
    """US 종목 최근 SEC 8-K 공시(야간 배치 수집분). 상세 타임라인용."""
    return [
        UsDisclosureOut(
            accession=d.accession, form_type=d.form_type, filing_date=d.filing_date,
            title=d.title, primary_doc_url=d.primary_doc_url,
            sentiment=d.sentiment.value if d.sentiment else None,
        )
        for d in us_disclosure_ingest.recent_disclosures(db, ticker)
    ]
