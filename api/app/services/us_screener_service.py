"""US 스크리너 조회 서비스 — us_universe 스냅샷 기반 필터·스코어. KR screener_service 대응.

KR 처럼 전략 3분기(성장·가치·이벤트)를 두지 않고, 유니버스 필드(시총·거래대금·PER/PBR·모멘텀)
필터 + 저평가·모멘텀 종합 스코어(domain.scoring.us_screen_score) 로 랭킹한다. 이벤트(8-K)
필터는 us_disclosure 최근 유무로 판단(4.3 연동).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import UsDisclosure, UsUniverse
from app.domain import scoring
from app.schemas import UsScreenerResult, UsScreenerRow

_EVENT_DAYS = 14  # 이벤트 스크리너: 최근 N일 내 8-K 있는 종목


def _latest_date(db: Session) -> date | None:
    return db.scalar(select(func.max(UsUniverse.snapshot_date)))


def _near_high_pct(u: UsUniverse) -> float | None:
    """종가/52주고가 (%) — 신고가 근접도. 데이터 없으면 None."""
    if u.close_price and u.high_52w:
        return round(u.close_price / u.high_52w * 100, 1)
    return None


def _tickers_with_recent_8k(db: Session, since: date) -> set[str]:
    """최근 since 이후 8-K 가 있는 ticker 집합(이벤트 스크리너·표시용)."""
    return set(
        db.scalars(
            select(UsDisclosure.ticker).where(UsDisclosure.filing_date >= since).distinct()
        ).all()
    )


def screen(
    db: Session,
    *,
    mktcap_min: float | None = None,
    mktcap_max: float | None = None,
    liq_min: float | None = None,
    per_max: float | None = None,
    pbr_max: float | None = None,
    mom_min: float | None = None,
    exchange: str | None = None,
    sector: str | None = None,
    has_event: bool = False,
    sort: str = "score",
    limit: int = 50,
    offset: int = 0,
) -> UsScreenerResult:
    """US 유니버스 필터·정렬. sort=score|market_cap|momentum|per|trading_value|change."""
    as_of = _latest_date(db)
    if not as_of:
        return UsScreenerResult(as_of=None, total=0, items=[])

    U = UsUniverse
    conds = [U.snapshot_date == as_of, U.market_cap.is_not(None)]
    if mktcap_min is not None:
        conds.append(U.market_cap >= mktcap_min)
    if mktcap_max is not None:
        conds.append(U.market_cap <= mktcap_max)
    if liq_min is not None:
        conds.append(U.trading_value >= liq_min)
    if per_max is not None:
        conds.append((U.per.is_not(None)) & (U.per > 0) & (U.per <= per_max))
    if pbr_max is not None:
        conds.append((U.pbr.is_not(None)) & (U.pbr > 0) & (U.pbr <= pbr_max))
    if mom_min is not None:
        conds.append(U.momentum_3m >= mom_min)
    if exchange:
        conds.append(U.exchange == exchange)
    if sector:
        conds.append(U.sector == sector)

    rows = list(db.scalars(select(U).where(*conds)).all())

    event_since = datetime.now(UTC).date() - timedelta(days=_EVENT_DAYS)
    event_tickers = _tickers_with_recent_8k(db, event_since)
    if has_event:
        rows = [r for r in rows if r.ticker in event_tickers]
    total = len(rows)

    if sort == "score" or sort not in ("market_cap", "momentum", "per", "trading_value", "change"):
        per_rank = scoring.cheap_ranker([r.per for r in rows])
        pbr_rank = scoring.cheap_ranker([r.pbr for r in rows])
        mom_rank = scoring.percentile_ranker([r.momentum_3m for r in rows])
        scored = [
            (
                r,
                scoring.us_screen_score(
                    per=r.per, pbr=r.pbr, momentum_3m=r.momentum_3m,
                    near_high_pct=_near_high_pct(r),
                    per_rank=per_rank, pbr_rank=pbr_rank, mom_rank=mom_rank,
                ),
            )
            for r in rows
        ]
        scored.sort(key=lambda x: (-x[1], x[0].ticker))
        page = scored[offset : offset + limit]
        items = [_to_row(r, event_tickers, score=sc) for r, sc in page]
    else:
        # None 은 맨 뒤로 보내되, 0.0(보합·무모멘텀)은 정상값으로 정렬해야 한다
        # (`x or -1e9` 는 0.0 도 falsy 라 None 과 같게 최하위로 밀리는 버그).
        key = {
            "market_cap": lambda r: -(r.market_cap or 0),
            "momentum": lambda r: -(r.momentum_3m if r.momentum_3m is not None else -1e9),
            "per": lambda r: (r.per if (r.per and r.per > 0) else 1e9),  # 저PER 먼저
            "trading_value": lambda r: -(r.trading_value or 0),
            "change": lambda r: -(r.change_pct if r.change_pct is not None else -1e9),
        }[sort]
        rows.sort(key=lambda r: (key(r), r.ticker))
        page = rows[offset : offset + limit]
        items = [_to_row(r, event_tickers, score=None) for r in page]

    return UsScreenerResult(as_of=as_of.isoformat(), total=total, items=items)


def _to_row(u: UsUniverse, event_tickers: set[str], *, score: float | None) -> UsScreenerRow:
    return UsScreenerRow(
        ticker=u.ticker,
        name=u.name,
        exchange=u.exchange,
        sector=u.sector,
        close_price=u.close_price,
        change_pct=u.change_pct,
        market_cap=u.market_cap,
        trading_value=u.trading_value,
        per=u.per,
        pbr=u.pbr,
        eps=u.eps,
        momentum_3m=u.momentum_3m,
        near_high_pct=_near_high_pct(u),
        has_recent_8k=u.ticker in event_tickers,
        score=score,
    )
