"""기업 분석 페이지용 라우터 — 검색·요약·봉·분석·재무·피어·타임라인·성장지표.

데이터 접근·스크랩·백필은 services/company_service 가 담당하고, 여기선 쿼리 파라미터를 받아
결과를 DTO(AnalysisAxis·TimelineItem 등)로 조립한다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.domain import analysis_scoring, judgment, stage, technicals
from app.schemas import (
    AnalysisAxis,
    CandlePoint,
    CompanyAnalysis,
    CompanyGrowth,
    CompanySummary,
    CompanyTrend,
    FinancialPeriodOut,
    JudgmentOut,
    PeerOut,
    RelStrengthPoint,
    StageFrame,
    StageSegment,
    StockSearchHit,
    TimelineItem,
    TopDownView,
)
from app.services import (
    analysis,
    analysis_comment,
    candle_service,
    company_service,
    today_service,
    trend,
)

router = APIRouter(prefix="/api/companies", tags=["companies"])

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
            CandlePoint(t=r.bar_ts.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume)
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
    code: str, bg: BackgroundTasks, db: Session = Depends(get_session)
) -> CompanyAnalysis:
    """테크노펀더멘탈 종합 — 성장·기술적 추세·탑다운."""
    settings = get_settings()
    if candle_service.is_stale(db, code, "day"):
        bg.add_task(candle_service.refresh_periodic, code, "day")
    snap = company_service.latest_snapshot(db, code)
    name = (snap.stock_name if snap else None) or company_service.resolve_stock_name(db, code)
    market = snap.market if snap else None

    # 성장 축 — GrowthMetric.
    g = company_service.growth_metric(db, code)
    growth_sc = analysis.growth_score(
        g.revenue_yoy if g else None,
        g.op_yoy if g else None,
        g.op_turnaround if g else False,
    )
    # 성장축은 점수 해석만 보여준다 — 원시 YoY 수치는 '성장 지표 스냅샷'이 단일 소유(중복 제거).
    growth_axis = AnalysisAxis(
        key="growth",
        label="성장",
        score=growth_sc,
        metrics=[
            {"label": "성장 등급", "value": _grade(growth_sc)},
            {"label": "흑자전환", "value": "예" if (g and g.op_turnaround) else "아니오"},
        ],
    )

    # 기술 축 — 일봉 지표 + 와인스타인 중기 국면.
    candles = company_service.ensure_day_candles(db, code)
    tech = technicals.compute(candles)
    mid_stage = stage.classify([c.close for c in candles], stage.FRAME_PERIODS["mid"])
    tech_axis = AnalysisAxis(
        key="technical",
        label="기술적 추세",
        score=tech.trend_score,
        metrics=[
            {"label": "와인스타인 국면", "value": mid_stage.label or "—"},
            {"label": "RS Rating", "value": f"{snap.rs_rating}" if snap and snap.rs_rating else "—"},
            {"label": "52주 고점 근접", "value": f"{tech.near_high_pct}%" if tech.near_high_pct else "—"},
            {"label": "이평 정배열", "value": _yn(tech.ma_aligned)},
            {"label": "거래량비", "value": f"{tech.vol_ratio}x" if tech.vol_ratio else "—"},
            {"label": "3개월 수익률", "value": f"{tech.return_3m}%" if tech.return_3m is not None else "—"},
        ],
    )

    # 탑다운 축 — 종목이 속한 섹터의 국내/미국 수급 flow(미국 선행) + 국내 지수.
    theme_names = company_service.theme_names(db, code)
    topdown_view, topdown_sc = analysis.build_topdown(theme_names, market)
    kr_sec = topdown_view["kr_sector"]
    us_sec = topdown_view["us_sector"]
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
        ]
        + [
            {"label": k["name"], "value": _signed(k["change_ratio"], k["rising"])}
            for k in topdown_view["kr_indices"]
        ],
    )

    axes = [growth_axis, tech_axis, topdown_axis]
    overall_sc = analysis.overall([growth_sc, tech.trend_score, topdown_sc])

    # 판단 요약(강점·약점·확인 + 신호) — 점수의 규칙 기반 요약(자문 아님, 프론트가 면책 노출).
    j = judgment.summarize(
        overall_sc,
        {"growth": growth_sc, "technical": tech.trend_score, "topdown": topdown_sc},
    )
    judgment_out = JudgmentOut(
        signal=j.signal,
        signal_label=j.signal_label,
        strengths=j.strengths,
        weaknesses=j.weaknesses,
        checks=j.checks,
    )

    # LLM 종합 코멘트 — 3축 + 시장 맥락·정성 재료를 함께 종합. 캐시 우선, 미스면 백그라운드 생성.
    axes_dump = [a.model_dump() for a in axes]
    comment = None
    comment_pending = False
    if settings.ollama_api_key:
        ctx = _comment_context(db, code)
        h = analysis_comment.inputs_hash(axes_dump, ctx)
        comment = analysis_comment.get_cached(db, code, h)
        if comment is None:
            comment_pending = True
            bg.add_task(
                analysis_comment.generate_and_store, code, name or code, axes_dump, h, ctx
            )

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


@router.get("/{code}/trend", response_model=CompanyTrend)
def company_trend(
    code: str, bg: BackgroundTasks, db: Session = Depends(get_session)
) -> CompanyTrend:
    """기술적 추세 — 와인스타인 국면(단/중/장기) + Mansfield 상대강도(지수 대비)."""
    if candle_service.is_stale(db, code, "day"):
        bg.add_task(candle_service.refresh_periodic, code, "day")
    snap = company_service.latest_snapshot(db, code)
    market = snap.market if snap else None

    result = trend.compute_trend(db, code, market)
    return CompanyTrend(
        stock_code=code,
        benchmark=result.benchmark,
        stages=[
            StageFrame(
                frame=frame,
                period=stage.FRAME_PERIODS[frame],
                stage=result.stages[frame].stage,
                label=result.stages[frame].label,
                ma_dir=result.stages[frame].ma_dir,
            )
            for frame in ("short", "mid", "long")
        ],
        stage_segments=[
            StageSegment(stage=s["stage"], from_date=s["from"], to_date=s["to"])
            for s in result.stage_segments
        ],
        rs_series=[RelStrengthPoint(date=p.date, value=p.value) for p in result.rs.series],
        rs_latest=result.rs.latest,
        rs_outperforming=result.rs.outperforming,
        rs_rating=snap.rs_rating if snap else None,
    )


def _yn(v: bool | None) -> str:
    return "예" if v is True else "아니오" if v is False else "—"


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


def _signed(ratio: str, rising: bool | None) -> str:
    r = (ratio or "").strip()
    if not r:
        return "—"
    if r.startswith(("+", "-")):
        return f"{r}%"
    sign = "+" if rising is True else "-" if rising is False else ""
    return f"{sign}{r}%"


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
    # EV/EBITDA·PSR 은 네이버 동일업종 테이블에 없어, 각 peer 의 최근 Financial(DART 산출)에서 채운다.
    val = company_service.peer_valuations(db, [r.peer_stock_code for r in rows])
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
        )
        for r in rows
    ]


@router.get("/{code}/timeline", response_model=list[TimelineItem])
def company_timeline(
    code: str,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> list[TimelineItem]:
    end = to or datetime.now().date()
    begin = from_ or (end - timedelta(days=90))

    company_service.sync_disclosures_safe(db, code, begin, end)  # DART 공시 cache-aside

    items: list[TimelineItem] = []
    for r, a in company_service.timeline_reports(db, code, begin, end):
        items.append(
            TimelineItem(
                type="report", date=r.published_date, title=r.title, source=r.broker,
                sentiment=a.sentiment.value, rationale=a.rationale, link=r.read_url, report_id=r.id,
            )
        )
    for d in company_service.timeline_disclosures(db, code, begin, end):
        items.append(
            TimelineItem(
                type="disclosure", date=d.rcept_dt, title=d.report_nm, source=d.flr_nm,
                sentiment=d.sentiment.value, rationale=d.rationale, link=d.dart_url, report_id=None,
            )
        )
    for b in company_service.timeline_broadcasts(db, code, begin, end):
        items.append(
            TimelineItem(
                type="broadcast", date=b.ref_date, title=b.title, source="텔레그램 브리핑",
                sentiment="HOLD", rationale=_snippet(b.body), link=None, report_id=None,
                broadcast_id=b.id, kind=b.kind.value,
            )
        )

    items.sort(key=lambda x: x.date, reverse=True)  # 최신순
    return items


_TIMELINE_SNIPPET = 160


def _snippet(body: str) -> str:
    """브로드캐스트 본문에서 헤더·구분선을 제외한 앞부분 미리보기."""
    lines = [ln for ln in body.splitlines() if ln.strip() and set(ln.strip()) != {"─"}]
    text = " ".join(lines[1:]) if len(lines) > 1 else " ".join(lines)
    return text[:_TIMELINE_SNIPPET] + ("…" if len(text) > _TIMELINE_SNIPPET else "")


@router.get("/{code}/growth", response_model=CompanyGrowth)
def company_growth(code: str, db: Session = Depends(get_session)) -> CompanyGrowth:
    """종목 성장지표 — universe 스냅샷(시총·모멘텀) + growth_metric(YoY) + 커버리지."""
    u = company_service.growth_snapshot(db, code)
    g = company_service.growth_metric(db, code)
    cov_count, buy_count = company_service.coverage_counts(db, code, date.today() - timedelta(days=90))
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
        period=g.period if g else None,
        coverage_count=cov_count,
        buy_ratio=round(buy_count / cov_count, 2) if cov_count else None,
    )
