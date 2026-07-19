"""시장 PEG 수집 — 전 종목 (현재 PER, 10년 실현 EPS CAGR) 횡단면으로 시장 PEG 를 MarketFactor 로 upsert.

fair_per 의 PEG(1.5 상수)를 시장 실측으로 대체. 성장률은 forward 추정(convex 외삽)이 아니라 **과거
실현 EPS CAGR**(시작→끝 연복리, 추정 편향 없음)을 쓴다 — forward g 는 중앙 40%로 과대해 PEG 를
오염시켰다. 시장이 '실제로 달성한 장기성장'에 매긴 배수를 실측. 표본 부족 시 skip(상수 폴백). DB 만 사용.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Financial, MarketFactor
from app.domain import forward as fwd

logger = logging.getLogger(__name__)

_FACTOR = "market_peg"


def _latest_per_by_code(db: Session) -> dict[str, float]:
    """종목별 최신 양수 PER(financials). 기간 문자열 내림차순으로 최신 1건."""
    rows = db.execute(
        select(Financial.stock_code, Financial.per, Financial.period)
        .where(Financial.per.is_not(None), Financial.per > 0, Financial.is_estimate.is_(False))
        .order_by(Financial.stock_code, desc(Financial.period))
    ).all()
    out: dict[str, float] = {}
    for code, per, _period in rows:
        if code not in out:  # 첫(최신) 것만
            out[code] = per
    return out


def _eps_series_by_code(db: Session) -> dict[str, list[float]]:
    """종목별 EPS 분기 시계열(기간 오름차순, 실적만). long_term_growth 의 TTM 창 입력용."""
    rows = db.execute(
        select(Financial.stock_code, Financial.period, Financial.eps)
        .where(Financial.eps.is_not(None), Financial.is_estimate.is_(False))
        .order_by(Financial.stock_code, Financial.period)
    ).all()
    out: dict[str, list[float]] = defaultdict(list)
    for code, _period, eps in rows:
        out[code].append(eps)
    return out


def ingest_market_peg(db: Session, today=None) -> dict:
    """전 종목 (PER, 장기성장률%) 쌍 → 시장 PEG 회귀 산출 → MarketFactor upsert."""
    per_map = _latest_per_by_code(db)
    eps_map = _eps_series_by_code(db)
    pairs: list[tuple[float, float]] = []
    for code, per in per_map.items():
        eps_series = eps_map.get(code)
        if not eps_series:
            continue
        # 실현 CAGR: TTM 시계열 시작→끝 연복리(추정 아님, forward 편향 없음).
        cagr = fwd._cagr(fwd.ttm_windows(eps_series))
        if cagr is not None and cagr > 0:
            pairs.append((per, cagr * 100.0))  # % 로 변환해 PER 스케일과 맞춤
    peg = fwd.market_peg(pairs)
    if peg is None:
        return {"inserted": 0, "skipped": "insufficient_sample", "pairs": len(pairs)}
    from datetime import date

    as_of = today or date.today()
    stmt = insert(MarketFactor).values(factor=_FACTOR, as_of_date=as_of, value=peg)
    stmt = stmt.on_conflict_do_update(constraint="uq_market_factor", set_={"value": peg})
    db.execute(stmt)
    db.commit()
    return {"inserted": 1, "market_peg": peg, "pairs": len(pairs)}


def latest_market_peg(db: Session) -> float | None:
    """최신 시장 PEG. 없으면 None(호출측이 상수 폴백)."""
    row = db.scalars(
        select(MarketFactor)
        .where(MarketFactor.factor == _FACTOR)
        .order_by(desc(MarketFactor.as_of_date))
        .limit(1)
    ).first()
    return row.value if row else None
