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
from app.adapters.dart import throttle as dart_throttle
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


def _shares_from_dart(settings: Settings, corp_code: str, today: date) -> int | None:
    """OpenDART 주식총수(DS002)로 발행주식수 조회 — 소스 통일. 최근 확정 사업연도부터 최대 3년 폴백.

    사업보고서는 다음 해 3월 제출이라 올해분은 아직 없을 수 있어 직전 연도부터 시도한다.
    발행총수(issued)를 반환(자기주식 포함 — 시총 계산 기준). 실패·미공시면 None(상위가 KRX·스냅샷 폴백).
    """
    with requests.Session() as s:
        for year in range(today.year - 1, today.year - 4, -1):
            total = dart.fetch_stock_total(settings.dart_api_key, corp_code, year, 4, s)
            if total and total.issued:
                return total.issued
    return None


def _upsert_dividend(
    db: Session,
    code: str,
    period: str,
    settings: Settings,
    corp_code: str,
    year: int,
    session: requests.Session,
) -> None:
    """OpenDART 배당(DS002)의 당기 dps·현금배당수익률을 financials 연간 행에 반영 — 소스 통일.

    배당은 사업보고서에만 있는 연간 항목이라 annual period(YYYY.12)에만 upsert 한다. 미공시·무배당
    이면 조용히 건너뛴다(네이버 스크랩 dps/div_yield 가 폴백으로 남는다). dps·div_yield 만 갱신해
    같은 행의 다른 재무값(백필·네이버 유래)은 건드리지 않는다.
    """
    div = dart.fetch_dividend(settings.dart_api_key, corp_code, year, 4, session)
    if div is None or (div.dps is None and div.div_yield is None):
        return
    values = {k: v for k, v in (("dps", div.dps), ("div_yield", div.div_yield)) if v is not None}
    stmt = insert(Financial).values(stock_code=code, period=period, is_estimate=False, **values)
    stmt = stmt.on_conflict_do_update(constraint="uq_financial", set_=values)
    db.execute(stmt)


# DS003 재무지표(fnlttSinglIndx)는 2023 3Q부터 제공 — 그 이전 연도는 조회해도 013(없음)이라
# 콜만 낭비한다. 사업보고서(연간) 기준 2023 회계연도부터만 시도하고 이전은 네이버 ROE 폴백.
_DS003_FROM_YEAR = 2023


def _upsert_roe(
    db: Session,
    code: str,
    period: str,
    settings: Settings,
    corp_code: str,
    year: int,
    session: requests.Session,
) -> None:
    """OpenDART 재무지표(DS003)의 ROE(%)를 financials 연간 행에 반영 — 소스 통일.

    2023 회계연도부터만 조회한다(그 이전은 미제공 → 네이버 sync_financials.roe 폴백). roe 만
    갱신해 같은 행의 per/pbr/psr(financials_backfill)·다른 값은 건드리지 않는다.
    """
    if year < _DS003_FROM_YEAR:
        return
    roe = dart.fetch_roe(settings.dart_api_key, corp_code, year, 4, session)
    if roe is None:
        return
    stmt = insert(Financial).values(stock_code=code, period=period, is_estimate=False, roe=roe)
    stmt = stmt.on_conflict_do_update(constraint="uq_financial", set_={"roe": roe})
    db.execute(stmt)


