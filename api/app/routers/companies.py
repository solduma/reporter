"""기업 분석 페이지용 라우터 — 주가 봉차트 + 종목 요약. (재무/피어/타임라인은 후속 단계)"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Broadcast,
    Disclosure,
    Financial,
    GrowthMetric,
    Peer,
    PriceCandle,
    Report,
    ReportAnalysis,
    SectorTheme,
    SectorThemeStock,
    Sentiment,
    SyncState,
    UniverseSnapshot,
)
from app.db.session import get_session
from app.schemas import (
    AnalysisAxis,
    CandlePoint,
    CompanyAnalysis,
    CompanyGrowth,
    CompanySummary,
    FinancialPeriodOut,
    PeerOut,
    StockSearchHit,
    TimelineItem,
    TopDownView,
)
from app.services import (
    analysis,
    analysis_comment,
    candle_service,
    dart_ingest,
    financials_backfill,
    quote,
    sync_state,
    technicals,
    valuation_ingest,
)

router = APIRouter(prefix="/api/companies", tags=["companies"])


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
    as_of = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
    if as_of is None:
        return []

    U = UniverseSnapshot
    like = f"%{q}%"
    prefix = f"{q}%"
    # DB 는 후보만 넓게(코드 prefix OR 이름 부분일치) 뽑고, 랭킹은 파이썬에서 한다.
    # (Postgres 정규식/case 정렬을 SQL 에 섞지 않아 테스트·유지보수가 쉽다.)
    rows = db.execute(
        select(U.stock_code, U.stock_name, U.market, U.market_cap)
        .where(
            U.snapshot_date == as_of,
            U.stock_type == "stock",
            ~U.stock_name.op("~")(r"우[A-C]?$"),  # 우선주 제외(스크리너 관례)
            or_(U.stock_code.ilike(prefix), U.stock_name.ilike(like)),
        )
        .limit(200)  # 랭킹 전 후보 상한(대중적 접두어도 흡수)
    ).all()

    # 랭크 오름차순 → 동순위는 시총 내림차순(대형주 먼저).
    ranked = sorted(rows, key=lambda r: (_search_rank(q, r[0], r[1]), -(r[3] or 0)))
    return [
        StockSearchHit(stock_code=c, stock_name=n, market=m, market_cap=cap)
        for c, n, m, cap in ranked[:limit]
    ]


def _resolve_stock_name(db: Session, code: str) -> str | None:
    """종목명 조회 — 유니버스 스냅샷(전 종목 보유) 우선, 없으면 리포트 폴백.

    리포트가 없는 종목도 이름이 나오도록 통일한다(summary/analysis/growth 공통).
    """
    name = db.scalar(
        select(UniverseSnapshot.stock_name)
        .where(UniverseSnapshot.stock_code == code, UniverseSnapshot.stock_name.is_not(None))
        .order_by(UniverseSnapshot.snapshot_date.desc())
        .limit(1)
    )
    if name:
        return name
    return db.scalar(
        select(Report.stock_name)
        .where(Report.stock_code == code, Report.stock_name.is_not(None))
        .order_by(Report.published_date.desc())
        .limit(1)
    )


@router.get("/{code}/summary", response_model=CompanySummary)
def company_summary(code: str, db: Session = Depends(get_session)) -> CompanySummary:
    return CompanySummary(stock_code=code, stock_name=_resolve_stock_name(db, code))


@router.get("/{code}/candles", response_model=list[CandlePoint])
def company_candles(
    code: str,
    bg: BackgroundTasks,
    tf: str = Query(default="day", pattern="^(30m|day|week|month)$"),
    db: Session = Depends(get_session),
) -> list[CandlePoint]:
    """종목 봉 — DB 우선 즉시 반환. 뒤처졌으면 백그라운드 증분 갱신을 예약한다."""
    if tf == "30m":
        # 30분봉은 '최근 2주' 창만 반환. DB 우선(비면 최초 1회 조회) + 백그라운드 최신화.
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


def _ensure_day_candles(db: Session, code: str) -> list[PriceCandle]:
    """일봉을 DB 우선으로 확보한다(기술 지표 계산용). 비었을 때만 최초 1회 동기 조회.

    이후 최신화는 차트 조회(/candles·/chart)의 백그라운드 증분이 담당한다. 지표는 다일
    누적값이라 하루 지연은 무해하므로 매 분석마다 외부를 타지 않는다.
    """
    return candle_service.ensure_periodic(db, code, "day")


@router.get("/{code}/analysis", response_model=CompanyAnalysis)
def company_analysis(
    code: str, bg: BackgroundTasks, db: Session = Depends(get_session)
) -> CompanyAnalysis:
    """테크노펀더멘탈 종합 — 성장·기술적 추세·탑다운."""
    settings = get_settings()
    # 기술 지표가 쓰는 일봉이 뒤처졌으면 백그라운드로 증분 갱신(조회는 DB 로 즉시 진행).
    if candle_service.is_stale(db, code, "day"):
        bg.add_task(candle_service.refresh_periodic, code, "day")
    snap = db.scalars(
        select(UniverseSnapshot)
        .where(UniverseSnapshot.stock_code == code)
        .order_by(UniverseSnapshot.snapshot_date.desc())
        .limit(1)
    ).first()
    name = (snap.stock_name if snap else None) or _resolve_stock_name(db, code)
    market = snap.market if snap else None

    # 성장 축 — GrowthMetric.
    g = db.scalars(select(GrowthMetric).where(GrowthMetric.stock_code == code)).first()
    growth_sc = analysis.growth_score(
        g.revenue_yoy if g else None,
        g.op_yoy if g else None,
        g.op_turnaround if g else False,
    )
    growth_axis = AnalysisAxis(
        key="growth",
        label="성장",
        score=growth_sc,
        metrics=[
            {"label": "매출 YoY", "value": _pct(g.revenue_yoy) if g else "—"},
            {"label": "영업이익 YoY", "value": _pct(g.op_yoy) if g else "—"},
            {"label": "흑자전환", "value": "예" if (g and g.op_turnaround) else "아니오"},
        ],
    )

    # 기술 축 — 일봉 지표.
    candles = _ensure_day_candles(db, code)
    tech = technicals.compute(candles)
    tech_axis = AnalysisAxis(
        key="technical",
        label="기술적 추세",
        score=tech.trend_score,
        metrics=[
            {"label": "52주 고점 근접", "value": f"{tech.near_high_pct}%" if tech.near_high_pct else "—"},
            {"label": "이평 정배열", "value": _yn(tech.ma_aligned)},
            {"label": "거래량비", "value": f"{tech.vol_ratio}x" if tech.vol_ratio else "—"},
            {"label": "3개월 수익률", "value": f"{tech.return_3m}%" if tech.return_3m is not None else "—"},
        ],
    )

    # 탑다운 축 — 종목이 속한 섹터의 국내/미국 수급 flow(미국 선행) + 국내 지수.
    theme_names = list(
        db.scalars(
            select(SectorTheme.name)
            .join(SectorThemeStock, SectorThemeStock.judal_idx == SectorTheme.judal_idx)
            .where(SectorThemeStock.stock_code == code)
        ).all()
    )
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

    # LLM 종합 코멘트 — 캐시 우선. 미스면 백그라운드 생성(응답은 pending 으로 즉시 반환,
    # 프론트가 재조회로 채운다). 동기 생성(~17초) 제거로 화면이 스코어와 함께 즉시 뜬다.
    axes_dump = [a.model_dump() for a in axes]
    comment = None
    comment_pending = False
    if settings.ollama_api_key:
        h = analysis_comment.inputs_hash(axes_dump)
        comment = analysis_comment.get_cached(db, code, h)
        if comment is None:
            comment_pending = True
            bg.add_task(analysis_comment.generate_and_store, code, name or code, axes_dump, h)

    return CompanyAnalysis(
        stock_code=code,
        stock_name=name,
        market=market,
        overall_score=overall_sc,
        axes=axes,
        topdown=TopDownView(**topdown_view),
        comment=comment,
        comment_pending=comment_pending,
    )


def _pct(v: float | None) -> str:
    return f"{v * 100:+.0f}%" if v is not None else "—"


def _yn(v: bool | None) -> str:
    return "예" if v is True else "아니오" if v is False else "—"


def _signed(ratio: str, rising: bool | None) -> str:
    r = (ratio or "").strip()
    if not r:
        return "—"
    if r.startswith(("+", "-")):
        return f"{r}%"
    sign = "+" if rising is True else "-" if rising is False else ""
    return f"{sign}{r}%"


def _flow_label(score: float | None) -> str:
    """자금유입 강도(0~100)를 '강함/보통/약함' 라벨 + 점수로."""
    if score is None:
        return "—"
    tag = "강함" if score >= 60 else "보통" if score >= 40 else "약함"
    return f"{tag} {score:.0f}"


# 동일업종 테이블의 한글 행 라벨 → peers 컬럼
_PEER_FIELDS = {
    "price": "현재가",
    "market_cap": "시가총액(억)",
    "foreign_ratio": "외국인비율(%)",
    "per": "PER(%)",
    "pbr": "PBR(배)",
    "roe": "ROE(%)",
}


# 재무·peers 스크랩 캐시 TTL — 분기 재무는 하루 1회면 충분(장중 잦은 갱신 불필요).
_FINANCIALS_TTL = timedelta(hours=12)
_PEERS_TTL = timedelta(hours=12)


def company_financials_rows(db: Session, code: str) -> list[Financial]:
    """저장된 재무 기간을 정렬해 반환(외부 호출 없음). 다른 서비스에서도 재사용."""
    return list(
        db.scalars(
            select(Financial).where(Financial.stock_code == code).order_by(Financial.period)
        ).all()
    )


@router.get("/{code}/financials", response_model=list[FinancialPeriodOut])
def company_financials(
    code: str, bg: BackgroundTasks, db: Session = Depends(get_session)
) -> list[FinancialPeriodOut]:
    rows = company_financials_rows(db, code)
    # DB 우선: 저장분이 없거나(최초) TTL 만료면 백그라운드로 스크랩·산출한다. 응답은 즉시.
    if not rows:
        _sync_financials(db, code)  # 최초만 동기 조회로 화면을 채운다
        rows = company_financials_rows(db, code)
    elif not sync_state.is_fresh(db, "financials", code, _FINANCIALS_TTL):
        bg.add_task(_sync_financials_bg, code)
    # EV/EBITDA·PSR(DART, 24h TTL)은 valuation_ingest 가 자체 게이트 — 항상 백그라운드 예약.
    bg.add_task(_sync_valuation_bg, code)
    # 10년 재무·밸류 백필은 종목당 1회만(야간 배치가 나머지를 채움). 아직이면 백그라운드로.
    if not _financials_10y_done(db, code):
        bg.add_task(_backfill_financials_10y_bg, code)

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
        )
        for r in rows
    ]


def _sync_financials(db: Session, code: str) -> None:
    """네이버 재무 스크랩 → financials upsert + sync_state 마킹. 예외는 호출측이 처리.

    per/pbr/psr(밸류)은 financials_backfill(총액·분할무관 방법론)이 전 분기를 일관되게
    소유하므로 여기서 덮어쓰지 않는다(방법론 혼재로 밴드 시계열이 튀는 것 방지). 네이버는
    operating_income/roe/추정치(E) 등 백필이 못 만드는 필드를 채운다.
    """
    session = requests.Session()
    fetched = quote.fetch_financials(code, session)
    for f in fetched:
        stmt = insert(Financial).values(
            stock_code=code, period=f.period, is_estimate=f.is_estimate,
            revenue=f.revenue, operating_income=f.operating_income, net_income=f.net_income,
            eps=f.eps, bps=f.bps, roe=f.roe,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_financial",
            set_={
                c: getattr(stmt.excluded, c)
                for c in ("is_estimate", "revenue", "operating_income", "net_income",
                          "eps", "bps", "roe")
            },
        )
        db.execute(stmt)
    sync_state.mark(db, "financials", code)
    db.commit()


def _sync_financials_bg(code: str) -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        _sync_financials(db, code)
    except Exception as e:
        import logging

        db.rollback()
        logging.getLogger(__name__).warning("financials sync failed %s: %s", code, e)
    finally:
        db.close()


def _sync_valuation_bg(code: str) -> None:
    """백그라운드 EV/EBITDA·PSR 산출 — 자체 세션."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        valuation_ingest.sync_valuation(db, get_settings(), code)
    except Exception as e:  # 백그라운드 실패가 조회를 깨지 않도록
        import logging

        db.rollback()
        logging.getLogger(__name__).warning("valuation sync failed %s: %s", code, e)
    finally:
        db.close()


