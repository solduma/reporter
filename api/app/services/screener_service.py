"""종목 스크리너 조회 서비스 — 종합·성장·가치·추세·탑다운 5전략(읽기 전용 쿼리 엔진).

공통 유니버스(시총/유동성/시장/섹터) 위에 전략을 얹고, 최근 이벤트는 모든 행에 컬럼으로 붙인다:
- growth: 매출/영업이익 YoY·모멘텀·흑자전환 (GrowthMetric). 집합 내 백분위 스코어.
- value: 저PER·저PBR·저EV-EBITDA + 고ROE·고배당 (Financial 최신 분기). 저평가 백분위 스코어.
- trend: 기술적 추세 종합(사전계산 trend_score, 종목분석과 동일 4요소).
- topdown: 종목이 속한 섹터의 국내/미국 수급 flow(섹터 flow 를 종목에 매핑).
- overall(종합): 위 계산 가능한 축들의 단순 평균(테크노펀더멘탈 종합과 동일).

라우터는 이 서비스의 screen() 에 쿼리 파라미터만 넘긴다. 스코어링 규칙은 domain.scoring/
analysis_scoring, 결과는 ScreenerResult 읽기모델(read-model)로 반환한다.
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
    SectorTheme,
    SectorThemeStock,
    Sentiment,
    StockEvent,
    UniverseSnapshot,
)
from app.domain import analysis_scoring
from app.schemas import ScreenerResult, ScreenerRow
from app.services import sector_flow, sector_ingest, universe_ingest
from reporter import sector_etf

_COVERAGE_DAYS = 90
_EVENT_DAYS = 14  # 이벤트 컬럼: 최근 N일 내 이벤트만


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


def _growth_score(u, g) -> float | None:
    """성장스코어 — 종목분석과 동일한 절대 밴드(analysis_scoring.growth_score).

    스크리너 순위는 정렬이 이미 제공하므로, 점수는 집합 무관 절대값으로 통일해 종목분석과 일치시킨다
    (필터를 바꿔도·상세로 넘어가도 같은 숫자). 모멘텀·커버리지 factor 는 정렬 축이라 점수에서 제외."""
    return analysis_scoring.growth_score(
        g.revenue_yoy if g else None,
        g.op_yoy if g else None,
        bool(g and g.op_turnaround),
    )


def _value_score(fin) -> float | None:
    """가치스코어 — 종목분석과 동일한 절대 밴드(analysis_scoring.value_score_abs)."""
    if fin is None:
        return None
    score, _ = analysis_scoring.value_score_abs(
        fin.per, fin.pbr, fin.ev_ebitda, fin.roe, fin.div_yield
    )
    return score


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
    if strategy == "trend":
        return _screen_trend(db, base, as_of, sort, limit, offset)
    if strategy == "topdown":
        return _screen_topdown(db, base, as_of, sort, limit, offset)
    if strategy == "overall":
        return _screen_overall(db, base, as_of, sort, limit, offset)
    return _screen_growth(db, base, as_of, sort, limit, offset)


# ── 성장 전략 ──────────────────────────────────────────────────────────
def _screen_growth(db, base, as_of, sort, limit, offset) -> ScreenerResult:
    U, G = UniverseSnapshot, GrowthMetric
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    if sort == "score":
        rows = list(db.execute(base).all())
        scored = [(r, _growth_score(r[0], r[1])) for r in rows]
        # 절대 점수라 결측(None)은 최하위로 정렬(-1). 표시는 None 유지.
        scored.sort(key=lambda x: (-(x[1] if x[1] is not None else -1), x[0][0].stock_code))
        page = scored[offset : offset + limit]
        ev = _representative_events(db, [r[0].stock_code for r, _ in page], as_of)
        items = [
            _to_row(r[0], r[1], r[2], r[3], score=score, growth_score=score,
                    event=ev.get(r[0].stock_code))
            for r, score in page
        ]
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
        ev = _representative_events(db, [r[0].stock_code for r in rows], as_of)
        items = [
            _to_row(r[0], r[1], r[2], r[3], score=None, event=ev.get(r[0].stock_code))
            for r in rows
        ]
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
        scored = [(r, f, _value_score(f)) for r, f in kept]
        scored.sort(key=lambda x: (-(x[2] if x[2] is not None else -1), x[0][0].stock_code))
        page = scored[offset : offset + limit]
        ev = _representative_events(db, [r[0].stock_code for r, _, _ in page], as_of)
        items = [
            _to_row(r[0], r[1], r[2], r[3], fin=f, score=score, value_score=score,
                    event=ev.get(r[0].stock_code))
            for r, f, score in page
        ]
    else:
        key = {
            "market_cap": lambda rf: (rf[0][0].market_cap or 0),
            "change": lambda rf: -(rf[0][0].change_pct or 0),
            "trading_value": lambda rf: -(rf[0][0].trading_value or 0),
        }[sort]
        kept.sort(key=lambda rf: (key(rf), rf[0][0].stock_code))
        page = kept[offset : offset + limit]
        ev = _representative_events(db, [r[0].stock_code for r, _ in page], as_of)
        items = [
            _to_row(r[0], r[1], r[2], r[3], fin=f, score=None, event=ev.get(r[0].stock_code))
            for r, f in page
        ]
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


# ── 추세 전략(사전계산 trend_score) ────────────────────────────────────
def _screen_trend(db, base, as_of, sort, limit, offset) -> ScreenerResult:
    """기술적 추세 종합(trend_score) 정렬. trend_score 는 야간 배치가 사전계산(종목분석과 동일)."""
    U = UniverseSnapshot
    conds_base = base.where(U.trend_score.is_not(None))
    total = db.scalar(select(func.count()).select_from(conds_base.subquery())) or 0
    db_sort = {
        "score": U.trend_score.desc().nulls_last(),
        "market_cap": U.market_cap.asc(),
        "change": U.change_pct.desc().nulls_last(),
        "trading_value": U.trading_value.desc().nulls_last(),
    }.get(sort, U.trend_score.desc().nulls_last())
    rows = db.execute(conds_base.order_by(db_sort, U.stock_code).limit(limit).offset(offset)).all()
    ev = _representative_events(db, [r[0].stock_code for r in rows], as_of)
    items = [
        _to_row(r[0], r[1], r[2], r[3], score=r[0].trend_score, event=ev.get(r[0].stock_code))
        for r in rows
    ]
    return ScreenerResult(as_of=as_of, total=total, items=items)


# ── 탑다운 전략(섹터 flow → 종목 매핑) ──────────────────────────────────
def _stock_sector_map(db, codes: list[str]) -> dict[str, str | None]:
    """종목 → 대표 국내 섹터. judal 테마명들을 sector_etf.themes_to_kr_sector 로 접어 정한다.
    종목당 테마를 모아 한 번에 매핑(N+1 회피)."""
    if not codes:
        return {}
    rows = db.execute(
        select(SectorThemeStock.stock_code, SectorTheme.name)
        .join(SectorTheme, SectorTheme.judal_idx == SectorThemeStock.judal_idx)
        .where(SectorThemeStock.stock_code.in_(codes))
    ).all()
    themes: dict[str, list[str]] = {}
    for code, name in rows:
        themes.setdefault(code, []).append(name)
    return {code: sector_etf.themes_to_kr_sector(names) for code, names in themes.items()}


def _topdown_scores(db) -> dict[str, tuple[float | None, float | None]]:
    """국내 섹터명 → (국내 flow, 미국 대응섹터 flow). 섹터 flow 는 소수라 1회 계산."""
    kr_flows = {f.sector: f.flow_score for f in sector_flow.compute_flows("KR")}
    us_flows = {f.sector: f.flow_score for f in sector_flow.compute_flows("US")}
    out: dict[str, tuple[float | None, float | None]] = {}
    for kr_sector in kr_flows:
        us_sector = sector_etf.kr_sector_to_us(kr_sector)
        out[kr_sector] = (kr_flows.get(kr_sector), us_flows.get(us_sector) if us_sector else None)
    return out


def _index_rising(market: str | None) -> bool | None:
    """종목 시장(KOSPI/KOSDAQ)에 해당하는 지수의 당일 방향. 종목분석 build_topdown 과 동일 기준."""
    from reporter import us_market

    name = "코스닥" if market == "KOSDAQ" else "코스피"
    for q in us_market.fetch_kr_indices():
        if q.name == name:
            return q.rising
    return None


def _stock_topdown_score(
    kr_sector: str | None, flows: dict, index_rising: bool | None
) -> float | None:
    if kr_sector is None or kr_sector not in flows:
        return None
    kr_f, us_f = flows[kr_sector]
    # 섹터 flow 를 하나도 못 구하면 지수 방향만으로 점수 내지 않고 None(종합서 제외) — 종목분석 일치.
    if kr_f is None and us_f is None:
        return None
    # 종목분석과 동일하게 지수 방향(0.15 가중)까지 포함해 점수를 일치시킨다.
    return analysis_scoring.topdown_flow_score(us_f, kr_f, index_rising)


def _screen_topdown(db, base, as_of, sort, limit, offset) -> ScreenerResult:
    """종목이 속한 섹터의 국내/미국 수급 flow 로 탑다운 점수. 섹터 매핑 실패·flow 없으면 제외."""
    rows = list(db.execute(base).all())
    codes = [r[0].stock_code for r in rows]
    sector_map = _stock_sector_map(db, codes)
    flows = _topdown_scores(db)
    idx_cache: dict[str | None, bool | None] = {}
    scored = []
    for r in rows:
        kr_sec = sector_map.get(r[0].stock_code)
        mkt = r[0].market
        if mkt not in idx_cache:
            idx_cache[mkt] = _index_rising(mkt)
        sc = _stock_topdown_score(kr_sec, flows, idx_cache[mkt])
        if sc is None:
            continue
        scored.append((r, kr_sec, sc))
    total = len(scored)
    if sort in ("market_cap", "change", "trading_value"):
        key = {
            "market_cap": lambda x: (x[0][0].market_cap or 0),
            "change": lambda x: -(x[0][0].change_pct or 0),
            "trading_value": lambda x: -(x[0][0].trading_value or 0),
        }[sort]
        scored.sort(key=lambda x: (key(x), x[0][0].stock_code))
    else:
        scored.sort(key=lambda x: (-x[2], x[0][0].stock_code))
    page = scored[offset : offset + limit]
    ev = _representative_events(db, [r[0].stock_code for r, _, _ in page], as_of)
    items = [
        _to_row(r[0], r[1], r[2], r[3], score=sc, topdown_score=sc, kr_sector=kr_sec,
                event=ev.get(r[0].stock_code))
        for r, kr_sec, sc in page
    ]
    return ScreenerResult(as_of=as_of, total=total, items=items)


# ── 종합 전략(계산 가능한 축 평균) ──────────────────────────────────────
def _screen_overall(db, base, as_of, sort, limit, offset) -> ScreenerResult:
    """성장·가치·추세·탑다운 중 계산 가능한 축의 단순 평균(테크노펀더멘탈 종합과 동일 규칙).

    각 축이 절대 밴드라 집합 무관 — 종목분석과 같은 점수를 낸다. 전 행을 스코어링한 뒤 페이지네이션."""
    rows = list(db.execute(base).all())
    codes = [r[0].stock_code for r in rows]

    # 가치용 최신 재무(배당 보정) — 절대 밴드라 랭커 불필요.
    fin_map = _latest_financials(db, codes)
    div_map = _latest_dividends(db, codes)
    for c, fin in fin_map.items():
        dy = div_map.get(c)
        if dy is not None:
            fin.div_yield = dy
    # 탑다운(섹터 flow + 지수 방향)
    sector_map = _stock_sector_map(db, codes)
    flows = _topdown_scores(db)
    idx_cache: dict[str | None, bool | None] = {}

    scored = []
    for r in rows:
        u, g = r[0], r[1]
        gsc = _growth_score(u, g)
        fin = fin_map.get(u.stock_code)
        vsc = _value_score(fin)
        tsc = u.trend_score
        kr_sec = sector_map.get(u.stock_code)
        if u.market not in idx_cache:
            idx_cache[u.market] = _index_rising(u.market)
        dsc = _stock_topdown_score(kr_sec, flows, idx_cache[u.market])
        overall = analysis_scoring.overall([gsc, vsc, tsc, dsc])
        if overall is None:
            continue
        scored.append((r, fin, kr_sec, gsc, vsc, tsc, dsc, overall))
    total = len(scored)
    if sort in ("market_cap", "change", "trading_value"):
        key = {
            "market_cap": lambda x: (x[0][0].market_cap or 0),
            "change": lambda x: -(x[0][0].change_pct or 0),
            "trading_value": lambda x: -(x[0][0].trading_value or 0),
        }[sort]
        scored.sort(key=lambda x: (key(x), x[0][0].stock_code))
    else:
        scored.sort(key=lambda x: (-x[7], x[0][0].stock_code))
    page = scored[offset : offset + limit]
    ev = _representative_events(db, [x[0][0].stock_code for x in page], as_of)
    items = [
        _to_row(
            r[0], r[1], r[2], r[3], fin=fin, score=overall, growth_score=gsc, value_score=vsc,
            trend_score=tsc, topdown_score=dsc, kr_sector=kr_sec, event=ev.get(r[0].stock_code),
        )
        for r, fin, kr_sec, gsc, vsc, tsc, dsc, overall in page
    ]
    return ScreenerResult(as_of=as_of, total=total, items=items)


def _representative_events(db, codes: list[str], as_of: date) -> dict[str, dict]:
    """종목별 대표(최신) 이벤트 1건 — 모든 전략 행에 컬럼으로 붙일 용도. 없으면 키 없음.

    급등락(당일 |등락|≥7%)은 유니버스 스냅샷에서 이미 알 수 있으나, 여기선 실이벤트(공시·리포트·
    브리핑·뉴스)만 최신 1건을 고른다(급등락은 등락률 컬럼으로 이미 보임)."""
    since = as_of - timedelta(days=_EVENT_DAYS)
    by_kind = _recent_events(db, codes, since)  # {code: {kind: {kind,date,summary}}}
    rep: dict[str, dict] = {}
    for code, kinds in by_kind.items():
        if kinds:
            rep[code] = max(kinds.values(), key=lambda e: e["date"])
    return rep


def _coverage_label(cov_n: int, buy_n: int) -> str | None:
    if not cov_n:
        return None
    return "BUY" if buy_n else "HOLD"


def _to_row(
    u,
    g,
    cov_n: int,
    buy_n: int,
    *,
    fin=None,
    event=None,
    score: float | None = None,
    growth_score: float | None = None,
    value_score: float | None = None,
    trend_score: float | None = None,
    topdown_score: float | None = None,
    kr_sector: str | None = None,
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
        growth_score=growth_score,
        value_score=value_score,
        per=fin.per if fin else None,
        pbr=fin.pbr if fin else None,
        roe=fin.roe if fin else None,
        ev_ebitda=fin.ev_ebitda if fin else None,
        div_yield=fin.div_yield if fin else None,
        trend_score=trend_score if trend_score is not None else u.trend_score,
        topdown_score=topdown_score,
        kr_sector=kr_sector,
        event_kind=event["kind"] if event else None,
        event_summary=event["summary"] if event else None,
        event_date=event["date"] if event else None,
        score=score,
    )