def _shares_from_snapshot(db: Session, code: str) -> int | None:
    """유니버스 스냅샷의 시가총액÷종가로 상장주식수를 역산(KRX shares 조회 실패 시 폴백).

    KRX fetch_shares 가 대부분 종목에서 빈 값을 줘 EV/EBITDA 가 전종목 결측이 되던 문제 대응.
    스냅샷은 이미 시총·종가를 담고 있어(=시총/종가=주식수) 외부 호출 없이 근사 주식수를 얻는다.
    최신 스냅샷 기준(가장 최근 상장주식수) — EV 는 각 연도 분기말 종가에 이 주식수를 곱해 근사한다.
    """
    row = db.execute(
        select(UniverseSnapshot.market_cap, UniverseSnapshot.close_price)
        .where(UniverseSnapshot.stock_code == code)
        .order_by(UniverseSnapshot.snapshot_date.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    market_cap, close_price = row
    if not market_cap or not close_price:
        return None
    return round(market_cap / close_price)


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
            # 감가상각(원문 XML) — 구조화 API 가 놓치는 D&A. 매출 대비 비현실적으로 크면(오파싱) 폐기.
            raw = dart_report_parser.fetch_report_zip(settings.dart_api_key, rcept_no, session)
            dep = dart_report_parser.parse_cf_depreciation(raw) if raw else None
            dep = dart_report_parser.plausible_depreciation(dep, fin.revenue)
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
                capex=fin.capex,  # 구조화 API CF 투자활동(유형+무형 취득 합, 원)
                income_tax=fin.income_tax,
                pretax_income=fin.pretax_income,
                interest_expense=fin.interest_expense,
            )
            # 연간 financials 반영값: EBITDA·순차입·D&A·CAPEX(FCFF) + 실효세율·부채비용(WACC).
            if kind == "annual" and fin.operating_income is not None and dep is not None:
                annual_ev[period] = {
                    "ebitda": fin.operating_income + dep, "net_debt": fin.net_debt,
                    "dep": dep, "capex": fin.capex,
                    "effective_tax_rate": _effective_tax_rate(fin),
                    "cost_of_debt": _cost_of_debt(fin),
                }
            # 배당(DS002 alotMatter)은 연간 항목 — annual 보고서에서만 조회해 financials 에 반영.
            if kind == "annual":
                _upsert_dividend(db, code, period, settings, corp_code, year, session)
                _upsert_roe(db, code, period, settings, corp_code, year, session)
            db.commit()

    if not any_data:
        return True

    if shares is None:  # 배치가 주지 않았으면(온디맨드 단건) 이때 조회.
        # OpenDART 주식총수(DS002) 우선 — 정기보고서 유래로 소스 통일. 실패 시 KRX→스냅샷 폴백.
        shares = _shares_from_dart(settings, corp_code, today)
        if not shares:
            latest = universe_ingest.latest_snapshot_date(db)
            if latest:
                with requests.Session() as s:
                    shares = krx.fetch_shares(settings.krx_api, latest.strftime("%Y%m%d"), code, s)
    if not shares:  # DART·KRX 실패 시 스냅샷 시총÷종가로 역산(EV/EBITDA 전종목 결측 방지).
        shares = _shares_from_snapshot(db, code)
    _recompute_ev_ebitda(db, code, annual_ev, shares)
    db.commit()
    logger.info("report backfill %s: %d annual EV periods", code, len(annual_ev))
    return True


def _effective_tax_rate(fin) -> float | None:
    """실효세율 = 법인세비용/세전이익. 세전이익≤0 이면 무의미 → None. 이상치는 [0,0.35] clamp."""
    tax, pre = fin.income_tax, fin.pretax_income
    if tax is None or pre is None or pre <= 0:
        return None
    return round(max(0.0, min(0.35, tax / pre)), 4)


def _cost_of_debt(fin) -> float | None:
    """부채비용 = 이자비용/총차입. 무차입(총차입 0/결측)이면 None(WACC 에서 D=0이라 무관)."""
    intr, bor = fin.interest_expense, fin.borrowings
    if intr is None or bor is None or bor <= 0:
        return None
    return round(intr / bor, 4)


def _recompute_ev_ebitda(
    db: Session, code: str, annual_ev: dict[str, dict], shares: int | None
) -> None:
    """연간 EBITDA·순차입으로 EBITDA 절대액·EV/EBITDA 를 financials 에 반영 + D&A·CAPEX·세율·부채비용.

    EBITDA(영업이익+D&A) 절대액은 시총과 무관하므로 항상 저장(딥다이브 EBITDA 성장 축이 읽는다).
    EV/EBITDA 배수는 EV=시총(분기말 수정종가 x 주식수)+순차입 이 필요해 shares·종가 있을 때만 산출.
    D&A·CAPEX 는 억원 변환(FCFF), 실효세율·부채비용은 비율(WACC·NOPAT 실측). 결측은 상수 폴백에 위임.
    """
    if not annual_ev:
        return
    for period, d in annual_ev.items():
        ebitda, net_debt = d["ebitda"], d["net_debt"]
        if ebitda <= 0:
            continue
        # EBITDA 절대액(억원). 매출·이익과 단위 일치(딥다이브 EBITDA 마진 = ebitda/revenue).
        values: dict = {"ebitda": ebitda / 1e8}
        if d.get("dep") is not None:
            values["depreciation"] = d["dep"] / 1e8
        if d.get("capex") is not None:
            values["capex"] = d["capex"] / 1e8
        if d.get("effective_tax_rate") is not None:
            values["effective_tax_rate"] = d["effective_tax_rate"]
        if d.get("cost_of_debt") is not None:
            values["cost_of_debt"] = d["cost_of_debt"]
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


def _reconcile_markers(db: Session, codes: list[str], done: set[str]) -> int:
    """report_financials 행이 이미 있는데 마커가 없는 종목의 완료 마커를 복원(DART 재조회 없이).

    report_financials 는 이 백필만 쓰는 전용 산출물이라, 있으면 과거에 백필이 완료된 종목이다.
    sync_state 마커가 외부에서 삭제돼도(일회성 정리 등) 이미 채운 종목을 매일 재조회(종목당
    document.xml 수MB)하지 않도록 마커를 되살린다. financials_backfill 과 동형. 반환: 복원 개수.
    """
    missing = [c for c in codes if c not in done]
    if not missing:
        return 0
    has_report = set(
        db.scalars(
            select(ReportFinancial.stock_code)
            .where(ReportFinancial.stock_code.in_(missing))
            .distinct()
        ).all()
    )
    for code in has_report:
        sync_state.mark(db, _BACKFILL_DOMAIN, code)
    if has_report:
        db.commit()
        done.update(has_report)
        logger.info("report backfill: 마커 %d개 복원(report_financials 보유·마커 결손)", len(has_report))
    return len(has_report)


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
    done_codes = _done_codes(db)
    reconciled = _reconcile_markers(db, codes, done_codes)  # 마커 결손분 복원(재조회 낭비 방지)
    pending = [c for c in codes if c not in done_codes]
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
    quota_hit = budget_hit = False
    for code in batch:
        # 정기공시·온디맨드 몫을 남기려 백필 예산을 넘으면 조기 중단(다음 밤에 이어서 처리).
        if dart_throttle.backfill_budget_exhausted():
            budget_hit = True
            logger.info("report backfill: 백필 예산 소진 — 조기 중단(%d 종목 처리 후)", done)
            break
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
        "report backfill: done=%d failed=%d reconciled=%d remaining=%d quota_hit=%s budget_hit=%s",
        done, failed, reconciled, remaining, quota_hit, budget_hit,
    )
    return {
        "done": done, "failed": failed, "reconciled": reconciled, "remaining": remaining,
        "quota_hit": quota_hit, "budget_hit": budget_hit,
    }


