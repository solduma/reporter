"""보유종목(포트폴리오) 라우터 — 단일 사용자 상태 CRUD.

라우터는 검증·위임만: portfolio 서비스가 데이터 접근·DTO 조립(종목명 해석 포함)을 소유한다.
비밀번호 게이트(web middleware) 뒤라 별도 인증 없음(단일 사용자 전제).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas import HoldingIn, HoldingOut, PortfolioView
from app.services import portfolio

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioView)
def get_portfolio(db: Session = Depends(get_session)) -> PortfolioView:
    """보유목록 + 요약(총손익·손절현황) + 섹터분산. 손익은 최근 종가 기준."""
    return portfolio.portfolio_view(db)


@router.get("/holdings", response_model=list[HoldingOut])
def list_holdings(db: Session = Depends(get_session)) -> list[HoldingOut]:
    return portfolio.list_holdings(db)


@router.put("/holdings/{code}", response_model=HoldingOut)
def put_holding(code: str, body: HoldingIn, db: Session = Depends(get_session)) -> HoldingOut:
    if body.shares <= 0 or body.avg_cost <= 0:
        raise HTTPException(status_code=422, detail="수량·평단은 0보다 커야 합니다")
    return portfolio.save_holding(
        db,
        portfolio.HoldingInput(
            stock_code=code,
            shares=body.shares,
            avg_cost=body.avg_cost,
            stop_loss=body.stop_loss,
            note=body.note,
        ),
    )


@router.delete("/holdings/{code}")
def delete_holding(code: str, db: Session = Depends(get_session)) -> dict:
    if not portfolio.delete_holding(db, code):
        raise HTTPException(status_code=404, detail="보유종목이 없습니다")
    return {"deleted": code}
