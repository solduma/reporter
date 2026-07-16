"""DART 정규 보고서 원문 파싱 적재 — report_financials + 정밀 EV/EBITDA.

연간 사업보고서 중심으로 10년치를, 2026 회계연도부터는 분기/반기/사업 전부를 적재한다.
각 보고서마다:
- fnlttSinglAcntAll(account_id): 매출·영업이익·지배순이익·EPS·지배자본 (안정적)
- document.xml 원문: 현금흐름표 감가상각+무형상각 (구조화 API 가 놓치는 D&A)
→ report_financials 에 원본 저장 + EBITDA(영업이익+D&A)로 financials.ev_ebitda 재산출.

무거워(보고서당 fnltt + 수MB document.xml) 야간 점진 백필(sync_state 'report_10y', 재개 가능).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import dart
from app.adapters.dart import report_parser as dart_report_parser
from app.adapters.dart.disclosure_adapter import DartDisclosureAdapter
from app.adapters.external import krx
from app.config import Settings, get_settings
from app.db.models import (
    CorpCodeMap,
    Financial,
    PriceCandle,
    ReportFinancial,
    SyncState,
    Timeframe,
    UniverseSnapshot,
)
from app.ports.disclosure import KrDisclosurePort
from app.services import sync_state, universe_ingest

logger = logging.getLogger(__name__)


# 포트 공급자 seam — 정기공시 접수번호 조회를 KrDisclosurePort 로. 기본은 실제 어댑터.
def _disclosures(settings: Settings) -> KrDisclosurePort:
    return DartDisclosureAdapter(settings.dart_api_key)

_BACKFILL_DOMAIN = "report_10y"
_YEARS = 10
# 이 회계연도부터는 분기/반기/사업 전부, 이전은 사업보고서만(과거 상세는 비용 대비 실익 낮음).
_FULL_FROM_YEAR = 2026
_QUARTER_MONTH = {"annual": 12, "half": 6, "quarter": 3}


def _target_reports(today: date) -> list[tuple[int, str]]:
    """백필 대상 (회계연도, kind) 목록. 과거는 annual, _FULL_FROM_YEAR~ 는 half·quarter 추가."""
    out: list[tuple[int, str]] = []
    for year in range(today.year - _YEARS, today.year + 1):
        # 사업보고서는 다음 해 3월 제출 → year 가 작년 이하일 때만 확정.
        if year < today.year:
            out.append((year, "annual"))
        if year >= _FULL_FROM_YEAR:
            out.append((year, "half"))
            out.append((year, "quarter"))
    return out


def _period_str(year: int, kind: str) -> str:
    return f"{year}.{_QUARTER_MONTH[kind]:02d}"


def _quarter_end_close(db: Session, code: str, year: int, kind: str) -> float | None:
    """보고 기간말 이하 최근 일봉 종가(수정주가)."""
    month = _QUARTER_MONTH[kind]
    boundary = date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
    return db.scalar(
        select(PriceCandle.close)
        .where(
            PriceCandle.stock_code == code,
            PriceCandle.timeframe == Timeframe.DAY,
            PriceCandle.bar_date <= boundary,
        )
        .order_by(PriceCandle.bar_date.desc())
        .limit(1)
    )


def _upsert_report(db: Session, code: str, period: str, kind: str, rcept_no: str, **vals) -> None:
    stmt = insert(ReportFinancial).values(
        stock_code=code, period=period, fs_div="CFS", report_kind=kind, rcept_no=rcept_no, **vals
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_report_financial",
        set_={"rcept_no": rcept_no, **{k: getattr(stmt.excluded, k) for k in vals}},
    )
    db.execute(stmt)


def backfill_stock(
    db: Session, settings: Settings, code: str, shares: int | None = None
) -> bool:
    """한 종목의 보고서 재무를 원문 파싱해 report_financials 적재 + EV/EBITDA 재산출.

    shares(현재 상장주식수)를 주면 KRX 조회를 생략한다(배치가 시장맵을 1회 받아 넘김). 없으면
    단건 조회. 성공(또는 데이터없음 확정) 시 True. 일시 실패면 False(재시도).
    """
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        return True  # 비상장 등 → 완료

    today = datetime.now(UTC).date()
    any_data = False
    disc = _disclosures(settings)
    # 연간 period → (EBITDA 원, 순차입 원|None). EV/EBITDA 재산출용(annual 만).
    annual_ev: dict[str, tuple[float, float | None]] = {}
    with requests.Session() as session:
        for year, kind in _target_reports(today):
            rcept_no = disc.find_periodic_report(corp_code, year, kind, session)
            if not rcept_no:
                continue
            # 손익·자본(구조화 API) — annual 은 연간, half/quarter 는 보고 기간 누적.
            q = 4 if kind == "annual" else (2 if kind == "half" else 1)
            fin = dart.fetch_income_and_equity(settings.dart_api_key, corp_code, year, q, session)
            if fin is None:
                continue
            any_data = True
            # 감가상각(원문 XML) — 구조화 API 가 놓치는 D&A.
            raw = dart_report_parser.fetch_report_zip(settings.dart_api_key, rcept_no, session)
            dep = dart_report_parser.parse_cf_depreciation(raw) if raw else None
            period = _period_str(year, kind)
            _upsert_report(
                db, code, period, kind, rcept_no,
                revenue=fin.revenue,
                operating_income=fin.operating_income,
                net_income=fin.net_income,
                equity=fin.equity,
                eps=fin.eps,
                depreciation=dep,  # parse_cf_depreciation 은 감가+무형 합산값(모델 주석 참조)
                amortization=None,
            )
            # EBITDA = 영업이익 + D&A. 연간만 EV/EBITDA 대상(반기/분기 누적은 TTM 아님).
            if kind == "annual" and fin.operating_income is not None and dep is not None:
                annual_ev[period] = (fin.operating_income + dep, fin.net_debt)
            db.commit()

    if not any_data:
        return True

    if shares is None:  # 배치가 주지 않았으면(온디맨드 단건) 이때 조회.
        latest = universe_ingest.latest_snapshot_date(db)
        if latest:
            with requests.Session() as s:
                shares = krx.fetch_shares(settings.krx_api, latest.strftime("%Y%m%d"), code, s)
    _recompute_ev_ebitda(db, code, annual_ev, shares)
    db.commit()
    logger.info("report backfill %s: %d annual EV periods", code, len(annual_ev))
    return True


def _recompute_ev_ebitda(
    db: Session, code: str, annual_ev: dict[str, tuple[float, float | None]], shares: int | None
) -> None:
    """연간 EBITDA·순차입으로 EBITDA 절대액과 EV/EBITDA 를 financials 에 반영(EV/EBITDA 단일 소유자).

    EBITDA(영업이익+D&A) 절대액은 시총과 무관하므로 항상 저장(딥다이브 EBITDA 성장 축이 읽는다).
    EV/EBITDA 배수는 EV=시총(분기말 수정종가 x 주식수)+순차입 이 필요해 shares·종가 있을 때만 산출.
    대형사 D&A 는 fnlttSinglAcntAll 에 없어(삼성·현대차 CF 에 상각 라인 부재) document.xml 원문 파싱만 정확.
    """
    if not annual_ev:
        return
    for period, (ebitda, net_debt) in annual_ev.items():  # ebitda·net_debt 는 원 단위 원자료
        if ebitda <= 0:
            continue
        # EBITDA 절대액은 shares 무관하게 항상 저장. Financial 의 매출·이익은 억원 단위이므로
        # ebitda 도 억원(/1e8)으로 변환해 저장(딥다이브 EBITDA 마진 = ebitda/revenue 단위 일치).
        values: dict = {"ebitda": ebitda / 1e8}
        # EV/EBITDA 배수는 EV(원)/EBITDA(원) 이라 원 단위 원자료로 계산(시총 산출 가능할 때만).
        if shares:
            year = int(period.split(".")[0])
            close = _quarter_end_close(db, code, year, "annual")
            if close:
                ev = close * shares + (net_debt or 0.0)
                values["ev_ebitda"] = round(ev / ebitda, 2)
        stmt = insert(Financial).values(
            stock_code=code, period=period, is_estimate=False, **values
        )
        stmt = stmt.on_conflict_do_update(constraint="uq_financial", set_=values)
        db.execute(stmt)


# ── 야간 점진 백필 ─────────────────────────────────────────────────────
_PER_RUN = 100  # 보고서당 document.xml(수MB) 다운로드라 무거움 → 하룻밤 소수


def _universe_codes(db: Session) -> list[str]:
    as_of = universe_ingest.latest_snapshot_date(db)
    if as_of is None:
        return []
    return list(
        db.scalars(
            select(UniverseSnapshot.stock_code).where(
                UniverseSnapshot.snapshot_date == as_of,
                UniverseSnapshot.stock_type == "stock",
                ~UniverseSnapshot.stock_name.op("~")(r"우[A-C]?$"),
            )
        ).all()
    )


def _done_codes(db: Session) -> set[str]:
    return set(
        db.scalars(select(SyncState.stock_code).where(SyncState.domain == _BACKFILL_DOMAIN)).all()
    )


def run_backfill_progressive(
    db: Session, settings: Settings | None = None, per_run: int = _PER_RUN
) -> dict:
    """유니버스 종목의 보고서 재무를 점진 백필한다(하룻밤 per_run 개, 재개 가능)."""
    settings = settings or get_settings()
    if not settings.dart_api_key:
        return {"done": 0, "failed": 0, "remaining": 0}
    codes = _universe_codes(db)
    if not codes:
        return {"done": 0, "failed": 0, "remaining": 0}
    pending = [c for c in codes if c not in _done_codes(db)]
    batch = pending[:per_run]
    # 주식수 시장맵을 배치당 1회만 조회(종목마다 전체시장 pull 반복 방지). 최신 스냅샷 기준.
    shares_map: dict[str, int] = {}
    latest = universe_ingest.latest_snapshot_date(db)
    if latest:
        with requests.Session() as s:
            bas = latest.strftime("%Y%m%d")
            for market in ("KOSPI", "KOSDAQ"):
                shares_map.update(krx.fetch_shares_by_date(settings.krx_api, bas, s, market))
    done = failed = 0
    quota_hit = False
    for code in batch:
        try:
            if backfill_stock(db, settings, code, shares=shares_map.get(code)):
                sync_state.mark(db, _BACKFILL_DOMAIN, code)
                db.commit()
                done += 1
            else:
                failed += 1
        except dart.DartQuotaExceeded:
            # 한도초과는 남은 종목도 모두 실패할 뿐 아니라 딥다이브 등 온디맨드 조회까지 굶긴다.
            # 배치를 즉시 중단해 콜 낭비를 막는다(다음 실행/자정 리셋 후 재개).
            db.rollback()
            quota_hit = True
            logger.warning("report backfill: DART 한도초과 — 배치 중단(%d 종목 처리 후)", done)
            break
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            failed += 1
            logger.warning("report backfill failed for %s: %s", code, e)
    remaining = len(pending) - done
    logger.info(
        "report backfill: done=%d failed=%d remaining=%d quota_hit=%s",
        done, failed, remaining, quota_hit,
    )
    return {"done": done, "failed": failed, "remaining": remaining, "quota_hit": quota_hit}
