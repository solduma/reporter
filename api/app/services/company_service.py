"""종목 상세 페이지 조회·동기화 서비스 — 라우터가 쓰던 데이터 접근·스크랩·백필을 응용 계층으로.

라우터는 이 서비스가 돌려준 ORM/데이터로 DTO(AnalysisAxis·TimelineItem 등)를 조립만 한다.
외부 스크랩은 naver_quote 어댑터 위임, PER/PBR/PSR 은 financials_backfill, EV/EBITDA(정밀 D&A)는
report_ingest 가 각각 단일 소유한다(역사 시총 기준으로 통일).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.market import naver_quote as quote
from app.config import get_settings
from app.db.models import (
    Broadcast,
    Disclosure,
    Financial,
    GrowthMetric,
    Peer,
    Report,
    ReportAnalysis,
    SectorTheme,
    SectorThemeStock,
    Sentiment,
    SyncState,
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
def financials_rows(db: Session, code: str) -> list[Financial]:
    """저장된 재무 기간 정렬 반환(외부 호출 없음)."""
    return list(
        db.scalars(
            select(Financial).where(Financial.stock_code == code).order_by(Financial.period)
        ).all()
    )


def latest_valuation(db: Session, code: str) -> Financial | None:
    """가치 축용 최신 밸류에이션 Financial(비추정). per/pbr 있는 행을 최신순 우선(반쪽 연간행
    이 최신으로 잡혀 누락되는 것 방지). 결산분기 배당(div_yield)을 in-memory 로 보정해 붙인다."""
    has_value = case((or_(Financial.per.is_not(None), Financial.pbr.is_not(None)), 0), else_=1)
    fin = db.scalars(
        select(Financial)
        .where(Financial.stock_code == code, Financial.is_estimate.is_(False))
        .order_by(has_value.asc(), Financial.period.desc())
        .limit(1)
    ).first()
    if fin is None:
        return None
    # 배당·EV/EBITDA 는 연간(.12)에만 있어(분기 최신 행엔 결측), 최신 연간값을 끌어와 보정한다.
    # 안 하면 가치 축에서 EV/EBITDA 가 항상 누락된다(분기 행이 최신으로 잡히므로).
    if fin.div_yield is None:
        fin.div_yield = _latest_annual_value(db, code, Financial.div_yield)  # 읽기 전용(커밋 안 함)
    if fin.ev_ebitda is None:
        fin.ev_ebitda = _latest_annual_value(db, code, Financial.ev_ebitda)
    return fin


def _latest_annual_value(db: Session, code: str, column):
    """연간(.12) 비추정 행 중 해당 컬럼의 최신 유효값. 분기 최신 행에 없는 연간 지표 보정용."""
    return db.scalar(
        select(column)
        .where(
            Financial.stock_code == code,
            Financial.is_estimate.is_(False),
            column.is_not(None),
            Financial.period.like("%.12"),
        )
        .order_by(Financial.period.desc())
        .limit(1)
    )


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
            stock_code=code, period=f.period, is_estimate=f.is_estimate,
            revenue=f.revenue, operating_income=f.operating_income, net_income=f.net_income,
            eps=f.eps, bps=f.bps, roe=f.roe, dps=f.dps, div_yield=f.div_yield,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_financial",
            set_={
                c: getattr(stmt.excluded, c)
                for c in ("is_estimate", "revenue", "operating_income", "net_income",
                          "eps", "bps", "roe", "dps", "div_yield")
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
    return list(db.scalars(select(Peer).where(Peer.base_stock_code == code).order_by(Peer.id)).all())


def peers_fresh(db: Session, code: str) -> bool:
    return sync_state.is_fresh(db, "peers", code, _PEERS_TTL)


def peer_valuations(db: Session, codes: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """peer 종목들의 최근(추정 아닌) 분기 ev_ebitda·psr 표시문자열. period desc 로 최신 채움."""
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
            continue
        if r.ev_ebitda is not None or r.psr is not None:
            ev = f"{r.ev_ebitda:.1f}" if r.ev_ebitda is not None else None
            psr = f"{r.psr:.2f}" if r.psr is not None else None
            out[r.stock_code] = (ev, psr)
    return out


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
            set_={"peer_name": stmt.excluded.peer_name, **{f: getattr(stmt.excluded, f) for f in _PEER_FIELDS}},
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
    """DART 공시 동기화(cache-aside). 키 없으면 스킵. 실패 시 롤백(타임라인 500 방지)."""
    settings = get_settings()
    if not settings.dart_api_key:
        return
    try:
        dart_ingest.sync_disclosures(db, settings, code, begin, end)
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


def coverage_counts(db: Session, code: str, since: date) -> tuple[int, int]:
    """(리포트수, BUY수) since 이후."""
    cov = db.execute(
        select(
            func.count(Report.id),
            func.sum(case((ReportAnalysis.sentiment == Sentiment.BUY, 1), else_=0)),
        )
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(Report.stock_code == code, Report.published_date >= since)
    ).one()
    return int(cov[0] or 0), int(cov[1] or 0)


def report_stock_name(db: Session, code: str) -> str | None:
    """리포트에서만 종목명 조회(성장지표 폴백 — 스냅샷에 없는 종목용). resolve_stock_name 과 달리
    유니버스 스냅샷을 보지 않아, 최신 스냅샷에 없는 종목은 시세 필드와 함께 이름도 비게 유지한다."""
    return db.scalar(
        select(Report.stock_name)
        .where(Report.stock_code == code, Report.stock_name.is_not(None))
        .limit(1)
    )
