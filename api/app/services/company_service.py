"""종목 상세 페이지 조회·동기화 서비스 — 라우터가 쓰던 데이터 접근·스크랩·백필을 응용 계층으로.

라우터는 이 서비스가 돌려준 ORM/데이터로 DTO(AnalysisAxis·TimelineItem 등)를 조립만 한다.
외부 스크랩은 naver_quote 어댑터 위임, PER/PBR/PSR 은 financials_backfill, EV/EBITDA(정밀 D&A)는
report_ingest 가 각각 단일 소유한다(역사 시총 기준으로 통일).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.market import naver_quote as quote
from app.config import get_settings
from app.db.models import (
    Broadcast,
    Disclosure,
    Financial,
    FinancialStatement,
    GrowthMetric,
    Peer,
    Report,
    ReportAnalysis,
    SectorTheme,
    SectorThemeStock,
    Sentiment,
    SyncState,
    TimelineCache,
    UniverseSnapshot,
)
from app.db.session import SessionLocal
from app.services import (
    candle_service,
    dart_ingest,
    financials_backfill,
    report_ingest,
    sync_state,
    universe_ingest,
)
from app.services import (
    ontology as ontology_service,
)

logger = logging.getLogger(__name__)

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


# ── 종목명·검색 ────────────────────────────────────────────────────────
def resolve_stock_name(db: Session, code: str) -> str | None:
    """종목명 — 유니버스 스냅샷 우선, 없으면 리포트 폴백. 리포트 없는 종목도 이름이 나오게 통일."""
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


def search_candidates(db: Session, q: str) -> list[tuple]:
    """검색 후보(코드,명,시장,시총) 최대 200행. 코드 prefix OR 이름 부분일치, 최신 스냅샷 보통주."""
    as_of = universe_ingest.latest_snapshot_date(db)
    if as_of is None:
        return []
    U = UniverseSnapshot
    like, prefix = f"%{q}%", f"{q}%"
    return list(
        db.execute(
            select(U.stock_code, U.stock_name, U.market, U.market_cap)
            .where(
                U.snapshot_date == as_of,
                U.stock_type == "stock",
                ~U.stock_name.op("~")(r"우[A-C]?$"),  # 우선주 제외
                or_(U.stock_code.ilike(prefix), U.stock_name.ilike(like)),
            )
            .limit(200)
        ).all()
    )


# ── 분석(성장·탑다운 조회) ────────────────────────────────────────────
def latest_snapshot(db: Session, code: str) -> UniverseSnapshot | None:
    return db.scalars(
        select(UniverseSnapshot)
        .where(UniverseSnapshot.stock_code == code)
        .order_by(UniverseSnapshot.snapshot_date.desc())
        .limit(1)
    ).first()


def growth_metric(db: Session, code: str) -> GrowthMetric | None:
    return db.scalars(select(GrowthMetric).where(GrowthMetric.stock_code == code)).first()


def theme_names(db: Session, code: str) -> list[str]:
    """종목이 속한 judal 테마명 목록(탑다운 섹터 특정용)."""
    return list(
        db.scalars(
            select(SectorTheme.name)
            .join(SectorThemeStock, SectorThemeStock.judal_idx == SectorTheme.judal_idx)
            .where(SectorThemeStock.stock_code == code)
        ).all()
    )


def ensure_day_candles(db: Session, code: str):
    """일봉 DB 우선 확보(기술 지표용). 비었을 때만 최초 1회 동기 조회."""
    return candle_service.ensure_periodic(db, code, "day")


# ── 재무 ──────────────────────────────────────────────────────────────
def financials_rows(db: Session, code: str, fs_div: str | None = None) -> list[Financial]:
    """저장된 재무 기간 정렬 반환(외부 호출 없음). fs_div=None이면 전체(연결+별도)."""
    q = select(Financial).where(Financial.stock_code == code)
    if fs_div:
        q = q.where(Financial.fs_div == fs_div)
    return list(db.execute(q.order_by(Financial.period)).scalars().all())


def financial_statement_rows(
    db: Session, code: str, fs_div: str = "CFS"
) -> list[FinancialStatement]:
    """FinancialStatement 기간 정렬 반환."""
    return list(
        db.scalars(
            select(FinancialStatement)
            .where(
                FinancialStatement.stock_code == code,
                FinancialStatement.fs_div == fs_div,
            )
            .order_by(FinancialStatement.period)
        ).all()
    )


def fetch_and_store_financial_statements(
    db: Session, code: str, fs_div: str = "CFS"
) -> None:
    """DART 에서 재무제표를 조회해 FinancialStatement 테이블에 저장한다."""
    from datetime import date

    import requests

    from app.adapters import dart as dart_adapter
    from app.config import get_settings
    from app.db.models import CorpCodeMap
    from app.services.financials_backfill import _period_str, _target_year_quarters

    settings = get_settings()
    if not settings.dart_api_key:
        return
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        return

    today = date.today()
    yqs = _target_year_quarters(today)
    with requests.Session() as session:
        for year, q in yqs:
            full = dart_adapter.fetch_full_statements(
                settings.dart_api_key, corp_code, year, q, session
            )
            if not full:
                continue
            ontology_service.enrich_with_ontology_id(full)
            period = _period_str(year, q)
            stmt = insert(FinancialStatement).values(
                stock_code=code, period=period, fs_div=fs_div, data=full,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_financial_statement",
                set_={"data": stmt.excluded.data, "updated_at": func.now()},
            )
            db.execute(stmt)
    db.commit()


def fetch_financial_statements_bg(code: str, fs_div: str = "CFS") -> None:
    """백그라운드로 DART 재무제표 조회·저장. 자체 DB 세션 사용(lock 회피)."""
    db = SessionLocal()
    try:
        fetch_and_store_financial_statements(db, code, fs_div)
    except Exception as e:
        db.rollback()
        logger.warning("financial statements bg fetch failed %s: %s", code, e)
    finally:
        db.close()


def backfill_financial_statement_ontology_id(
    db: Session, code: str | None = None, limit: int | None = None
) -> int:
    """기존 FinancialStatement 행의 data JSONB 에 ontology_id 를 in-place 보강.

    DART 호출 없이 name 정규화만 수행(A1 영속화 도입 전 행 마이그레이션용).
    code 지정 시 해당 종목만, 미지정 시 전체. 갱신된 행 수 반환.
    """
    q = select(FinancialStatement)
    if code:
        q = q.where(FinancialStatement.stock_code == code)
    if limit:
        q = q.limit(limit)
    rows = list(db.scalars(q).all())
    updated = 0
    for row in rows:
        data = row.data or {}
        before = sum(1 for items in data.values() for item in items if item.get("ontology_id") is not None)
        ontology_service.enrich_with_ontology_id(data)
        after = sum(1 for items in data.values() for item in items if item.get("ontology_id") is not None)
        if after > before:
            row.data = data  # JSONB 재할당으로 dirty 마킹
            updated += 1
    if updated:
        db.commit()
    return updated


def latest_valuation(db: Session, code: str, fs_div: str | None = None) -> Financial | None:
    """가치 축용 최신 밸류에이션 Financial(비추정). fs_div=None이면 전체."""
    has_value = case((or_(Financial.per.is_not(None), Financial.pbr.is_not(None)), 0), else_=1)
    q = select(Financial).where(Financial.stock_code == code, Financial.is_estimate.is_(False))
    if fs_div:
        q = q.where(Financial.fs_div == fs_div)
    fin = db.execute(q.order_by(has_value.asc(), Financial.period.desc()).limit(1)).scalar_one_or_none()
    if fin is None:
        return None
    # 배당·EV/EBITDA 는 연간(.12)에만 있어(분기 최신 행엔 결측), 최신 연간값을 끌어와 보정한다.
    # 안 하면 가치 축에서 EV/EBITDA 가 항상 누락된다(분기 행이 최신으로 잡히므로).
    if fin.div_yield is None:
        fin.div_yield = _latest_annual_value(db, code, Financial.div_yield, fs_div)
    if fin.ev_ebitda is None:
        fin.ev_ebitda = _latest_annual_value(db, code, Financial.ev_ebitda, fs_div)
    return fin


def _latest_annual_value(db: Session, code: str, column, fs_div: str | None = None):
    """연간(.12) 비추정 행 중 해당 컬럼의 최신 유효값. 분기 최신 행에 없는 연간 지표 보정용."""
    q = select(column).where(
        Financial.stock_code == code,
        Financial.is_estimate.is_(False),
        column.is_not(None),
        Financial.period.like("%.12"),
    )
    if fs_div:
        q = q.where(Financial.fs_div == fs_div)
    return db.execute(q.order_by(Financial.period.desc()).limit(1)).scalar_one_or_none()


def financials_fresh(db: Session, code: str) -> bool:
    return sync_state.is_fresh(db, "financials", code, _FINANCIALS_TTL)


def financials_10y_done(db: Session, code: str) -> bool:
    return bool(
        db.scalar(
            select(SyncState.id).where(
                SyncState.domain == "financials_10y", SyncState.stock_code == code
            )
        )
    )


def report_10y_done(db: Session, code: str) -> bool:
    return bool(
        db.scalar(
            select(SyncState.id).where(
                SyncState.domain == "report_10y", SyncState.stock_code == code
            )
        )
    )


def sync_financials(db: Session, code: str) -> None:
    """네이버 재무 스크랩 → financials upsert + sync_state 마킹.

    per/pbr/psr(밸류)은 financials_backfill 이 전 분기를 일관되게 소유하므로 여기서 덮어쓰지
    않는다. 네이버는 operating_income/roe/추정치(E) 등 백필이 못 만드는 필드를 채운다.
    """
    import requests

    session = requests.Session()
    fetched = quote.fetch_financials(code, session)
    for f in fetched:
        stmt = insert(Financial).values(
            stock_code=code,
            period=f.period,
            fs_div="CFS",
            is_estimate=f.is_estimate,
            revenue=f.revenue,
            operating_income=f.operating_income,
            net_income=f.net_income,
            eps=f.eps,
            bps=f.bps,
            roe=f.roe,
            dps=f.dps,
            div_yield=f.div_yield,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_financial",
            set_={
                c: getattr(stmt.excluded, c)
                for c in (
                    "is_estimate",
                    "revenue",
                    "operating_income",
                    "net_income",
                    "eps",
                    "bps",
                    "roe",
                    "dps",
                    "div_yield",
                )
            },
        )
        db.execute(stmt)
    sync_state.mark(db, "financials", code)
    db.commit()


def sync_financials_bg(code: str) -> None:
    db = SessionLocal()
    try:
        sync_financials(db, code)
    except Exception as e:
        db.rollback()
        logger.warning("financials sync failed %s: %s", code, e)
    finally:
        db.close()


def backfill_reports_bg(code: str) -> None:
    """백그라운드 보고서 원문 백필 — EV/EBITDA(정밀 D&A·역사시총) 산출. 종목당 1회. 자체 세션."""
    db = SessionLocal()
    try:
        if report_ingest.backfill_stock(db, get_settings(), code):
            sync_state.mark(db, "report_10y", code)
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("report backfill failed %s: %s", code, e)
    finally:
        db.close()


def backfill_financials_10y_bg(code: str) -> None:
    """백그라운드 10년 재무·밸류 백필 — 종목당 1회(야간 배치가 나머지). 자체 세션."""
    db = SessionLocal()
    try:
        if financials_backfill.backfill_stock(db, get_settings(), code):
            sync_state.mark(db, "financials_10y", code)
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("financials 10y backfill failed %s: %s", code, e)
    finally:
        db.close()


# ── 동일업종(peers) ────────────────────────────────────────────────────
def peers_rows(db: Session, code: str) -> list[Peer]:
    return list(
        db.scalars(select(Peer).where(Peer.base_stock_code == code).order_by(Peer.id)).all()
    )


def peers_fresh(db: Session, code: str) -> bool:
    return sync_state.is_fresh(db, "peers", code, _PEERS_TTL)


def peer_valuations(db: Session, codes: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """peer 종목들의 최근(추정 아닌) ev_ebitda·psr 표시문자열. period desc 로 최신 채움.

    ev_ebitda 는 연간(.12)에만, psr 은 분기에도 있어 서로 다른 period 에 산다. 한 지표가 있는
    첫 행에서 종목을 확정하면 다른 지표를 놓치므로, 두 지표를 독립 dict 로 각각 최신값 채운다.
    """
    if not codes:
        return {}
    rows = db.scalars(
        select(Financial)
        .where(Financial.stock_code.in_(codes), Financial.is_estimate.is_(False))
        .order_by(Financial.period.desc())
    ).all()
    ev_map: dict[str, str] = {}
    psr_map: dict[str, str] = {}
    for r in rows:
        if r.ev_ebitda is not None and r.stock_code not in ev_map:
            ev_map[r.stock_code] = f"{r.ev_ebitda:.1f}"
        if r.psr is not None and r.stock_code not in psr_map:
            psr_map[r.stock_code] = f"{r.psr:.2f}"
    codes_with_val = set(ev_map) | set(psr_map)
    return {c: (ev_map.get(c), psr_map.get(c)) for c in codes_with_val}


def sync_peers(db: Session, code: str) -> None:
    """네이버 동일업종 스크랩 → peers upsert + sync_state 마킹."""
    import requests

    session = requests.Session()
    fetched = quote.fetch_peers(code, session)
    for p in fetched:
        vals = {field: p.values.get(label) for field, label in _PEER_FIELDS.items()}
        stmt = insert(Peer).values(
            base_stock_code=code, peer_stock_code=p.stock_code, peer_name=p.name, **vals
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_peer",
            set_={
                "peer_name": stmt.excluded.peer_name,
                **{f: getattr(stmt.excluded, f) for f in _PEER_FIELDS},
            },
        )
        db.execute(stmt)
    sync_state.mark(db, "peers", code)
    db.commit()


def sync_peers_bg(code: str) -> None:
    db = SessionLocal()
    try:
        sync_peers(db, code)
    except Exception as e:
        db.rollback()
        logger.warning("peers sync failed %s: %s", code, e)
    finally:
        db.close()


# ── 타임라인 ────────────────────────────────────────────────────────────
def sync_disclosures_safe(db: Session, code: str, begin: date, end: date) -> None:
    """DART 공시 동기화(cache-aside). 키 없으면 스킵. 실패 시 롤백(타임라인 500 방지).

    동기화 창은 최근 7일로 제한한다 — 타임라인이 730일 기본 윈도우로 호출돼도 DART
    조회가 오래 걸리지 않도록. 오래된 공시는 이전 동기화가 DB에 남아 있어 타임라인에
    계속 표시된다.
    """
    settings = get_settings()
    if not settings.dart_api_key:
        return
    sync_begin = max(begin, end - timedelta(days=7))
    try:
        dart_ingest.sync_disclosures(db, settings, code, sync_begin, end)
    except Exception as e:
        db.rollback()
        logger.warning("disclosure sync failed %s: %s", code, e)


def timeline_reports(db: Session, code: str, begin: date, end: date) -> list[tuple]:
    return list(
        db.execute(
            select(Report, ReportAnalysis)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(
                Report.stock_code == code,
                Report.published_date >= begin,
                Report.published_date <= end,
            )
        ).all()
    )


def timeline_disclosures(db: Session, code: str, begin: date, end: date) -> list[Disclosure]:
    return list(
        db.scalars(
            select(Disclosure).where(
                Disclosure.stock_code == code,
                Disclosure.rcept_dt >= begin,
                Disclosure.rcept_dt <= end,
            )
        ).all()
    )


def timeline_broadcasts(db: Session, code: str, begin: date, end: date) -> list[Broadcast]:
    return list(
        db.scalars(
            select(Broadcast).where(
                Broadcast.stock_codes.contains([code]),
                Broadcast.ref_date >= begin,
                Broadcast.ref_date <= end,
            )
        ).all()
    )


# ── 타임라인 캐시 ────────────────────────────────────────────────────────

_TIMELINE_WINDOW_DAYS = 730  # 기본 조회 창 — 과거 2년(프론트가 10개씩 페이지네이션).
_TIMELINE_SNIPPET = 160  # 브로드캐스트 본문 미리보기 최대 길이


def _snippet(body: str) -> str:
    """브로드캐스트 본문에서 헤더·구분선을 제외한 앞부분 미리보기."""
    lines = [ln for ln in body.splitlines() if ln.strip() and set(ln.strip()) != {"─"}]
    text = " ".join(lines[1:]) if len(lines) > 1 else " ".join(lines)
    return text[:_TIMELINE_SNIPPET] + ("…" if len(text) > _TIMELINE_SNIPPET else "")


def get_timeline_cache(db: Session, code: str) -> TimelineCache | None:
    """TimelineCache 에서 stock_code 조회. 없으면 None."""
    return db.scalar(select(TimelineCache).where(TimelineCache.stock_code == code))


def build_timeline_items(
    db: Session, code: str, begin: date, end: date
) -> tuple[list[dict], date | None]:
    """3개 소스에서 타임라인 아이템을 조립, 최신순 정렬, 최근 공시일 반환.

    company_timeline() 라우터의 아이템 조립 로직과 동일하다.
    반환: (items_dicts, last_disclosure_date)
    """
    from app.schemas import TimelineItem

    items: list[dict] = []
    last_disc_date: date | None = None

    for r, a in timeline_reports(db, code, begin, end):
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
            ).model_dump(mode="json")
        )

    for d in timeline_disclosures(db, code, begin, end):
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
            ).model_dump(mode="json")
        )
        if last_disc_date is None or d.rcept_dt > last_disc_date:
            last_disc_date = d.rcept_dt

    for b in timeline_broadcasts(db, code, begin, end):
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
            ).model_dump(mode="json")
        )

    items.sort(key=lambda x: x["date"], reverse=True)
    return items, last_disc_date


def store_timeline_cache(
    db: Session, code: str, items: list[dict], last_disclosure_date: date | None
) -> None:
    """TimelineCache upsert. on_conflict_do_update 로 stock_code 당 1행 유지."""
    stmt = insert(TimelineCache).values(
        stock_code=code,
        payload={"items": items},
        last_disclosure_date=last_disclosure_date,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_timeline_cache_code",
        set_={
            "payload": {"items": items},
            "last_disclosure_date": last_disclosure_date,
            "cached_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def refresh_timeline_cache(db: Session, code: str) -> tuple[list[dict], date | None]:
    """DART 신규 공시 동기화 → 캐시 재구축 → 최신 아이템 반환.

    1. 현재 캐시의 last_disclosure_date 부터 오늘까지 DART 동기화
    2. 전체 타임라인 재조립
    3. 캐시 저장
    4. (items, last_disclosure_date) 반환
    """
    cache = get_timeline_cache(db, code)
    today = datetime.now().date()
    sync_from = (
        cache.last_disclosure_date
        if cache and cache.last_disclosure_date
        else today - timedelta(days=7)
    )

    sync_disclosures_safe(db, code, sync_from, today)

    end = today
    begin = end - timedelta(days=_TIMELINE_WINDOW_DAYS)
    items, last_disc_date = build_timeline_items(db, code, begin, end)
    store_timeline_cache(db, code, items, last_disc_date)
    return items, last_disc_date


# ── 성장지표 ────────────────────────────────────────────────────────────
def growth_snapshot(db: Session, code: str) -> UniverseSnapshot | None:
    snap_date = universe_ingest.latest_snapshot_date(db)
    if not snap_date:
        return None
    return db.scalar(
        select(UniverseSnapshot).where(
            UniverseSnapshot.snapshot_date == snap_date, UniverseSnapshot.stock_code == code
        )
    )


def daily_closes(db: Session, code: str, limit: int = 260) -> list[tuple[str, float]]:
    """일봉 (날짜iso, 종가) 최근 limit 개(오름차순). 베타 회귀용. 지수(KOSPI/KOSDAQ)도 code 로 조회."""
    from app.db.models import PriceCandle, Timeframe

    rows = db.execute(
        select(PriceCandle.bar_date, PriceCandle.close)
        .where(PriceCandle.stock_code == code, PriceCandle.timeframe == Timeframe.DAY)
        .order_by(PriceCandle.bar_date.desc())
        .limit(limit)
    ).all()
    return [(d.isoformat(), c) for d, c in reversed(rows)]


# 혼동 종목명(대상명을 부분문자열로 포함하는 다른 종목명)이 이 수 이상이면 재벌·그룹 약칭으로 보고
# 산업 리포트의 종목명 매칭을 끈다(코드 매칭에만 의존). 측정 근거: 혼동명 0~3개는 오탐 거의 없고
# (케이아이엔엑스 0·에코프로 3), 8개+ 는 오탐 다발(SK 37·LG 14·KT 8). 4 를 경계로 약칭을 분리한다.
_CONFUSABLE_ABBREV_THRESHOLD = 4


# 같은 회사의 파생상품(우선주·ETF·ETN) — 대상명을 포함해도 '다른 회사'가 아니라 오탐 소스가 아니다.
# 이걸 혼동명에서 빼야 '삼성전자'(파생만 있음 → 고유명)와 'SK'(SK하이닉스 등 다른 회사 다수 → 약칭)가
# 구별된다. 대상명 뒤 우선주 접미사, 또는 ETF/ETN 브랜드·상품어를 포함하면 파생으로 간주.
_ETF_ETN_MARK = re.compile(
    r"KODEX|TIGER|KIWOOM|SOL|RISE|PLUS|ACE|WON|UNICORN|HANARO|KOSEF|ARIRANG|KBSTAR|TIMEFOLIO|"
    r"채권혼합|레버리지|인버스|단일종목|선물|ETN|액티브|커버드콜|밸류체인|그룹플러스|포커스|[0-9]+호$"
)


def _confusable_names(db: Session, name: str) -> list[str]:
    """대상명을 포함하는 '다른 회사' 종목명들(예: 'SK' → 'SK하이닉스'·'SK증권'…). 본문 매칭 오탐 소스.

    산업 리포트 본문의 'SK하이닉스'가 'SK' 부분매칭으로 오탐하므로 매칭 제외에 쓴다. 단 같은 회사 파생
    (우선주 '삼성전자우', ETF 'KODEX 삼성전자…')은 오탐 소스가 아니라 제외한다 — 이래야 파생만 있는
    고유명(삼성전자, 혼동명 0)과 다른 회사가 많은 약칭(SK, 혼동명 다수)이 갈린다.
    """
    rows = db.execute(
        select(UniverseSnapshot.stock_name)
        .where(UniverseSnapshot.stock_name.is_not(None), UniverseSnapshot.stock_name.contains(name))
        .distinct()
    ).all()
    pref = re.compile(rf"^{re.escape(name)}우[A-C]?$")  # 대상명의 우선주(같은 회사)
    return [
        r[0]
        for r in rows
        if r[0] and r[0] != name and not pref.match(r[0]) and not _ETF_ETN_MARK.search(r[0])
    ]


def _mentions_target(text: str | None, name: str | None, confusables: list[str]) -> bool:
    """본문 text 에 이 종목이 단어경계로 단독 언급됐는지(파이썬 판정, DB 정규식 비의존 → 이식성).

    혼동명(다른 회사)을 먼저 제거한 뒤(부분문자열 오탐 차단), 남은 텍스트에서 종목명이 앞뒤가 한글·
    영숫자가 아닌 독립 토큰으로 나오면 True. name 없으면 False.
    """
    if not text or not name:
        return False
    stripped = text
    for c in confusables:
        stripped = stripped.replace(c, " ")
    return (
        re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(name)}(?![가-힣A-Za-z0-9])", stripped)
        is not None
    )


def _coverage_rows(db: Session, code: str, since: date) -> list[tuple[Report, ReportAnalysis]]:
    """커버리지 리포트(종목 + 본문 언급 산업) — since 이후 최신순. counts·reports 공용.

    산업 리포트는 industry_name(섹터 분류, '기타'·오분류 사각지대)이 아니라 본문 언급으로 판정:
      1) 종목코드(6자리) 언급 — 오탐 0(가장 확실), 또는
      2) 종목명 단어경계 단독 언급(혼동명 제거 후) — 단 혼동명(다른 회사)이 임계 이상인 재벌 약칭
         (SK·LG)은 표기변형·사전누락으로 오탐이 남아 이름 매칭을 끄고 코드에만 의존.
    SQL 은 이식 가능한 contains 로 후보만 넓게 뽑고, 단어경계·혼동명 정밀 판정은 파이썬에서 한다
    (산업 리포트가 수백 건 규모라 성능 무관, DB 정규식 비의존).
    """
    name = resolve_stock_name(db, code)
    confusables = _confusable_names(db, name) if name else []
    name_match_on = bool(name) and len(confusables) < _CONFUSABLE_ABBREV_THRESHOLD
    text_cols = (ReportAnalysis.full_text, ReportAnalysis.rationale)

    # SQL 후보: 종목 리포트 OR (산업 & (코드 언급 OR [이름매칭 켜졌으면] 이름 부분포함)).
    candidate_terms = [col.contains(code) for col in text_cols]
    if name_match_on:
        candidate_terms += [col.contains(name) for col in text_cols]
    industry_candidate = and_(Report.category == "industry", or_(*candidate_terms))
    rows = db.execute(
        select(Report, ReportAnalysis)
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(or_(Report.stock_code == code, industry_candidate), Report.published_date >= since)
        .order_by(Report.published_date.desc())
    ).all()

    out: list[tuple[Report, ReportAnalysis]] = []
    for rep, an in rows:
        if rep.stock_code == code:  # 종목 직접 리포트
            out.append((rep, an))
            continue
        # 산업 리포트 — 정밀 판정: 코드 언급 OR (이름매칭 켜졌고 단어경계 단독 언급)
        by_code = (code in (an.full_text or "")) or (code in (an.rationale or ""))
        by_name = name_match_on and (
            _mentions_target(an.full_text, name, confusables)
            or _mentions_target(an.rationale, name, confusables)
        )
        if by_code or by_name:
            out.append((rep, an))
    return out


def coverage_counts(db: Session, code: str, since: date) -> tuple[int, int]:
    """(리포트수, BUY수) since 이후. 종목 리포트 + 회사가 본문 언급된 산업 리포트."""
    rows = _coverage_rows(db, code, since)
    buys = sum(1 for _r, a in rows if a.sentiment == Sentiment.BUY)
    return len(rows), buys


def coverage_reports(db: Session, code: str, since: date) -> list[tuple[Report, ReportAnalysis]]:
    """커버리지 리포트 목록(종목 + 회사 언급된 산업), since 이후 최신순. counts 와 동일 조건·창."""
    return _coverage_rows(db, code, since)


def report_stock_name(db: Session, code: str) -> str | None:
    """리포트에서만 종목명 조회(성장지표 폴백 — 스냅샷에 없는 종목용). resolve_stock_name 과 달리
    유니버스 스냅샷을 보지 않아, 최신 스냅샷에 없는 종목은 시세 필드와 함께 이름도 비게 유지한다."""
    return db.scalar(
        select(Report.stock_name)
        .where(Report.stock_code == code, Report.stock_name.is_not(None))
        .limit(1)
    )
