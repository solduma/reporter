"""종목 스크리너 조회 서비스 — 성장·가치·이벤트드리븐 3전략(읽기 전용 쿼리 엔진).

공통 유니버스(시총/유동성/시장/섹터) 위에 전략을 얹는다:
- growth: 매출/영업이익 YoY·모멘텀·흑자전환 (GrowthMetric). 필터 통과 집합 내 백분위 스코어.
- value: 저PER·저PBR / 고ROE·저PBR / 저EV-EBITDA (Financial 최신 분기). 저평가 백분위 스코어.
- event: 최근 공시·리포트·급등락·브리핑·뉴스 이벤트. 최신·강도 스코어.

라우터는 이 서비스의 screen() 에 쿼리 파라미터만 넘긴다. 스코어링 규칙은 domain.scoring,
결과는 ScreenerResult 읽기모델(read-model)로 반환한다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Broadcast,
    Disclosure,
    Financial,
    GrowthMetric,
    Report,
    ReportAnalysis,
    Sentiment,
    StockEvent,
    UniverseSnapshot,
)
from app.domain import scoring
from app.schemas import ScreenerResult, ScreenerRow
from app.services import sector_ingest, universe_ingest

_COVERAGE_DAYS = 90
_EVENT_DAYS = 14  # 이벤트 전략: 최근 N일 내 이벤트만

_EVENT_KIND_LABEL = {
    "disclosure": "공시", "report": "리포트", "surge": "급등락", "broadcast": "브리핑", "news": "뉴스",
}


def _latest_date(db: Session) -> date | None:
    return universe_ingest.latest_snapshot_date(db)


def _coverage_subquery(since: date):
    """종목별 최근 커버리지 집계: 리포트 수, BUY 수. since 이후 발행분."""
    return (
        select(
            Report.stock_code.label("stock_code"),
            func.count(Report.id).label("coverage_count"),
            func.sum(case((ReportAnalysis.sentiment == Sentiment.BUY, 1), else_=0)).label("buy_count"),
        )
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(Report.stock_code.is_not(None), Report.published_date >= since)
        .group_by(Report.stock_code)
        .subquery()
    )


def _growth_score(u, g, cov_count, buy_count, rev_rank, op_rank, mom_rank) -> float:
    """성장스코어 — 도메인 규칙(scoring.growth_score)에 ORM 행에서 뽑은 값을 넘긴다."""
    return scoring.growth_score(
        revenue_yoy=g.revenue_yoy if g else None,
        op_yoy=g.op_yoy if g else None,
        momentum_3m=u.momentum_3m,
        op_turnaround=bool(g and g.op_turnaround),
        coverage_count=cov_count,
        buy_count=buy_count,
        rev_rank=rev_rank,
        op_rank=op_rank,
        mom_rank=mom_rank,
    )


def _value_score(fin, per_rank, pbr_rank, ev_rank) -> float:
    """가치스코어 — 도메인 규칙(scoring.value_score)에 ORM 행에서 뽑은 값을 넘긴다."""
    if fin is None:
        return 0.0
    return scoring.value_score(
        per=fin.per,
        pbr=fin.pbr,
        ev_ebitda=fin.ev_ebitda,
        roe=fin.roe,
        div_yield=fin.div_yield,
        per_rank=per_rank,
        pbr_rank=pbr_rank,
        ev_rank=ev_rank,
    )


def screen(
    db: Session,
    *,
    strategy: str,
    mktcap_max: int | None,
    mktcap_min: int | None,
    liq_min: int | None,
    rev_yoy_min: float | None,
    op_growth: str | None,
    mom_min: float | None,
    mom_max: float | None,
    per_max: float | None,
    pbr_max: float | None,
    roe_min: float | None,
    div_min: float | None,
    event_kind: str | None,
    market: str | None,
    sector: str | None,
    include_etf: bool,
    coverage: str | None,
    recent_buy: bool,
    sort: str,
    limit: int,
    offset: int,
) -> ScreenerResult:
    """공통 유니버스 필터 + 전략 디스패치. 결과 ScreenerResult(read-model)."""
    as_of = _latest_date(db)
    if not as_of:
        return ScreenerResult(as_of=None, total=0, items=[])

    U, G = UniverseSnapshot, GrowthMetric
    cov = _coverage_subquery(datetime.now().date() - timedelta(days=_COVERAGE_DAYS))
    cov_count = func.coalesce(cov.c.coverage_count, 0)
    buy_count = func.coalesce(cov.c.buy_count, 0)

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
    if sector:
        codes = sector_ingest.sector_stock_codes(db, sector)
        if not codes:
            return ScreenerResult(as_of=as_of, total=0, items=[])
        conds.append(U.stock_code.in_(codes))
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
    if coverage == "has":
        conds.append(cov.c.coverage_count > 0)
    elif coverage == "none":
        conds.append(cov.c.coverage_count.is_(None))
    if recent_buy:
        conds.append(cov.c.buy_count > 0)

    base = (
        select(U, G, cov_count.label("cov_n"), buy_count.label("buy_n"))
        .outerjoin(G, G.stock_code == U.stock_code)
        .outerjoin(cov, cov.c.stock_code == U.stock_code)
        .where(*conds)
    )

    if strategy == "value":
        return _screen_value(db, base, as_of, per_max, pbr_max, roe_min, div_min, sort, limit, offset)
    if strategy == "event":
        return _screen_event(db, base, as_of, event_kind, sort, limit, offset)
    return _screen_growth(db, base, as_of, sort, limit, offset)


# ── 성장 전략 ──────────────────────────────────────────────────────────
def _screen_growth(db, base, as_of, sort, limit, offset) -> ScreenerResult:
    U, G = UniverseSnapshot, GrowthMetric
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    if sort == "score":
        rows = list(db.execute(base).all())
        rev_rank = scoring.percentile_ranker([r[1].revenue_yoy for r in rows if r[1]])
        op_rank = scoring.percentile_ranker([r[1].op_yoy for r in rows if r[1]])
        mom_rank = scoring.percentile_ranker([r[0].momentum_3m for r in rows])
        scored = [
            (r, _growth_score(r[0], r[1], r[2], r[3], rev_rank, op_rank, mom_rank)) for r in rows
        ]
        scored.sort(key=lambda x: (-x[1], x[0][0].stock_code))
        page = scored[offset : offset + limit]
        items = [_to_row(r[0], r[1], r[2], r[3], score=score) for r, score in page]
    else:
        db_sort = {
            "market_cap": U.market_cap.asc(),
            "momentum": U.momentum_3m.desc().nulls_last(),
            "rev_yoy": G.revenue_yoy.desc().nulls_last(),
            "trading_value": U.trading_value.desc().nulls_last(),
            "change": U.change_pct.desc().nulls_last(),
            "coverage": func.coalesce(base.selected_columns.cov_n, 0).desc(),
        }.get(sort, U.market_cap.asc())
        rows = db.execute(base.order_by(db_sort, U.stock_code).limit(limit).offset(offset)).all()
        items = [_to_row(r[0], r[1], r[2], r[3], score=None) for r in rows]
    return ScreenerResult(as_of=as_of, total=total, items=items)


# ── 가치 전략 ──────────────────────────────────────────────────────────
def _latest_financials(db, codes: list[str]) -> dict[str, Financial]:
    """종목별, 밸류 지표(per/pbr)가 있는 최신 비추정 분기 Financial.

    DISTINCT ON (stock_code) 로 종목당 1행만 DB 에서 뽑는다. per/pbr 있는 행 먼저 → period 최신
    정렬로, ev_ebitda 만 있는 반쪽 연간행이 최신으로 잡혀 종목이 통째로 누락되는 것을 막는다.
    """
    if not codes:
        return {}
    has_value = case((or_(Financial.per.is_not(None), Financial.pbr.is_not(None)), 0), else_=1)
    rows = db.scalars(
        select(Financial)
        .where(Financial.stock_code.in_(codes), Financial.is_estimate.is_(False))
        .distinct(Financial.stock_code)
        .order_by(Financial.stock_code, has_value.asc(), Financial.period.desc())
    ).all()
    return {r.stock_code: r for r in rows}


def _latest_dividends(db, codes: list[str]) -> dict[str, float]:
    """종목별 시가배당률 — 결산분기(.12)의 최신값. {code: div_yield}.

    배당은 연간 지표라 결산분기(12월)에만 값이 있어 period 가 '.12'인 행만 최신순으로 잡는다.
    """
    if not codes:
        return {}
    rows = db.execute(
        select(Financial.stock_code, Financial.div_yield)
        .where(
            Financial.stock_code.in_(codes),
            Financial.is_estimate.is_(False),
            Financial.div_yield.is_not(None),
            Financial.period.like("%.12"),  # 결산분기(연간 배당)만
        )
        .distinct(Financial.stock_code)
        .order_by(Financial.stock_code, Financial.period.desc())
    ).all()
    return dict(rows)


def _screen_value(
    db, base, as_of, per_max, pbr_max, roe_min, div_min, sort, limit, offset
) -> ScreenerResult:
    rows = list(db.execute(base).all())
    codes = [r[0].stock_code for r in rows]
    fin_map = _latest_financials(db, codes)
    div_map = _latest_dividends(db, codes)
    for c, fin in fin_map.items():
        dy = div_map.get(c)
        if dy is not None:
            fin.div_yield = dy  # 세션 객체 in-memory 보정(커밋 안 함, 읽기 전용)

    def _passes(fin: Financial | None) -> bool:
        if fin is None or (fin.per is None and fin.pbr is None and fin.div_yield is None):
            return False
        if per_max is not None and not (fin.per is not None and 0 < fin.per <= per_max):
            return False
        if pbr_max is not None and not (fin.pbr is not None and 0 < fin.pbr <= pbr_max):
            return False
        if roe_min is not None and not (fin.roe is not None and fin.roe >= roe_min):
            return False
        return not (div_min is not None and not (fin.div_yield is not None and fin.div_yield >= div_min))

    kept = [(r, fin_map.get(r[0].stock_code)) for r in rows]
    kept = [(r, f) for r, f in kept if _passes(f)]
    total = len(kept)

    if sort == "score" or sort not in ("market_cap", "change", "trading_value"):
        per_rank = scoring.cheap_ranker([f.per for _, f in kept])
        pbr_rank = scoring.cheap_ranker([f.pbr for _, f in kept])
        ev_rank = scoring.cheap_ranker([f.ev_ebitda for _, f in kept])
        scored = [(r, f, _value_score(f, per_rank, pbr_rank, ev_rank)) for r, f in kept]
        scored.sort(key=lambda x: (-x[2], x[0][0].stock_code))
        page = scored[offset : offset + limit]
        items = [_to_row(r[0], r[1], r[2], r[3], fin=f, score=score) for r, f, score in page]
    else:
        key = {
            "market_cap": lambda rf: (rf[0][0].market_cap or 0),
            "change": lambda rf: -(rf[0][0].change_pct or 0),
            "trading_value": lambda rf: -(rf[0][0].trading_value or 0),
        }[sort]
        kept.sort(key=lambda rf: (key(rf), rf[0][0].stock_code))
        page = kept[offset : offset + limit]
        items = [_to_row(r[0], r[1], r[2], r[3], fin=f, score=None) for r, f in page]
    return ScreenerResult(as_of=as_of, total=total, items=items)


# ── 이벤트드리븐 전략 ──────────────────────────────────────────────────
def _recent_events(db, codes: list[str], since: date) -> dict[str, dict[str, dict]]:
    """종목별·유형별 최근 이벤트. {code: {kind: {kind,date,summary}}}. 유형별 최신 1건씩."""
    if not codes:
        return {}
    ev: dict[str, dict[str, dict]] = {}

    def _consider(code: str, kind: str, edate: date, summary: str) -> None:
        by_kind = ev.setdefault(code, {})
        cur = by_kind.get(kind)
        if cur is None or edate > cur["date"]:
            by_kind[kind] = {"kind": kind, "date": edate, "summary": summary[:80]}

    for d in db.scalars(
        select(Disclosure).where(Disclosure.stock_code.in_(codes), Disclosure.rcept_dt >= since)
    ).all():
        _consider(d.stock_code, "공시", d.rcept_dt, d.report_nm)
    for r in db.execute(
        select(Report.stock_code, Report.published_date, Report.title)
        .where(Report.stock_code.in_(codes), Report.published_date >= since)
    ).all():
        _consider(r[0], "리포트", r[1], r[2] or "신규 리포트")
    code_set = set(codes)
    since_dt = datetime.combine(since, datetime.min.time())
    for b in db.scalars(select(Broadcast).where(Broadcast.created_at >= since_dt)).all():
        for c in (b.stock_codes or []):
            if c in code_set:
                _consider(c, "브리핑", b.created_at.date(), b.title or "브리핑 언급")
    for e in db.scalars(
        select(StockEvent).where(StockEvent.stock_code.in_(codes), StockEvent.event_date >= since)
    ).all():
        _consider(e.stock_code, "뉴스", e.event_date, e.summary or e.theme or "뉴스 이벤트")
    return ev


def _screen_event(db, base, as_of, event_kind, sort, limit, offset) -> ScreenerResult:
    since = datetime.now().date() - timedelta(days=_EVENT_DAYS)
    rows = list(db.execute(base).all())
    codes = [r[0].stock_code for r in rows]
    ev_map = _recent_events(db, codes, since)
    want_kind = _EVENT_KIND_LABEL.get(event_kind) if event_kind else None

    def _event_of(r) -> dict | None:
        u = r[0]
        by_kind = dict(ev_map.get(u.stock_code, {}))
        if u.change_pct is not None and abs(u.change_pct) >= 7.0:
            by_kind.setdefault("급등락", {"kind": "급등락", "date": as_of, "summary": f"당일 {u.change_pct:+.1f}%"})
        if not by_kind:
            return None
        if want_kind is not None:
            return by_kind.get(want_kind)
        return max(by_kind.values(), key=lambda e: e["date"])

    kept = []
    for r in rows:
        ev = _event_of(r)
        if ev is None:
            continue
        kept.append((r, ev))
    total = len(kept)

    if sort in ("market_cap", "change", "trading_value"):
        key = {
            "market_cap": lambda x: (x[0][0].market_cap or 0),
            "change": lambda x: -(x[0][0].change_pct or 0),
            "trading_value": lambda x: -(x[0][0].trading_value or 0),
        }[sort]
        kept.sort(key=lambda x: (key(x), x[0][0].stock_code))
    else:
        kept.sort(key=lambda x: (x[1]["date"], (x[0][0].trading_value or 0)), reverse=True)
    page = kept[offset : offset + limit]
    items = [_to_row(r[0], r[1], r[2], r[3], event=ev) for r, ev in page]
    return ScreenerResult(as_of=as_of, total=total, items=items)


def _coverage_label(cov_n: int, buy_n: int) -> str | None:
    if not cov_n:
        return None
    return "BUY" if buy_n else "HOLD"


def _to_row(
    u, g, cov_n: int, buy_n: int, *, fin=None, event=None, score: float | None = None
) -> ScreenerRow:
    return ScreenerRow(
        stock_code=u.stock_code,
        stock_name=u.stock_name,
        market=u.market,
        close_price=u.close_price,
        change_pct=u.change_pct,
        market_cap=u.market_cap,
        trading_value=u.trading_value,
        momentum_3m=u.momentum_3m,
        rs_rating=u.rs_rating,
        revenue_yoy=g.revenue_yoy if g else None,
        op_yoy=g.op_yoy if g else None,
        op_turnaround=bool(g.op_turnaround) if g else False,
        coverage_count=int(cov_n or 0),
        recent_sentiment=_coverage_label(int(cov_n or 0), int(buy_n or 0)),
        growth_score=score if fin is None and event is None else None,
        per=fin.per if fin else None,
        pbr=fin.pbr if fin else None,
        roe=fin.roe if fin else None,
        ev_ebitda=fin.ev_ebitda if fin else None,
        div_yield=fin.div_yield if fin else None,
        event_kind=event["kind"] if event else None,
        event_summary=event["summary"] if event else None,
        event_date=event["date"] if event else None,
        score=score,
    )