# ── WACC·FCFF 재료 경량 백필(capex·D&A·실효세율·부채비용) ─────────────────
# 모두 구조화 API(fnlttSinglAcntAll) 한 응답에서 파싱(D&A 원문 재다운로드 불필요, 추가 호출 0).
# financials 에 capex·세율·부채비용 중 하나라도 결측인 연간행을 채운다.
def backfill_capex(db: Session, settings: Settings | None = None, limit: int = 200) -> dict:
    """FCFF·WACC 재료(capex·D&A·실효세율·부채비용)를 financials 에 채운다(경량, 추가 호출 0).

    capex·세율·부채비용은 fetch_income_and_equity 한 응답에서, D&A 는 report_financials 원값 복사.
    financials 에 셋 중 하나라도 결측인 연간행이 대상. 반환: 처리 통계.
    """
    settings = settings or get_settings()
    rows = db.execute(
        select(ReportFinancial.stock_code, ReportFinancial.period, ReportFinancial.depreciation)
        .where(ReportFinancial.report_kind == "annual")
        .order_by(ReportFinancial.stock_code)
    ).all()
    # financials 에 capex·세율·부채비용 모두 채워진 (종목,기간)만 제외(하나라도 결측이면 대상).
    done = {
        (c, p) for c, p in db.execute(
            select(Financial.stock_code, Financial.period).where(
                Financial.capex.is_not(None),
                Financial.effective_tax_rate.is_not(None),
                Financial.cost_of_debt.is_not(None),
            )
        ).all()
    }
    pending = [(c, p, dep) for c, p, dep in rows if (c, p) not in done][:limit]
    if not pending:
        return {"filled": 0, "codes": 0}
    by_code: dict[str, list[tuple[str, float | None]]] = {}
    for code, period, dep in pending:
        by_code.setdefault(code, []).append((period, dep))
    filled = 0
    quota_hit = False
    with requests.Session() as session:
        for code, items in by_code.items():
            corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
            if not corp_code:
                continue
            try:
                for period, dep in items:
                    year = int(period.split(".")[0])
                    fin = dart.fetch_income_and_equity(settings.dart_api_key, corp_code, year, 4, session)
                    if fin is None:
                        continue
                    # report_financials 원자료(capex·세율·이자) 저장.
                    rf_vals = {"capex": fin.capex, "income_tax": fin.income_tax,
                               "pretax_income": fin.pretax_income, "interest_expense": fin.interest_expense}
                    rf_vals = {k: v for k, v in rf_vals.items() if v is not None}
                    if rf_vals:
                        db.execute(
                            insert(ReportFinancial)
                            .values(stock_code=code, period=period, fs_div="CFS",
                                    report_kind="annual", rcept_no="", **rf_vals)
                            .on_conflict_do_update(constraint="uq_report_financial", set_=rf_vals)
                        )
                    # financials 에 FCFF·WACC 재료 반영(capex·D&A 억원, 세율·부채비용 비율).
                    vals: dict = {}
                    if fin.capex is not None:
                        vals["capex"] = fin.capex / 1e8
                    if dep is not None:
                        vals["depreciation"] = dep / 1e8
                    etr = _effective_tax_rate(fin)
                    if etr is not None:
                        vals["effective_tax_rate"] = etr
                    cod = _cost_of_debt(fin)
                    if cod is not None:
                        vals["cost_of_debt"] = cod
                    if not vals:
                        continue
                    db.execute(
                        insert(Financial)
                        .values(stock_code=code, period=period, is_estimate=False, **vals)
                        .on_conflict_do_update(constraint="uq_financial", set_=vals)
                    )
                    filled += 1
                db.commit()
            except dart.DartQuotaExceeded:
                db.rollback()
                quota_hit = True
                logger.warning("wacc/fcff backfill: DART 한도초과 — 중단(%d 채움)", filled)
                break
            except Exception as e:
                db.rollback()
                logger.warning("wacc/fcff backfill failed for %s: %s", code, e)
    return {"filled": filled, "codes": len(by_code), "quota_hit": quota_hit}