def _financials_10y_done(db: Session, code: str) -> bool:
    """이 종목이 10년 재무 백필을 이미 마쳤는지(sync_state financials_10y)."""
    return bool(
        db.scalar(
            select(SyncState.id).where(
                SyncState.domain == "financials_10y", SyncState.stock_code == code
            )
        )
    )


def _backfill_financials_10y_bg(code: str) -> None:
    """백그라운드 10년 재무·밸류 백필 — 종목당 1회(야간 배치가 나머지). 자체 세션."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        if financials_backfill.backfill_stock(db, get_settings(), code):
            sync_state.mark(db, "financials_10y", code)
            db.commit()
    except Exception as e:  # 백그라운드 실패가 조회를 깨지 않도록
        import logging

        db.rollback()
        logging.getLogger(__name__).warning("financials 10y backfill failed %s: %s", code, e)
    finally:
        db.close()


@router.get("/{code}/peers", response_model=list[PeerOut])
def company_peers(
    code: str, bg: BackgroundTasks, db: Session = Depends(get_session)
) -> list[PeerOut]:
    rows = list(db.scalars(select(Peer).where(Peer.base_stock_code == code).order_by(Peer.id)).all())
    # DB 우선: 없으면 최초 동기 조회, TTL 만료면 백그라운드 갱신.
    if not rows:
        _sync_peers(db, code)
        rows = list(
            db.scalars(select(Peer).where(Peer.base_stock_code == code).order_by(Peer.id)).all()
        )
    elif not sync_state.is_fresh(db, "peers", code, _PEERS_TTL):
        bg.add_task(_sync_peers_bg, code)
    # EV/EBITDA·PSR 은 네이버 동일업종 테이블에 없어, 각 peer 의 최근 Financial(DART 산출)에서 채운다.
    val = _peer_valuations(db, [r.peer_stock_code for r in rows])
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


def _peer_valuations(db: Session, codes: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """peer 종목들의 최근(추정 아닌) 분기 ev_ebitda·psr 을 표시문자열로 반환한다.

    Financial 은 종목당 여러 분기가 있으므로 ev_ebitda/psr 이 채워진 가장 최신 분기를 고른다.
    """
    if not codes:
        return {}
    rows = db.scalars(
        select(Financial)
        .where(Financial.stock_code.in_(codes), Financial.is_estimate.is_(False))
        .order_by(Financial.period.desc())
    ).all()
    out: dict[str, tuple[str | None, str | None]] = {}
    for r in rows:
        if r.stock_code in out:
            continue  # 이미 더 최신 분기를 잡음(period desc)
        if r.ev_ebitda is not None or r.psr is not None:
            ev = f"{r.ev_ebitda:.1f}" if r.ev_ebitda is not None else None
            psr = f"{r.psr:.2f}" if r.psr is not None else None
            out[r.stock_code] = (ev, psr)
    return out


def _sync_peers(db: Session, code: str) -> None:
    """네이버 동일업종 스크랩 → peers upsert + sync_state 마킹."""
    session = requests.Session()
    fetched = quote.fetch_peers(code, session)
    for p in fetched:
        vals = {field: p.values.get(label) for field, label in _PEER_FIELDS.items()}
        stmt = insert(Peer).values(
            base_stock_code=code, peer_stock_code=p.stock_code, peer_name=p.name, **vals
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_peer",
            set_={"peer_name": stmt.excluded.peer_name, **{f: getattr(stmt.excluded, f) for f in _PEER_FIELDS}},
        )
        db.execute(stmt)
    sync_state.mark(db, "peers", code)
    db.commit()


def _sync_peers_bg(code: str) -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        _sync_peers(db, code)
    except Exception as e:
        import logging

        db.rollback()
        logging.getLogger(__name__).warning("peers sync failed %s: %s", code, e)
    finally:
        db.close()


@router.get("/{code}/timeline", response_model=list[TimelineItem])
def company_timeline(
    code: str,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> list[TimelineItem]:
    end = to or datetime.now().date()
    begin = from_ or (end - timedelta(days=90))

    # DART 공시 동기화(cache-aside). 키 없으면 조용히 건너뜀.
    settings = get_settings()
    if settings.dart_api_key:
        try:
            dart_ingest.sync_disclosures(db, settings, code, begin, end)
        except Exception as e:  # 공시 동기화 실패가 리포트 타임라인까지 막지 않도록
            import logging

            # 동기화가 세션을 미완결 상태로 남겼을 수 있어 롤백한다. 안 하면 이후 쿼리가
            # PendingRollbackError 로 터져 타임라인 전체가 500 이 된다.
            db.rollback()
            logging.getLogger(__name__).warning("disclosure sync failed %s: %s", code, e)

    items: list[TimelineItem] = []

    # 리포트(종목분석)
    report_rows = db.execute(
        select(Report, ReportAnalysis)
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(
            Report.stock_code == code,
            Report.published_date >= begin,
            Report.published_date <= end,
        )
    ).all()
    for r, a in report_rows:
        items.append(
            TimelineItem(
                type="report",
                date=r.published_date,
                title=r.title,
                source=r.broker,
                sentiment=a.sentiment.value,
                rationale=a.rationale,
                link=r.read_url,
                report_id=r.id,
            )
        )

    # DART 공시
    disc_rows = db.scalars(
        select(Disclosure).where(
            Disclosure.stock_code == code,
            Disclosure.rcept_dt >= begin,
            Disclosure.rcept_dt <= end,
        )
    ).all()
    for d in disc_rows:
        items.append(
            TimelineItem(
                type="disclosure",
                date=d.rcept_dt,
                title=d.report_nm,
                source=d.flr_nm,
                sentiment=d.sentiment.value,
                rationale=d.rationale,
                link=d.dart_url,
                report_id=None,
            )
        )

    # 이 종목을 언급한 텔레그램 브로드캐스트(오후 리서치·미장·종합 등)
    bc_rows = db.scalars(
        select(Broadcast).where(
            Broadcast.stock_codes.contains([code]),
            Broadcast.ref_date >= begin,
            Broadcast.ref_date <= end,
        )
    ).all()
    for b in bc_rows:
        items.append(
            TimelineItem(
                type="broadcast",
                date=b.ref_date,
                title=b.title,
                source="텔레그램 브리핑",
                sentiment="HOLD",
                rationale=_snippet(b.body),
                link=None,
                report_id=None,
                broadcast_id=b.id,
                kind=b.kind.value,
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
    snap_date = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
    u = db.scalar(
        select(UniverseSnapshot).where(
            UniverseSnapshot.snapshot_date == snap_date, UniverseSnapshot.stock_code == code
        )
    ) if snap_date else None
    g = db.scalar(select(GrowthMetric).where(GrowthMetric.stock_code == code))

    since = date.today() - timedelta(days=90)
    cov = db.execute(
        select(
            func.count(Report.id),
            func.sum(case((ReportAnalysis.sentiment == Sentiment.BUY, 1), else_=0)),
        )
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(Report.stock_code == code, Report.published_date >= since)
    ).one()
    cov_count = int(cov[0] or 0)
    buy_count = int(cov[1] or 0)

    name = u.stock_name if u else db.scalar(
        select(Report.stock_name).where(Report.stock_code == code, Report.stock_name.is_not(None)).limit(1)
    )
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
