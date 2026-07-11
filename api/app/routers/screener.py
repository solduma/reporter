"""종목 스크리너 — 성장·가치·이벤트드리븐 3전략.

공통 유니버스(시총/유동성/시장/섹터) 위에 전략을 얹는다:
- growth: 매출/영업이익 YoY·모멘텀·흑자전환 (GrowthMetric). 필터 통과 집합 내 백분위 스코어.
- value: 저PER·저PBR / 고ROE·저PBR / 저EV-EBITDA (Financial 최신 분기). 저평가 백분위 스코어.
- event: 최근 공시·리포트·급등락·브리핑 이벤트 (Disclosure/Report/Broadcast/등락). 최신·강도 스코어.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
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
    UniverseSnapshot,
)
from app.db.session import get_session
from app.schemas import ScreenerResult, ScreenerRow
from app.services import sector_ingest

router = APIRouter(prefix="/api/screener", tags=["screener"])

_COVERAGE_DAYS = 90
_EVENT_DAYS = 14  # 이벤트 전략: 최근 N일 내 이벤트만


def _latest_date(db: Session) -> date | None:
    return db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))


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


def _cheap_ranker(values: list[float]):
    """저평가 백분위(작을수록 1.0). PER/PBR/EV-EBITDA 처럼 낮을수록 좋은 지표용. 양수만."""
    clean = sorted(v for v in values if v is not None and v > 0)
    n = len(clean)
    if n <= 1:
        return lambda v: 0.5 if (v is not None and v > 0) else 0.0

    def rank(v: float | None) -> float:
        if v is None or v <= 0:  # 결측·적자(음수 PER 등)는 최하위
            return 0.0
        hi = sum(1 for c in clean if c > v)
        return hi / (n - 1)  # 작을수록 1.0

    return rank


def _growth_score(u, g, cov_count, buy_count, rev_rank, op_rank, mom_rank) -> float:
    """성장스코어(0~100). YoY 백분위 + 모멘텀 + 흑전 + 센티먼트·커버리지 factor."""
    rev = rev_rank(g.revenue_yoy if g else None)
    op = op_rank(g.op_yoy if g else None)
    mom = mom_rank(u.momentum_3m)
    turn_bonus = 0.10 if (g and g.op_turnaround) else 0.0
    sentiment_factor = (buy_count / cov_count) if cov_count else 0.0
    coverage_factor = 1.0 if cov_count else 0.0
    score = (
        0.30 * rev + 0.25 * op + 0.15 * mom + turn_bonus
        + 0.12 * sentiment_factor + 0.08 * coverage_factor
    )
    return round(min(score, 1.0) * 100, 1)


def _value_score(fin, per_rank, pbr_rank, ev_rank) -> float:
    """가치스코어(0~100). 저PER·저PBR·저EV-EBITDA 백분위 + 고ROE·고배당 가점.

    저PBR 을 가장 무겁게(자산가치 기준), 저PER·저EV/EBITDA 를 수익가치로. ROE·배당은 우량 가점.
    """
    if fin is None:
        return 0.0
    per = per_rank(fin.per)
    pbr = pbr_rank(fin.pbr)
    ev = ev_rank(fin.ev_ebitda)
    # ROE 절대 기준 가점(15% 이상 만점, % 값). 배당 가점(5% 이상 만점, 시가배당률 %).
    roe_bonus = 0.0
    if fin.roe is not None:
        roe_bonus = max(0.0, min(fin.roe / 15.0, 1.0)) * 0.12
    div_bonus = 0.0
    if fin.div_yield is not None:
        div_bonus = max(0.0, min(fin.div_yield / 5.0, 1.0)) * 0.08
    score = 0.35 * pbr + 0.28 * per + 0.17 * ev + roe_bonus + div_bonus
    return round(min(score, 1.0) * 100, 1)


@router.get("", response_model=ScreenerResult)
def screen(
    strategy: str = Query(default="growth", pattern="^(growth|value|event)$"),
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
    # 이벤트 전략 필터
    event_kind: str | None = Query(
        default=None, pattern="^(disclosure|report|surge|broadcast)$", description="이벤트 유형"
    ),
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
    # 성장 전략 필터(strategy 무관하게 값이 오면 적용).
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
        return _screen_value(
            db, base, as_of, per_max, pbr_max, roe_min, div_min, sort, limit, offset
        )
    if strategy == "event":
        return _screen_event(db, base, as_of, event_kind, sort, limit, offset)
    return _screen_growth(db, base, as_of, sort, limit, offset)


# ── 성장 전략 ──────────────────────────────────────────────────────────
def _screen_growth(db, base, as_of, sort, limit, offset) -> ScreenerResult:
    U, G = UniverseSnapshot, GrowthMetric
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    if sort == "score":
        rows = list(db.execute(base).all())
        rev_rank = _percentile_ranker([r[1].revenue_yoy for r in rows if r[1]])
        op_rank = _percentile_ranker([r[1].op_yoy for r in rows if r[1]])
        mom_rank = _percentile_ranker([r[0].momentum_3m for r in rows])
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

    DISTINCT ON (stock_code) 로 종목당 1행만 DB 에서 뽑는다(전 종목이면 재무 수만 행 전량
    로드를 피함). 정렬 우선순위: per/pbr 있는 행 먼저 → period 최신. 이는 report_ingest 가
    연간(.12) 행에 ev_ebitda 만 채우고 per/pbr 을 NULL 로 둔 반쪽 행이 최신으로 잡혀 종목이
    통째로 누락되는 것을 막는다(밸류 지표 있는 최신 분기 우선, 없으면 최신 행 폴백).
    """
    if not codes:
        return {}
    # has_value: per 또는 pbr 이 있으면 0(먼저), 없으면 1(뒤). DISTINCT ON 은 첫 행을 남긴다.
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

    배당은 연간 지표라 네이버 분기 컬럼에선 결산분기(12월)에만 값이 있고 나머지는 비어 있다.
    분기 파편값이 아닌 결산 배당수익률을 쓰려고 period 가 '.12'인 행만 최신순으로 잡는다.
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
    # 배당은 연간 지표라 per/pbr 최신 분기엔 결측이 흔함 → 배당 기재된 최신 분기값으로 보정.
    div_map = _latest_dividends(db, codes)
    for c, fin in fin_map.items():
        dy = div_map.get(c)
        if dy is not None:
            fin.div_yield = dy  # 세션 객체 in-memory 보정(커밋 안 함, 읽기 전용)

    # 가치 지표 필터 + 최소 데이터(PER/PBR/배당 중 하나 존재) 요구.
    def _passes(fin: Financial | None) -> bool:
        if fin is None or (fin.per is None and fin.pbr is None and fin.div_yield is None):
            return False  # 밸류 지표가 전혀 없는 종목은 가치 스크리너에서 제외
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
        per_rank = _cheap_ranker([f.per for _, f in kept])
        pbr_rank = _cheap_ranker([f.pbr for _, f in kept])
        ev_rank = _cheap_ranker([f.ev_ebitda for _, f in kept])
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
    """종목별·유형별 최근 이벤트. {code: {kind: {kind,date,summary}}}.

    유형별로 최신 1건씩 보관해, event_kind 필터가 '그 유형 이벤트를 가진 종목'을 정확히 잡게
    한다(단일 대표 이벤트만 두면 최신이 다른 유형일 때 해당 종목이 누락됨).
    """
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
    # 브리핑 언급: 최근 브리핑을 한 번에 받아 후보 종목과 교집합(JSONB contains OR 회피).
    code_set = set(codes)
    since_dt = datetime.combine(since, datetime.min.time())
    for b in db.scalars(
        select(Broadcast).where(Broadcast.created_at >= since_dt)
    ).all():
        for c in (b.stock_codes or []):
            if c in code_set:
                _consider(c, "브리핑", b.created_at.date(), b.title or "브리핑 언급")
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
        # 급등락: 당일 등락률 급변(±7%)을 자체 이벤트로 추가(다른 이벤트와 공존 가능).
        if u.change_pct is not None and abs(u.change_pct) >= 7.0:
            by_kind.setdefault("급등락", {"kind": "급등락", "date": as_of, "summary": f"당일 {u.change_pct:+.1f}%"})
        if not by_kind:
            return None
        if want_kind is not None:
            return by_kind.get(want_kind)  # 필터: 그 유형 이벤트만(없으면 제외)
        # 필터 없음: 유형 무관 최신 이벤트를 대표로.
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
        # 기본: 이벤트 최신순(날짜 desc), 동일 날짜는 거래대금 desc.
        kept.sort(key=lambda x: (x[1]["date"], (x[0][0].trading_value or 0)), reverse=True)
    page = kept[offset : offset + limit]
    items = [_to_row(r[0], r[1], r[2], r[3], event=ev) for r, ev in page]
    return ScreenerResult(as_of=as_of, total=total, items=items)


_EVENT_KIND_LABEL = {"disclosure": "공시", "report": "리포트", "surge": "급등락", "broadcast": "브리핑"}


@router.get("/sectors", response_model=list[str])
def screener_sectors() -> list[str]:
    """섹터 필터용 섹터명 목록(국내 섹터 ETF 기준)."""
    from reporter import sector_etf

    return [e.sector for e in sector_etf.KR_SECTOR_ETFS]


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
