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


_MIN_CELL = 20  # 섹터·성장구간 셀 최소 표본(회귀 신뢰). 미달 셀은 저장 안 함 → 상위 레벨 폴백.


def _growth_bucket(cagr_pct: float, lo: float, hi: float) -> str:
    """실현 CAGR(%) → 성장구간(low|mid|high). 경계는 분포 삼분위(lo=33%, hi=67%)로 유도(임의 상수 아님)."""
    if cagr_pct < lo:
        return "low"
    return "high" if cagr_pct >= hi else "mid"


def ingest_market_peg(db: Session, today=None) -> dict:
    """전 종목 (PER, 실현 CAGR%, 섹터) → 전체·섹터별·성장구간별 PEG 를 MarketFactor 로 upsert.

    계층: market_peg(전체) / market_peg:sector:<섹터> / market_peg:growth:<구간>. 각 셀 표본<20이면
    미저장(조회 시 상위 레벨로 폴백). 성장구간 경계는 CAGR 분포 삼분위로 데이터에서 유도."""
    from datetime import date

    from app.config import get_settings
    from app.services.deepdive.tools import ToolContext, sector_for

    per_map = _latest_per_by_code(db)
    eps_map = _eps_series_by_code(db)
    settings = get_settings()
    triples: list[tuple[float, float, str | None]] = []  # (per, cagr%, sector)
    for code, per in per_map.items():
        eps_series = eps_map.get(code)
        if not eps_series:
            continue
        cagr = fwd._cagr(fwd.ttm_windows(eps_series))
        if cagr is None or cagr <= 0:
            continue
        ctx = ToolContext(db=db, code=code, settings=settings, session=None)
        triples.append((per, cagr * 100.0, sector_for(ctx)))
    if len(triples) < _MIN_CELL:
        return {"inserted": 0, "skipped": "insufficient_sample", "pairs": len(triples)}

    # 성장구간 경계 = CAGR 분포 삼분위(데이터 유도).
    cagrs = sorted(g for _, g, _ in triples)
    lo, hi = cagrs[len(cagrs) // 3], cagrs[len(cagrs) * 2 // 3]

    cells: dict[str, list[tuple[float, float]]] = defaultdict(list)  # factor 키 → (per,g) 쌍
    for per, g, sector in triples:
        cells[_FACTOR].append((per, g))  # 전체
        if sector:
            cells[f"{_FACTOR}:sector:{sector}"].append((per, g))
        cells[f"{_FACTOR}:growth:{_growth_bucket(g, lo, hi)}"].append((per, g))

    as_of = today or date.today()
    saved: dict[str, float] = {}
    # 성장구간 경계도 저장(조회 시 종목 CAGR 을 같은 경계로 버킷팅해야 정합).
    for factor, val_ in ((f"{_FACTOR}:bound:low", lo), (f"{_FACTOR}:bound:high", hi)):
        cells.pop(factor, None)
        stmt = insert(MarketFactor).values(factor=factor, as_of_date=as_of, value=round(val_, 4))
        stmt = stmt.on_conflict_do_update(constraint="uq_market_factor", set_={"value": round(val_, 4)})
        db.execute(stmt)
    for factor, pairs in cells.items():
        peg = fwd.market_peg(pairs)  # 내부에서 표본<20·IQR 처리
        if peg is None:
            continue
        stmt = insert(MarketFactor).values(factor=factor, as_of_date=as_of, value=peg)
        stmt = stmt.on_conflict_do_update(constraint="uq_market_factor", set_={"value": peg})
        db.execute(stmt)
        saved[factor] = peg
    db.commit()
    return {"inserted": len(saved), "cells": saved,
            "growth_bounds": {"low<": round(lo, 1), "high>=": round(hi, 1)}, "pairs": len(triples)}


def latest_market_factor(db: Session, factor: str) -> float | None:
    """최신 시장 팩터 값(factor 별). 없으면 None(호출측이 결측 처리 — 상수 폴백 없음)."""
    row = db.scalars(
        select(MarketFactor)
        .where(MarketFactor.factor == factor)
        .order_by(desc(MarketFactor.as_of_date))
        .limit(1)
    ).first()
    return row.value if row else None


def market_peg_for(db: Session, sector: str | None, cagr_pct: float | None) -> tuple[float | None, str]:
    """계층적 PEG: 섹터(표본충분) → 성장구간 → 전체 순 폴백. (PEG, source) 반환. 다 없으면 (None, '').

    상수 아님 — 실측 PEG 의 세분/폴백 계층. 성장구간은 배치가 저장한 경계로 종목 CAGR 을 버킷팅.
    """
    if sector:
        v = latest_market_factor(db, f"{_FACTOR}:sector:{sector}")
        if v is not None:
            return v, f"sector:{sector}"
    if cagr_pct is not None:
        lo = latest_market_factor(db, f"{_FACTOR}:bound:low")
        hi = latest_market_factor(db, f"{_FACTOR}:bound:high")
        if lo is not None and hi is not None:
            bucket = _growth_bucket(cagr_pct, lo, hi)
            v = latest_market_factor(db, f"{_FACTOR}:growth:{bucket}")
            if v is not None:
                return v, f"growth:{bucket}"
    v = latest_market_factor(db, _FACTOR)
    return (v, "market") if v is not None else (None, "")


def latest_market_peg(db: Session) -> float | None:
    """최신 전체 시장 PEG(편의 래퍼, 폴백 기본값)."""
    return latest_market_factor(db, _FACTOR)
