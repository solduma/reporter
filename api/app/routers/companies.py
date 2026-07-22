"""기업 분석 페이지용 라우터 — 검색·요약·봉·분석·재무·피어·타임라인·성장지표.

데이터 접근·스크랩·백필은 services/company_service 가 담당하고, 여기선 쿼리 파라미터를 받아
결과를 DTO(AnalysisAxis·TimelineItem 등)로 조립한다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.domain import analysis_scoring, judgment, score_factors, stage, technicals
from app.schemas import (
    AnalysisAxis,
    CandlePoint,
    CompanyAnalysis,
    CompanyGrowth,
    CompanySummary,
    CompanyTrend,
    FinancialPeriodOut,
    FinancialsStatusOut,
    FinancialStatementItem,
    FinancialStatementPeriod,
    FinancialStatementsOut,
    JudgmentOut,
    PeerOut,
    ReportCard,
    ScoreFactor,
    StockSearchHit,
    TimelineCacheResponse,
    TimelineItem,
    TopDownView,
)
from app.services import (
    analysis,
    analysis_comment,
    candle_service,
    company_service,
    screener_service,
    today_service,
    trend,
)

router = APIRouter(prefix="/api/companies", tags=["companies"])

# 성장 스냅샷 리포트 커버리지 집계 창(일). 최근 1년치 리서치 커버리지·BUY 비율을 본다.
_COVERAGE_DAYS = 365

# 도메인 강도 분류 → 한글 라벨(표현은 라우터 edge 책임).
_FLOW_TAG = {"strong": "강함", "moderate": "보통", "weak": "약함"}


def _flow_label(score: float | None) -> str:
    """자금유입 강도(0~100)를 '강함/보통/약함 NN' 표시 문자열로. None 은 '—'."""
    strength = analysis_scoring.flow_strength(score)
    return f"{_FLOW_TAG[strength]} {score:.0f}" if strength else "—"


def _search_rank(query: str, code: str, name: str) -> int:
    """검색 랭크(작을수록 상위): 코드 완전일치 0 > 코드 prefix 1 > 이름 prefix 2 > 부분일치 3."""
    if code == query:
        return 0
    if code.startswith(query):
        return 1
    if name.startswith(query):
        return 2
    return 3


@router.get("/search", response_model=list[StockSearchHit])
def search_stocks(
    q: str = Query(..., min_length=1, max_length=40, description="종목 코드 또는 종목명"),
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_session),
) -> list[StockSearchHit]:
    """종목 코드·이름 퍼지 검색. 최신 유니버스 스냅샷의 보통주에서 찾는다.

    랭크: 코드 완전일치 > 코드 prefix > 이름 prefix > 이름 부분일치. 동순위는 시총 큰 순.
    """
    q = q.strip()
    if not q:
        return []
    rows = company_service.search_candidates(db, q)
    # 랭크 오름차순 → 동순위는 시총 내림차순(대형주 먼저).
    ranked = sorted(rows, key=lambda r: (_search_rank(q, r[0], r[1]), -(r[3] or 0)))
    return [
        StockSearchHit(stock_code=c, stock_name=n, market=m, market_cap=cap)
        for c, n, m, cap in ranked[:limit]
    ]


@router.get("/{code}/summary", response_model=CompanySummary)
def company_summary(code: str, db: Session = Depends(get_session)) -> CompanySummary:
    return CompanySummary(stock_code=code, stock_name=company_service.resolve_stock_name(db, code))


@router.get("/{code}/candles", response_model=list[CandlePoint])
def company_candles(
    code: str,
    bg: BackgroundTasks,
    tf: str = Query(default="day", pattern="^(30m|day|week|month)$"),
    db: Session = Depends(get_session),
) -> list[CandlePoint]:
    """종목 봉 — DB 우선 즉시 반환. 뒤처졌으면 백그라운드 증분 갱신을 예약한다."""
    if tf == "30m":
        rows_i = candle_service.read_intraday_or_fetch(db, code, days=14)
        bg.add_task(candle_service.refresh_intraday, code)
        return [
            CandlePoint(
                t=r.bar_ts.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume
            )
            for r in rows_i
        ]

    rows = candle_service.ensure_periodic(db, code, tf)
    if candle_service.is_stale(db, code, tf):
        bg.add_task(candle_service.refresh_periodic, code, tf)
    return [
        CandlePoint(t=r.bar_date.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume)
        for r in rows
    ]


@router.get("/{code}/analysis", response_model=CompanyAnalysis)
def company_analysis(
    code: str,
    bg: BackgroundTasks,
    db: Session = Depends(get_session),
    quick: bool = Query(default=False),
) -> CompanyAnalysis:
    """테크노펀더멘탈 종합 — 성장·기술적 추세·탑다운.

    quick=true: 외부 API 가 필요 없는 성장·가치 축만 반환(추세·탑다운은 score None).
    프론트가 빠르게 1차 렌더 후 전체 재조회하는 패턴용.
    """
    settings = get_settings()
    if candle_service.is_stale(db, code, "day"):
        bg.add_task(candle_service.refresh_periodic, code, "day")
    snap = company_service.latest_snapshot(db, code)
    name = (snap.stock_name if snap else None) or company_service.resolve_stock_name(db, code)
    market = snap.market if snap else None

    # 성장 축 — GrowthMetric (DB only, 항상 빠름).
    g = company_service.growth_metric(db, code)
    growth_sc = analysis.growth_score(
        g.revenue_yoy if g else None,
        g.op_status if g else None,
        g.op_margin_delta if g else None,
        g.net_status if g else None,
        g.net_margin_delta if g else None,
        g.ebitda_status if g else None,
        g.ebitda_margin_delta if g else None,
    )
    growth_axis = AnalysisAxis(
        key="growth",
        label="성장",
        score=growth_sc,
        metrics=[
            {"label": "성장 등급", "value": _grade(growth_sc)},
            {"label": "영업손익", "value": (g.op_status if g and g.op_status else "—")},
        ],
        method=score_factors.GROWTH_METHOD,
        factors=_factors(
            score_factors.growth_factors(
                g.revenue_yoy if g else None,
                g.op_status if g else None,
                g.op_margin_delta if g else None,
                g.net_status if g else None,
                g.net_margin_delta if g else None,
                g.ebitda_status if g else None,
                g.ebitda_margin_delta if g else None,
            )
        ),
    )

    # 가치 축 — 최신 밸류에이션 (DB only, 항상 빠름). 연결(CFS) 우선, 없으면 별도(OFS).
    fin = company_service.latest_valuation(db, code, fs_div="CFS") or company_service.latest_valuation(db, code, fs_div="OFS")
    per = fin.per if fin else None
    pbr = fin.pbr if fin else None
    ev = fin.ev_ebitda if fin else None
    roe = fin.roe if fin else None
    dy = fin.div_yield if fin else None
    eps_yoy = g.eps_yoy if g else None
    net_status = g.net_status if g else None
    net_margin_delta = g.net_margin_delta if g else None
    value_sc, (per_r, pbr_r, ev_r, peg_r) = analysis_scoring.value_score_abs(
        per, pbr, ev, roe, dy, eps_yoy, net_status, net_margin_delta
    )
    peg_val = analysis_scoring.peg(per, eps_yoy)
    peg_display = (
        f"{peg_val:.2f}"
        if peg_val is not None
        else net_status
        if net_status in ("흑자전환", "흑자지속") and net_margin_delta is not None
        else "—"
    )
    value_axis = AnalysisAxis(
        key="value",
        label="가치",
        score=value_sc,
        metrics=[
            {"label": "PER", "value": f"{per:.1f}배" if per else "—"},
            {"label": "PBR", "value": f"{pbr:.2f}배" if pbr else "—"},
            {"label": "PEG", "value": peg_display},
            {"label": "ROE", "value": f"{roe:.1f}%" if roe is not None else "—"},
            {"label": "배당수익률", "value": f"{dy:.1f}%" if dy is not None else "—"},
        ],
        method=score_factors.VALUE_METHOD,
        factors=_factors(
            score_factors.value_factors(
                per,
                pbr,
                ev,
                roe,
                dy,
                per_r,
                pbr_r,
                ev_r,
                peg_r,
                peg_val,
                peg_surrogate_status=(
                    net_status
                    if peg_val is None and net_status in ("흑자전환", "흑자지속")
                    else None
                ),
            )
        ),
    )

    if quick:
        # quick 모드: 외부 API 가 필요한 추세·탑다운은 생략하고 성장·가치만 반환.
        axes = [growth_axis, value_axis]
        overall_sc = analysis.overall([growth_sc, value_sc, None, None])
        j = judgment.summarize(overall_sc, {"growth": growth_sc, "value": value_sc})
        return CompanyAnalysis(
            stock_code=code,
            stock_name=name,
            market=market,
            overall_score=overall_sc,
            axes=axes,
            topdown=None,
            judgment=_judgment_out(j),
            comment=None,
            comment_pending=False,
        )

    # 기술 축 — 일봉 지표 + 와인스타인 중기 국면(주봉 30주). 외부 API 필요 시 느림.
    candles = company_service.ensure_day_candles(db, code)
    _mid = stage.FRAMES["mid"]
    _mid_b = stage.resample_ohlcv(
        [c.bar_date.isoformat() for c in candles],
        [c.high for c in candles],
        [c.low for c in candles],
        [c.close for c in candles],
        [int(c.volume or 0) for c in candles],
        _mid.bar,
    )
    mid_stage = stage.classify(
        _mid_b.closes,
        _mid.ma_period,
        _mid.slope_lookback,
        _mid_b.volumes,
        _mid_b.highs,
        _mid_b.lows,
    )
    tech = technicals.compute(candles, stage=mid_stage.stage)
    tech_axis = AnalysisAxis(
        key="technical",
        label="추세",
        score=tech.trend_score,
        metrics=[
            {"label": "와인스타인 국면", "value": mid_stage.label or "—"},
            {
                "label": "RS Rating",
                "value": f"{snap.rs_rating}" if snap and snap.rs_rating else "—",
            },
            {
                "label": "52주 고점 근접",
                "value": f"{tech.near_high_pct}%" if tech.near_high_pct else "—",
            },
            {"label": "이평 정배열", "value": _yn(tech.ma_aligned)},
            {"label": "거래량비", "value": f"{tech.vol_ratio}x" if tech.vol_ratio else "—"},
            {
                "label": "3개월 수익률",
                "value": f"{tech.return_3m}%" if tech.return_3m is not None else "—",
            },
        ],
        method=score_factors.TREND_METHOD,
        factors=_factors(
            score_factors.trend_factors(
                tech.near_high_pct,
                tech.ma_aligned,
                tech.above_ma120,
                tech.vol_ratio,
                tech.return_3m,
                mid_stage.stage,
            )
        ),
    )

    # 탑다운 축 — 종목 섹터의 국내/미국 수급 flow(미국 선행) + 국내 지수 + 종목 상대강도(RS).
    theme_names = company_service.theme_names(db, code)
    stock_rs = float(snap.rs_rating) if snap and snap.rs_rating else None
    topdown_view, topdown_sc = analysis.build_topdown(
        theme_names, market, code=code, stock_rs=stock_rs
    )
    kr_sec = topdown_view["kr_sector"]
    us_sec = topdown_view["us_sector"]
    kr_index_flow = topdown_view["kr_index_flow"]
    idx_label = "국내 코스닥 수급" if market == "KOSDAQ" else "국내 코스피 수급"
    topdown_axis = AnalysisAxis(
        key="topdown",
        label="탑다운",
        score=topdown_sc,
        metrics=[
            {
                "label": f"국내 {kr_sec} 수급" if kr_sec else "국내 섹터",
                "value": _flow_label(topdown_view["kr_sector_flow"]),
            },
            {
                "label": f"미국 {us_sec} 수급(선행)" if us_sec else "미국 섹터(선행)",
                "value": _flow_label(topdown_view["us_sector_flow"]),
            },
            {"label": idx_label, "value": _flow_label(kr_index_flow)},
            {"label": "종목 상대강도(RS)", "value": f"{int(stock_rs)}" if stock_rs else "—"},
        ],
        method=score_factors.TOPDOWN_METHOD,
        factors=_factors(
            score_factors.topdown_factors(
                topdown_view["us_sector_flow"],
                topdown_view["kr_sector_flow"],
                kr_index_flow,
                stock_rs,
            )
        ),
    )

    axes = [growth_axis, value_axis, tech_axis, topdown_axis]
    overall_sc = analysis.overall([growth_sc, value_sc, tech.trend_score, topdown_sc])

    j = judgment.summarize(
        overall_sc,
        {
            "growth": growth_sc,
            "value": value_sc,
            "technical": tech.trend_score,
            "topdown": topdown_sc,
        },
    )
    judgment_out = _judgment_out(j)

    # LLM 종합 코멘트 — 캐시 우선, 미스면 백그라운드 생성.
    axes_dump = [a.model_dump() for a in axes]
    comment = None
    comment_pending = False
    if settings.ollama_api_key:
        ctx = _comment_context(db, code)
        h = analysis_comment.inputs_hash(axes_dump, ctx)
        comment = analysis_comment.get_cached(db, code, h)
        if comment is None:
            comment_pending = True
            bg.add_task(analysis_comment.generate_and_store, code, name or code, axes_dump, h, ctx)

    return CompanyAnalysis(
        stock_code=code,
        stock_name=name,
        market=market,
        overall_score=overall_sc,
        axes=axes,
        topdown=TopDownView(**topdown_view),
        judgment=judgment_out,
        comment=comment,
        comment_pending=comment_pending,
    )


def _judgment_out(j: judgment.Judgment) -> JudgmentOut:
    """judgment.summarize 결과를 JudgmentOut 으로 변환."""
    return JudgmentOut(
        signal=j.signal,
        signal_label=j.signal_label,
        strengths=j.strengths,
        weaknesses=j.weaknesses,
        checks=j.checks,
    )


@router.get("/{code}/trend", response_model=CompanyTrend)
def company_trend(
    code: str, bg: BackgroundTasks, db: Session = Depends(get_session)
) -> CompanyTrend:
    """기술적 추세 — 와인스타인 국면(단/중/장기) + Mansfield 상대강도(지수 대비).

    사전계산 캐시(TrendCache, 야간 배치) 우선. 미스·stale(신규종목·새 확정봉) 시 동기 계산 후
    저장(cache-aside) — 첫 요청만 느리고 이후 읽기. rs_rating 은 캐시와 별개로 스냅샷에서 붙는다.
    """
    if candle_service.is_stale(db, code, "day"):
        bg.add_task(candle_service.refresh_periodic, code, "day")

    cached = trend.get_cached_trend(db, code)
    if cached is not None:
        return cached

    snap = company_service.latest_snapshot(db, code)
    market = snap.market if snap else None
    result = trend.compute_trend(db, code, market)
    trend.store_trend(db, code, result)  # cache-aside 저장(다음 요청부터 캐시 히트)
    return trend.build_company_trend(code, result, rs_rating=snap.rs_rating if snap else None)


def _yn(v: bool | None) -> str:
    return "예" if v is True else "아니오" if v is False else "—"


def _factors(factors: list[score_factors.Factor]) -> list[ScoreFactor]:
    """도메인 Factor → 스키마 ScoreFactor(직렬화)."""
    return [ScoreFactor(**f.as_dict()) for f in factors]


def _grade(score: float | None) -> str:
    """성장 점수(0~100)를 등급 라벨로. 원시 YoY 대신 점수 해석을 보인다."""
    if score is None:
        return "데이터 없음"
    if score >= 70:
        return "고성장"
    if score >= 50:
        return "성장"
    if score >= 30:
        return "완만"
    return "정체·역성장"


def _comment_context(db: Session, code: str) -> analysis.CommentContext:
    """LLM 종합 코멘트용 시장 맥락·정성 재료를 모은다(오늘 시황·국면 + 최근 리서치·공시 정제문).

    리포트·공시의 요약/근거(이미 저장된 정제문)를 넣어 '애널리스트가 실제로 뭐라 했는지'까지
    LLM 이 읽게 한다. 최신순 소수만(토큰·프롬프트 통제).
    """
    from datetime import datetime, timedelta

    mi = today_service.market_info(db, None)
    now = datetime.now()
    since = now.date() - timedelta(days=30)
    reports, buys = company_service.coverage_counts(db, code, since)

    rows = company_service.timeline_reports(db, code, since, now.date())
    rows.sort(key=lambda ra: ra[0].published_date, reverse=True)  # 최신순
    report_notes = [
        f"{r.broker} {a.sentiment.value}: {(a.summary or a.rationale or r.title)[:120]}"
        for r, a in rows[:4]
    ]

    discs = company_service.timeline_disclosures(db, code, since, now.date())
    discs.sort(key=lambda d: d.rcept_dt, reverse=True)
    disclosure_notes = [
        f"{d.report_nm}{(' — ' + d.rationale[:80]) if d.rationale else ''}" for d in discs[:3]
    ]

    return analysis.CommentContext(
        market_phase=(mi.phase or None) if mi else None,
        market_summary=(mi.summary or None) if mi else None,
        report_count=reports,
        buy_count=buys,
        recent_disclosures=[d.report_nm for d in discs[:3]],
        report_notes=report_notes,
        disclosure_notes=disclosure_notes,
    )


@router.get("/{code}/financials", response_model=list[FinancialPeriodOut])
def company_financials(
    code: str, bg: BackgroundTasks, db: Session = Depends(get_session)
) -> list[FinancialPeriodOut]:
    rows = company_service.financials_rows(db, code)
    # DB 우선: 저장분이 없거나(최초) TTL 만료면 백그라운드로 스크랩·산출한다. 응답은 즉시.
    if not rows:
        company_service.sync_financials(db, code)  # 최초만 동기 조회로 화면을 채운다
        rows = company_service.financials_rows(db, code)
    elif not company_service.financials_fresh(db, code):
        bg.add_task(company_service.sync_financials_bg, code)
    # 10년 재무·밸류(PER/PBR/PSR) 백필·보고서 원문(EV/EBITDA) 백필은 종목당 1회만
    # (야간 배치가 나머지를 채움). 아직이면 백그라운드로. 역사 시총 기준이라 재조회 불필요.
    if not company_service.financials_10y_done(db, code):
        bg.add_task(company_service.backfill_financials_10y_bg, code)
    if not company_service.report_10y_done(db, code):
        bg.add_task(company_service.backfill_reports_bg, code)

    return [
        FinancialPeriodOut(
            period=r.period,
            is_estimate=r.is_estimate,
            revenue=r.revenue,
            operating_income=r.operating_income,
            net_income=r.net_income,
            eps=r.eps,
            bps=r.bps,
            per=r.per,
            pbr=r.pbr,
            psr=r.psr,
            roe=r.roe,
            ev_ebitda=r.ev_ebitda,
            dps=r.dps,
            div_yield=r.div_yield,
        )
        for r in rows
    ]


@router.get("/{code}/financials/status", response_model=FinancialsStatusOut)
def company_financials_status(code: str, db: Session = Depends(get_session)) -> FinancialsStatusOut:
    """재무 백필 진행상태. 프론트가 '가용분 즉시 표시 + 백필 중 배지'를 그리기 위한 경량 조회.

    /financials 가 백그라운드로 건 10년 재무·보고서원문 백필의 완료 여부를 읽기 전용으로 노출한다
    (부수효과·백필 트리거 없음)."""
    return FinancialsStatusOut(
        fresh=company_service.financials_fresh(db, code),
        financials_10y_done=company_service.financials_10y_done(db, code),
        report_10y_done=company_service.report_10y_done(db, code),
    )


@router.get("/{code}/financial-statements", response_model=FinancialStatementsOut)
def company_financial_statements(
    code: str, db: Session = Depends(get_session), fs_div: str = "CFS"
) -> FinancialStatementsOut:
    """종목의 전체 재무제표(재무상태표·손익계산서·현금흐름표·자본변동표) 시계열.

    fs_div: CFS(연결) | OFS(별도). DB 에 없으면 DART 에서 온디맨드 조회해 저장한다.
    """
    rows = company_service.financial_statement_rows(db, code, fs_div)
    if not rows:
        company_service.fetch_and_store_financial_statements(db, code, fs_div)
        rows = company_service.financial_statement_rows(db, code, fs_div)

    periods = []
    for r in rows:
        data = r.data or {}
        # IS(손익계산서)는 DART에서 CIS(포괄손익계산서)로 분류되는 경우가 많아
        # IS가 비었으면 CIS를 IS로 사용한다.
        is_items = [FinancialStatementItem(**i) for i in data.get("IS", [])]
        if not is_items:
            is_items = [FinancialStatementItem(**i) for i in data.get("CIS", [])]
        periods.append(FinancialStatementPeriod(
            period=r.period,
            fs_div=r.fs_div,
            bs=[FinancialStatementItem(**i) for i in data.get("BS", [])],
            is_=is_items,
            cis=[FinancialStatementItem(**i) for i in data.get("CIS", [])],
            cf=[FinancialStatementItem(**i) for i in data.get("CF", [])],
        ))
    return FinancialStatementsOut(stock_code=code, periods=periods)


@router.get("/{code}/peers", response_model=list[PeerOut])
def company_peers(
    code: str, bg: BackgroundTasks, db: Session = Depends(get_session)
) -> list[PeerOut]:
    rows = company_service.peers_rows(db, code)
    # DB 우선: 없으면 최초 동기 조회, TTL 만료면 백그라운드 갱신.
    if not rows:
        company_service.sync_peers(db, code)
        rows = company_service.peers_rows(db, code)
    elif not company_service.peers_fresh(db, code):
        bg.add_task(company_service.sync_peers_bg, code)
    peer_codes = [r.peer_stock_code for r in rows]
    # EV/EBITDA·PSR 은 네이버 동일업종 테이블에 없어, 각 peer 의 최근 Financial(DART 산출)에서 채운다.
    val = company_service.peer_valuations(db, peer_codes)
    # 상세 조회된 적 없는 peer 는 report_10y 백필이 안 돌아 ev_ebitda 가 빈다 → 백그라운드 백필 트리거
    # (본 종목 온디맨드 백필과 동일 패턴). 다음 조회부터 채워진다.
    for pc in peer_codes:
        if not company_service.report_10y_done(db, pc):
            bg.add_task(company_service.backfill_reports_bg, pc)
    # 동일업종 4축·종합 점수 — 종목분석·스크리너와 동일 절대 밴드(집합 무관 같은 점수).
    scores = screener_service.peer_scores(db, peer_codes)
    return [
        PeerOut(
            stock_code=r.peer_stock_code,
            name=r.peer_name,
            price=r.price,
            market_cap=r.market_cap,
            foreign_ratio=r.foreign_ratio,
            per=r.per,
            pbr=r.pbr,
            roe=r.roe,
            ev_ebitda=val.get(r.peer_stock_code, (None, None))[0],
            psr=val.get(r.peer_stock_code, (None, None))[1],
            **{f"{k}_score": v for k, v in scores.get(r.peer_stock_code, {}).items()},
        )
        for r in rows
    ]


@router.get("/{code}/coverage/reports", response_model=list[ReportCard])
def company_coverage_reports(code: str, db: Session = Depends(get_session)) -> list[ReportCard]:
    """종목 커버리지 리포트 목록(종목 + 소속 산업), 최근 1년. 커버리지 타일 클릭 시 모달이 사용."""
    since = date.today() - timedelta(days=_COVERAGE_DAYS)
    return [
        ReportCard(
            id=r.id,
            category=r.category,
            title=r.title,
            broker=r.broker,
            name=r.stock_name or r.industry_name,
            summary=(a.summary if a else ""),
            sentiment=(a.sentiment.value if a and a.sentiment else "HOLD"),
            rationale=(a.rationale if a else ""),
            published_date=r.published_date,
            has_pdf=bool(r.pdf_object_key),
        )
        for r, a in company_service.coverage_reports(db, code, since)
    ]


@router.get("/{code}/timeline", response_model=TimelineCacheResponse)
def company_timeline(
    code: str,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> TimelineCacheResponse:
    end = to or datetime.now().date()
    begin = from_ or (end - timedelta(days=company_service._TIMELINE_WINDOW_DAYS))

    _TIMELINE_CACHE_TTL_HOURS = 24

    # 1) 캐시 조회 — TTL 이내인 경우만 반환. 오래되면 재구축한다.
    cache = company_service.get_timeline_cache(db, code)
    if cache is not None and cache.cached_at is not None:
        age = datetime.now(UTC) - cache.cached_at
        if age < timedelta(hours=_TIMELINE_CACHE_TTL_HOURS):
            return TimelineCacheResponse(
                items=[TimelineItem(**item) for item in cache.payload["items"]],
                cached_at=cache.cached_at,
                last_disclosure_date=cache.last_disclosure_date,
            )

    # 2) 캐시 미스/TTL 만료 → DB 에서 빌드
    company_service.sync_disclosures_safe(db, code, begin, end)
    items, last_disc_date = company_service.build_timeline_items(db, code, begin, end)
    company_service.store_timeline_cache(db, code, items, last_disc_date)

    return TimelineCacheResponse(
        items=[TimelineItem(**item) for item in items],
        cached_at=None,
        last_disclosure_date=last_disc_date,
    )


@router.post("/{code}/timeline/refresh", response_model=TimelineCacheResponse)
def company_timeline_refresh(
    code: str,
    db: Session = Depends(get_session),
) -> TimelineCacheResponse:
    """DART 신규 공시 확인 → 캐시 재구축 → 갱신된 타임라인 반환.

    프론트가 타임라인을 먼저 보여준 뒤, 이 엔드포인트를 호출해 최신 공시를
    백그라운드에서 확인한다. refresh 는 last_disclosure_date 이후 공시만
    조회하므로 빠르다(보통 0건 또는 소수).
    """
    items, last_disc_date = company_service.refresh_timeline_cache(db, code)
    return TimelineCacheResponse(
        items=[TimelineItem(**item) for item in items],
        cached_at=datetime.now(),
        last_disclosure_date=last_disc_date,
    )


@router.get("/{code}/growth", response_model=CompanyGrowth)
def company_growth(code: str, db: Session = Depends(get_session)) -> CompanyGrowth:
    """종목 성장지표 — universe 스냅샷(시총·모멘텀) + growth_metric(YoY) + 커버리지."""
    u = company_service.growth_snapshot(db, code)
    g = company_service.growth_metric(db, code)
    cov_count, buy_count = company_service.coverage_counts(
        db, code, date.today() - timedelta(days=_COVERAGE_DAYS)
    )
    # 성장지표는 스냅샷에 없는 종목이면 이름도 리포트 폴백만(레거시 동작 보존 — 시세 필드와 일관).
    name = u.stock_name if u else company_service.report_stock_name(db, code)
    return CompanyGrowth(
        stock_code=code,
        stock_name=name,
        market=u.market if u else None,
        market_cap=u.market_cap if u else None,
        close_price=u.close_price if u else None,
        change_pct=u.change_pct if u else None,
        momentum_3m=u.momentum_3m if u else None,
        revenue_yoy=g.revenue_yoy if g else None,
        op_yoy=g.op_yoy if g else None,
        op_turnaround=bool(g.op_turnaround) if g else False,
        op_status=g.op_status if g else None,
        op_margin_delta=g.op_margin_delta if g else None,
        eps_yoy=g.eps_yoy if g else None,
        net_status=g.net_status if g else None,
        net_margin_delta=g.net_margin_delta if g else None,
        ebitda_status=g.ebitda_status if g else None,
        ebitda_margin_delta=g.ebitda_margin_delta if g else None,
        period=g.period if g else None,
        coverage_count=cov_count,
        buy_ratio=round(buy_count / cov_count, 2) if cov_count else None,
    )
