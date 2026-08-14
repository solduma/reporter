"""재무·밸류에이션(PER/PBR/PSR) 10년 점진 백필 — DART 재무제표 + 보유 일봉 + 네이버 주식수.

네이버 main.naver 는 최근 분기 몇 개만 노출해 10년 히스토리가 없다. 이 서비스는:
- DART fnlttSinglAcntAll 로 과거 40분기(10년) 매출·지배순이익·EPS·지배자본을 수집(account_id 매칭).
- DART 분기/반기보고서는 '당기 3개월', 사업보고서(4Q)는 '연간 누적' → Q4 개별 = 연간-(Q1+Q2+Q3).
- 분기말 종가(수정주가, 보유 일봉)와 현재 상장주식수(네이버)로 '과거 시총'을 근사한 뒤,
  **총액 기준**으로 계산한다(주당 EPS/BPS 를 쓰지 않는 이유: 수정주가는 액면분할이 소급
  반영되지만 DART EPS/BPS 는 당시 미수정값이라 분할 종목에서 스케일이 어긋난다):
    과거 시총 ≈ 분기말_수정종가 x 현재_주식수  (수정주가 체계라 분할 소급이 자동 상쇄)
    PER = 시총 / TTM_순이익,  PBR = 시총 / 지배자본,  PSR = 시총 / TTM_매출
  (과거 증자·자사주 등 실제 주식수 변동은 현재 주식수 고정 근사로 미반영.)
무거워(종목당 40분기 DART 콜) 야간 점진 백필로 돌린다(sync_state 'financials_10y', 재개 가능).
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import dart
from app.adapters.dart import throttle as dart_throttle
from app.adapters.dart.report_parser import fetch_report_zip, parse_sce_tables_from_zip
from app.adapters.market import naver_quote as quote
from app.config import Settings, get_settings
from app.db.models import (
    CorpCodeMap,
    Disclosure,
    Financial,
    FinancialStatement,
    FinancialStatementCache,
    PriceCandle,
    SyncState,
    Timeframe,
    UniverseSnapshot,
)
from app.domain import financials
from app.services import ontology as ontology_service
from app.services import sync_state, universe_ingest
from app.services.fs_parse import parse_income_equity_from_fs, record_gaps

logger = logging.getLogger(__name__)

_BACKFILL_DOMAIN = "financials_10y"
_YEARS = 10
_QUARTER_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}


def _target_year_quarters(today: date) -> list[tuple[int, int]]:
    """오늘 기준 과거 _YEARS 년의 (year, quarter) 목록(오름차순). 미래 분기는 제외."""
    out: list[tuple[int, int]] = []
    for year in range(today.year - _YEARS, today.year + 1):
        for q in (1, 2, 3, 4):
            # 분기말이 오늘 이후면(아직 보고 전) 제외.
            if date(year, _QUARTER_MONTH[q], 28) <= today:
                out.append((year, q))
    return out


def _quarter_end_close(db: Session, code: str, year: int, quarter: int) -> float | None:
    """분기말(3/6/9/12월 말) 이하의 가장 최근 일봉 종가. 없으면 None."""
    end_month = _QUARTER_MONTH[quarter]
    # 분기말 경계일(말일 근사: 다음 달 1일 직전).
    boundary = date(year + (end_month // 12), (end_month % 12) + 1, 1) - timedelta(days=1)
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


def _ttm_from_discrete(
    discrete: dict[tuple[int, int], float | None], yq: tuple[int, int]
) -> float | None:
    """domain.financials.ttm_from_discrete 위임(음수-매출 필터 적용된 discrete dict)."""
    return financials.ttm_from_discrete(discrete, yq)


def _period_str(year: int, quarter: int) -> str:
    return f"{year}.{_QUARTER_MONTH[quarter]:02d}"


def backfill_stock(db: Session, settings: Settings, code: str) -> bool:
    """한 종목의 10년 분기 재무·PER/PBR/PSR 을 계산해 Financial 에 upsert 한다.

    성공(또는 데이터없음 확정) 시 True — 호출측이 완료 마킹한다. 일시 실패면 False(재시도).
    """
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        return True  # 매핑 없음(비상장 등) → 완료 처리(재시도 불필요)

    today = datetime.now(UTC).date()
    yqs = _target_year_quarters(today)

    # DART 원자료 수집(account_id 매칭). CFS(연결)와 OFS(별도)를 각각 수집한다.
    cfs_rev: dict[tuple[int, int], float | None] = {}
    cfs_op: dict[tuple[int, int], float | None] = {}
    cfs_ni: dict[tuple[int, int], float | None] = {}
    cfs_eps: dict[tuple[int, int], float | None] = {}
    cfs_equity: dict[tuple[int, int], float | None] = {}
    ofs_rev: dict[tuple[int, int], float | None] = {}
    ofs_op: dict[tuple[int, int], float | None] = {}
    ofs_ni: dict[tuple[int, int], float | None] = {}
    ofs_eps: dict[tuple[int, int], float | None] = {}
    ofs_equity: dict[tuple[int, int], float | None] = {}
    any_data = False
    # 전체 재무제표 라인아이템 수집(FinancialStatement 저장용)
    stmt_data: dict[tuple[int, int, str], dict[str, list[dict]]] = {}

    # ── 파이프라인 원칙: FinancialStatement 원문 우선 파싱 → DART 폴백 ──
    # 과거 분기 FS 행은 이미 DB 에 영속화되어 있으면 DART 0건으로 파싱. 없는 분기만
    # fetch_full_statements_by_div 로 CFS/OFS 각 1회 호출해 FS 저장 + 파싱(과거엔 매
    # 분기마다 fetch_income_and_equity + fetch_full_statements 중복 2~4회 호출).
    # db.scalars 로 엔티티를 직접 받는다 — select(Entity) 의 Row 는 키가 엔티티명 하나뿐이라
    # r.period 같은 컬럼 접근이 AttributeError 난다(08-12 백필 97.5% 정지 원인).
    existing_fs: dict[tuple[int, int, str], dict] = {
        (int(r.period.split(".")[0]), int(r.period.split(".")[1]), r.fs_div): r.data
        for r in db.scalars(
            select(FinancialStatement).where(FinancialStatement.stock_code == code)
        )
        if r.period and "." in r.period
    }

    def _collect_fin(fin, target: dict, fs_div: str, year: int, q: int, full: dict) -> None:
        if fin is None:
            return
        yq = (year, q)
        target_rev = cfs_rev if fs_div == "CFS" else ofs_rev
        target_op = cfs_op if fs_div == "CFS" else ofs_op
        target_ni = cfs_ni if fs_div == "CFS" else ofs_ni
        target_eps = cfs_eps if fs_div == "CFS" else ofs_eps
        target_eq = cfs_equity if fs_div == "CFS" else ofs_equity
        if fin.revenue is not None:
            target_rev[yq] = fin.revenue
        if fin.operating_income is not None:
            target_op[yq] = fin.operating_income
        if fin.net_income is not None:
            target_ni[yq] = fin.net_income
        if fin.eps is not None:
            target_eps[yq] = fin.eps
        if fin.equity is not None:
            target_eq[yq] = fin.equity
        # 파싱 실패 필드 기록(온톨로지 매핑 보완용). 여기선 FS 가 원천이라 폴백=skip.
        record_gaps(db, code, _period_str(year, q), fs_div, fin, full, fallback="skip")

    with requests.Session() as session:
        for year, q in yqs:
            period = _period_str(year, q)
            for fs_div in ("CFS", "OFS"):
                # 1) FS 원문 우선(DB 에 있으면 DART 0건 파싱)
                # existing_fs 키는 period "YYYY.MM" 에서 뽑은 (연도, 월) — 분기 q 를
                # _QUARTER_MONTH 로 월로 환산해 조회해야 q=3(9월)이 March 캐시에 hit 하지 않는다.
                cached = existing_fs.get((year, _QUARTER_MONTH[q], fs_div))
                if cached is not None:
                    fin = parse_income_equity_from_fs(cached)
                    if fin is not None:
                        any_data = True
                        _collect_fin(fin, None, fs_div, year, q, cached)
                    continue
                # 2) DART 폴백: FS 행이 없으면 fnlttSinglAcntAll 1회 호출로 FS 저장 + 파싱
                full = dart.fetch_full_statements_by_div(
                    settings.dart_api_key, corp_code, year, q, fs_div, session
                )
                if not full:
                    continue
                any_data = True
                stmt_data[(year, q, fs_div)] = full
                fin = parse_income_equity_from_fs(full)
                _collect_fin(fin, None, fs_div, year, q, full)
        shares = quote.fetch_shares_outstanding(code, session)

    if not any_data:
        return True  # 재무 공시 없음 → 완료 처리

    def _store_fs(
        fs_div: str, rev_raw: dict, op_raw: dict, ni_raw: dict, eps_raw: dict, equity: dict
    ) -> int:
        """한 fs_div(CFS/OFS)의 분기 개별값 환산 → PER/PBR/PSR 계산 → 저장. 저장한 분기 수 반환."""
        # 분기 개별값 환산(4Q=연간-누적).
        rev_q = {yq: financials.discrete_quarter(rev_raw, yq) for yq in rev_raw}
        op_q = {yq: financials.discrete_quarter(op_raw, yq) for yq in op_raw}
        ni_q = {yq: financials.discrete_quarter(ni_raw, yq) for yq in ni_raw}
        eps_q = {yq: financials.discrete_quarter(eps_raw, yq) for yq in eps_raw}
        # 매출 개별값이 음수면 1~3Q 가 누적 보고였다는 신호 → 그 분기 매출·TTM 을 신뢰 불가로 폐기.
        rev_q = {yq: (v if (v is None or v >= 0) else None) for yq, v in rev_q.items()}

        updated = 0
        for year, q in yqs:
            yq = (year, q)
            if yq not in rev_raw:
                continue
            close = _quarter_end_close(db, code, year, q)
            # 과거 시총 근사 = 분기말 수정종가 x 현재 주식수(수정주가라 분할 소급 상쇄).
            cap = (close * shares) if (close and shares) else None
            ttm_ni = _ttm_from_discrete(ni_q, yq)  # 원(총액)
            ttm_rev = _ttm_from_discrete(rev_q, yq)  # 원(총액)
            eq = equity.get(yq)  # 지배자본(원, 시점값)

            # 총액 기준(분할 무관): PER=시총/순이익, PBR=시총/자본, PSR=시총/매출.
            per = round(cap / ttm_ni, 2) if (cap and ttm_ni and ttm_ni > 0) else None
            pbr = round(cap / eq, 2) if (cap and eq and eq > 0) else None
            psr = round(cap / ttm_rev, 2) if (cap and ttm_rev and ttm_rev > 0) else None
            # BPS 표시용(현재 주식수 기준 근사, 원).
            bps = (eq / shares) if (eq and shares) else None

            rev_q_val = rev_q.get(yq)
            op_q_val = op_q.get(yq)
            ni_q_val = ni_q.get(yq)
            # 표시 단위: 매출·영업이익·순이익은 억원(기존 quote 저장 단위와 일치), EPS/BPS 는 원.
            # 영업이익은 적자(음수)도 유효값이라 클램프하지 않는다.
            _upsert_financial(
                db,
                code,
                _period_str(year, q),
                fs_div=fs_div,
                revenue=(rev_q_val / 1e8) if rev_q_val is not None else None,
                operating_income=(op_q_val / 1e8) if op_q_val is not None else None,
                net_income=(ni_q_val / 1e8) if ni_q_val is not None else None,
                eps=eps_q.get(yq),
                bps=bps,
                per=per,
                pbr=pbr,
                psr=psr,
            )
            updated += 1
        return updated

    updated = 0
    if cfs_rev:
        updated += _store_fs("CFS", cfs_rev, cfs_op, cfs_ni, cfs_eps, cfs_equity)
    if ofs_rev:
        updated += _store_fs("OFS", ofs_rev, ofs_op, ofs_ni, ofs_eps, ofs_equity)

    # 전체 재무제표 라인아이템 저장(FinancialStatement) — CFS/OFS 각각.
    for (year, q, fs_div), stmt in stmt_data.items():
        ontology_service.enrich_with_ontology_id(stmt)
        period = _period_str(year, q)
        stmt_insert = insert(FinancialStatement).values(
            stock_code=code,
            period=period,
            fs_div=fs_div,
            data=stmt,
        )
        stmt_insert = stmt_insert.on_conflict_do_update(
            constraint="uq_financial_statement",
            set_={"data": stmt_insert.excluded.data, "updated_at": func.now()},
        )
        db.execute(stmt_insert)

    # API 캐시를 날려 새 데이터가 즉시 반영되게 한다(트랜잭션 마지막에 한 번 commit).
    db.execute(
        FinancialStatementCache.__table__.delete().where(FinancialStatementCache.stock_code == code)
    )
    db.commit()
    logger.info("financials 10y backfill %s: %d periods (shares=%s)", code, updated, shares)
    return True


def _upsert_financial(db: Session, code: str, period: str, fs_div: str = "CFS", **vals) -> None:
    """Financial 행 upsert(백필 소유 필드만 갱신: 재무·PER/PBR/PSR). 추정치 아님.

    None 값은 갱신에서 제외한다 — 주식수 조회 실패(밸류 None) 등으로 기존 유효값(예: 네이버
    per/pbr, 이전 백필분)을 NULL 로 덮어쓰지 않기 위함.
    fs_div: 'CFS'(연결) | 'OFS'(별도) — CFS/OFS 각각 저장.
    """
    present = {k: v for k, v in vals.items() if v is not None}
    if not present:
        return
    stmt = insert(Financial).values(
        stock_code=code, period=period, fs_div=fs_div, is_estimate=False, **present
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_financial",
        set_={k: getattr(stmt.excluded, k) for k in present},
    )
    db.execute(stmt)


def _universe_codes(db: Session) -> list[str]:
    """공시일 내림차순 → 시총 내림차순. 최근 공시한 종목이 먼저 백필된다."""
    as_of = universe_ingest.latest_snapshot_date(db)
    if as_of is None:
        return []
    latest_disc = (
        select(Disclosure.stock_code, func.max(Disclosure.rcept_dt).label("latest_disc"))
        .group_by(Disclosure.stock_code)
        .subquery()
    )
    return list(
        db.scalars(
            select(UniverseSnapshot.stock_code)
            .outerjoin(latest_disc, latest_disc.c.stock_code == UniverseSnapshot.stock_code)
            .where(
                UniverseSnapshot.snapshot_date == as_of,
                UniverseSnapshot.stock_type == "stock",
                ~UniverseSnapshot.stock_name.op("~")(r"우[A-C]?$"),
            )
            .order_by(latest_disc.c.latest_disc.desc().nullslast(), UniverseSnapshot.market_cap.desc())
        ).all()
    )


def _done_codes(db: Session) -> set[str]:
    """sync_state 마킹 + 최근 6개월 내 revenue 있는 종목만 '완료'로 본다.

    sync_state에 있어도 최근 분기의 revenue가 None이면 미완료 → 재처리.
    빈 결과를 done으로 마킹해 최신 분기를 못 채우는 회귀 방지.
    SQL 조인으로 일괄 처리(대량 IN 파라미터 회피).
    """
    cutoff = (datetime.now(UTC) - timedelta(days=180)).strftime("%Y.%m")
    has_recent = set(
        db.scalars(
            select(SyncState.stock_code)
            .where(SyncState.domain == _BACKFILL_DOMAIN)
            .join(
                Financial,
                (Financial.stock_code == SyncState.stock_code)
                & Financial.is_estimate.is_(False)
                & Financial.revenue.is_not(None)
                & (Financial.period >= cutoff),
            )
            .distinct()
        ).all()
    )
    marked_count = db.scalar(
        select(func.count()).select_from(SyncState).where(SyncState.domain == _BACKFILL_DOMAIN)
    ) or 0
    stale = marked_count - len(has_recent)
    if stale > 0:
        logger.info(
            "financials 10y: %d종목 sync_state 있으나 최근 재무 결측 → 재처리 대상", stale
        )
    return has_recent


def _reconcile_markers(db: Session, codes: list[str], done: set[str]) -> int:
    """마커는 없지만 백필 산출물(psr)이 이미 있는 종목의 완료 마커를 복원한다(DART 재조회 없이).

    psr 은 이 백필만 쓰는 전용 출력값(backfill_stock → _upsert_financial)이라, psr 이 있으면
    과거에 백필이 실제로 완료된 종목이다. 단 CFS/OFS 모두 있어야 복원 — OFS 가 없으면
    재처리 대상으로 남긴다(CFS/OFS 분리 후 OFS 누락분 채우기 위함).
    sync_state 마커가 외부에서 삭제돼도(일회성 psql 정리 등) 이미 채운 종목을 매일 재조회하지
    않도록 마커를 되살린다. 반환: 복원 개수.
    """
    missing = [c for c in codes if c not in done]
    if not missing:
        return 0
    # CFS psr 이 있으면서 OFS 데이터도 있는 종목만 복원.
    has_both = set(
        db.scalars(
            select(Financial.stock_code)
            .where(
                Financial.stock_code.in_(missing),
                Financial.psr.isnot(None),
                Financial.fs_div == "CFS",
            )
            .distinct()
        ).all()
    )
    # OFS 데이터가 없는 종목은 복원에서 제외(재처리 필요).
    has_ofs = set(
        db.scalars(
            select(Financial.stock_code)
            .where(
                Financial.stock_code.in_(missing),
                Financial.fs_div == "OFS",
            )
            .distinct()
        ).all()
    )
    restore = has_both & has_ofs
    for code in restore:
        sync_state.mark(db, _BACKFILL_DOMAIN, code)
    if restore:
        db.commit()
        done.update(restore)
        logger.info(
            "financials 10y backfill: 마커 %d개 복원(psr+OFS 보유·마커 결손), "
            "OFS 누락 %d개는 재처리 대기",
            len(restore),
            len(has_both - has_ofs),
        )
    return len(restore)


# ── 야간 점진 백필 (재개 가능) ─────────────────────────────────────────
# 종목당 ~40분기 DART 콜 x dart_throttle(0.34s) ≈ 14s/종목. per_run=300 이면 하룻밤 ~70분,
# 일일 콜 ~12k(2만 한도 내). 배치 진입 시 남은 예산으로 자동 축소(_budget_aware_per_run)하므로
# 정기공시·온디맨드 몫을 지킨다.
_PER_RUN = 300
_PER_RUN_CAP = 300  # 예산이 넉넉해도 하룻밤 상한(과도한 DART 부하 방지).
_CALLS_PER_STOCK = 40  # 종목당 DART 콜 추정치(10년 x 4분기).


def _budget_aware_per_run(per_run: int) -> int:
    """남은 DART 예산 기준 per_run 보정 — 예산 부족 시 자동 축소, 여유 시 상한까지.

    remaining_budget() 은 키별 잔량 합계. 남은 예산으로 처리 가능한 종목 수 =
    remaining // _CALLS_PER_STOCK. 예산이 소진됐으면 0을 반환해 배치를 건너뛴다.
    """
    if per_run <= 0:
        return per_run
    remaining = dart_throttle.remaining_budget()
    budget_run = remaining // _CALLS_PER_STOCK if remaining else 0
    return min(per_run, _PER_RUN_CAP, budget_run)


def run_backfill_progressive(
    db: Session, settings: Settings | None = None, per_run: int = _PER_RUN
) -> dict:
    """유니버스 종목의 재무·밸류를 10년으로 점진 백필한다(하룻밤 per_run 개, 재개 가능).

    반환: {done, failed, remaining}. 종목당 DART 콜이 많아 순차 처리한다(병렬 시 DART 부하↑).
    """
    settings = settings or get_settings()
    if not settings.dart_api_key:
        logger.warning("no DART key; skip financials 10y backfill")
        return {"done": 0, "failed": 0, "remaining": 0}
    codes = _universe_codes(db)
    if not codes:
        return {"done": 0, "failed": 0, "remaining": 0}

    done_codes = _done_codes(db)
    reconciled = _reconcile_markers(db, codes, done_codes)  # 마커 결손분 복원(재조회 낭비 방지)
    pending = [c for c in codes if c not in done_codes]
    per_run = _budget_aware_per_run(per_run)  # 남은 예산으로 상한 보정(부족 시 축소).
    batch = pending[:per_run]
    done = failed = 0
    quota_hit = budget_hit = False
    for code in batch:
        # 정기공시·온디맨드 몫을 남기려 백필 예산을 넘으면 조기 중단(다음 밤에 이어서 처리).
        if dart_throttle.backfill_budget_exhausted():
            budget_hit = True
            logger.info(
                "financials 10y backfill: 백필 예산 소진 — 조기 중단(%d 종목 처리 후)", done
            )
            break
        try:
            if backfill_stock(db, settings, code):
                sync_state.mark(db, _BACKFILL_DOMAIN, code)
                db.commit()
                done += 1
            else:
                failed += 1
        except dart.DartQuotaExceeded:
            # 한도초과는 남은 종목도 모두 실패할 뿐 아니라 온디맨드 조회까지 굶긴다 → 배치 즉시 중단.
            db.rollback()
            quota_hit = True
            logger.warning(
                "financials 10y backfill: DART 한도초과 — 배치 중단(%d 종목 처리 후)", done
            )
            break
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            failed += 1
            logger.warning("financials 10y backfill failed for %s: %s", code, e)

    remaining = len(pending) - done
    logger.info(
        "financials 10y backfill: done=%d failed=%d reconciled=%d remaining=%d "
        "quota_hit=%s budget_hit=%s",
        done,
        failed,
        reconciled,
        remaining,
        quota_hit,
        budget_hit,
    )
    return {
        "done": done,
        "failed": failed,
        "reconciled": reconciled,
        "remaining": remaining,
        "quota_hit": quota_hit,
        "budget_hit": budget_hit,
    }


_OFS_STATEMENTS_DOMAIN = "ofs_statements"


def _ofs_done_codes(db: Session) -> set[str]:
    return set(
        db.scalars(
            select(SyncState.stock_code).where(SyncState.domain == _OFS_STATEMENTS_DOMAIN)
        ).all()
    )


def _ofs_pending_codes(db: Session, codes: list[str]) -> list[str]:
    """OFS FinancialStatement 가 이미 있는 종목은 제외."""
    done = _ofs_done_codes(db)
    has_ofs = set(
        db.scalars(
            select(FinancialStatement.stock_code)
            .where(
                FinancialStatement.stock_code.in_(codes),
                FinancialStatement.fs_div == "OFS",
            )
            .distinct()
        ).all()
    )
    return [c for c in codes if c not in done and c not in has_ofs]


def backfill_ofs_stock(db: Session, settings: Settings, code: str) -> bool:
    """한 종목의 10년 별도재무제표(FinancialStatement.fs_div='OFS')를 수집·저장한다."""
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        return True

    today = datetime.now(UTC).date()
    yqs = _target_year_quarters(today)
    updated = 0
    with requests.Session() as session:
        for year, q in yqs:
            full = dart.fetch_full_statements_ofs(
                settings.dart_api_key, corp_code, year, q, session
            )
            if not full:
                continue
            ontology_service.enrich_with_ontology_id(full)
            period = _period_str(year, q)
            stmt_insert = insert(FinancialStatement).values(
                stock_code=code,
                period=period,
                fs_div="OFS",
                data=full,
            )
            stmt_insert = stmt_insert.on_conflict_do_update(
                constraint="uq_financial_statement",
                set_={"data": stmt_insert.excluded.data, "updated_at": func.now()},
            )
            db.execute(stmt_insert)
            updated += 1

    if updated:
        # API 캐시를 날려 새 데이터가 즉시 반영되게 한다(트랜잭션 마지막에 한 번 commit).
        db.execute(
            FinancialStatementCache.__table__.delete().where(
                FinancialStatementCache.stock_code == code
            )
        )
        db.commit()
        logger.info("ofs statements backfill %s: %d periods", code, updated)
    return True


def _sce_pending_rows(db: Session) -> list[tuple[str, str]]:
    """SCE 키가 없는 (stock_code, period) 목록. CFS 한정."""
    rows = db.execute(
        select(FinancialStatement.stock_code, FinancialStatement.period)
        .where(
            FinancialStatement.fs_div == "CFS",
            ~FinancialStatement.data.has_key("SCE"),
        )
        .order_by(FinancialStatement.stock_code, FinancialStatement.period)
    ).all()
    return list(rows)


def _period_end_date(period: str) -> tuple[int, int, int]:
    """'2024.03' → (2024, 3, 31). 기말자본 라벨 날짜는 항상 말일(실측)."""
    year, month = (int(x) for x in period.split("."))
    return year, month, calendar.monthrange(year, month)[1]


# SCE leaf(발행사 XBRL 라벨)와 CFS BS name(DART 계정명)의 어휘 차이를 정규화:
# '이익잉여금(결손금)'↔'이익잉여금', '지배기업의 소유주에게 귀속되는 지분'↔'...자본'.
_SCE_COMPONENT_NORM = (
    (re.compile(r"\(결손금\)"), ""),
    (re.compile(r"\s+"), ""),
    (re.compile(r"지분"), "자본"),
)


def _norm_comp(s: str) -> str:
    for pat, rep in _SCE_COMPONENT_NORM:
        s = pat.sub(rep, s)
    return s


def _bs_balance_map(bs_items: list[dict]) -> dict[str, float]:
    """CFS BS 자본 구성요소 name → amount. 합계·총계 항목은 제외(구성요소만)."""
    out: dict[str, float] = {}
    for it in bs_items:
        name = it.get("name") or ""
        if not name or any(k in name for k in ("합계", "총계")):
            continue
        out[_norm_comp(name)] = float(it.get("amount") or 0)
    return out


def _sce_balance_map(sce_items: list[dict]) -> dict[str, float]:
    """SCE 기말자본 행 → (구성요소 leaf → amount). 합계·연결/별도재무제표 열 제외.

    행 라벨 변형: 기말자본·분기말자본·반기말자본(대형사)·기말(삼성전기)·분기말금액
    (011170) — 접미사 매칭.
    """
    out: dict[str, float] = {}
    for it in sce_items:
        if not re.search(r"(?:분|반)?기말(?:자본|금액)?$", it.get("name") or ""):
            continue
        detail = it.get("detail") or ""
        if detail in ("연결재무제표 [member]", "별도재무제표 [member]"):
            continue
        leaf = detail[: -len(" [member]")] if detail.endswith(" [member]") else detail
        if not leaf or any(k in leaf for k in ("합계", "총계")):
            continue
        out[_norm_comp(leaf)] = float(it.get("amount") or 0)
    return out


def _match_sce_table(
    candidates: list[tuple[str, list[tuple[tuple[int, int, int], list[dict]]]]],
    bs_items: list[dict],
) -> list[tuple[str, list[tuple[tuple[int, int, int], list[dict]]]]]:
    """CFS BS 자본 구성요소와 값이 일치하는 SCE 테이블(연결/별도)만 남긴다.

    CFS 행이 연결 데이터인지 별도인지는 회사·기간별로 달라(DART CFS SCE 013 때문), 기말자본
    행의 구성요소 값이 CFS BS 와 정확히 일치하는 개수로 판별한다(포인트인타임 값 — 분기 포함
    전 기간에서 정합). 1개 이상 일치 필수. 동점 시 CFS BS 에 비지배지분(연결 증거)이 있으면
    연결 테이블을 우선한다.
    """
    if not candidates:
        return candidates
    bs = _bs_balance_map(bs_items)
    has_nci = any(_norm_comp(it.get("name") or "") == "비지배자본" for it in bs_items)
    scored: list[tuple[int, str, list[tuple[tuple[int, int, int], list[dict]]]]] = []
    for table_type, blocks in candidates:
        matches = 0
        for _end_date, items in blocks:
            matches = max(
                matches,
                sum(
                    1
                    for leaf, amt in _sce_balance_map(items).items()
                    if leaf in bs and abs(bs[leaf] - amt) < 0.5
                ),
            )
        if matches:
            scored.append((matches, table_type, blocks))
    if not scored:
        return []
    best_score = max(s[0] for s in scored)
    best = [s for s in scored if s[0] == best_score]
    if len(best) > 1 and has_nci:
        cons = [s for s in best if s[1] == "consolidated"]
        if cons:
            best = cons
    return [(table_type, blocks) for _m, table_type, blocks in best]


def _run_sce_migration_for_code(
    db: Session, settings: Settings, code: str, periods: list[str], session: requests.Session
) -> int:
    """한 종목의 지정된 기간들에 SCE를 공시 원문 파싱으로 채운다. 갱신된 기간 수 반환.

    DART fnlttSinglAcntAll 재조회(013·연결/별도 혼재로 신뢰 불가) 대신 document.xml 원문의
    자본변동표 본표를 파싱한다. 종목당 list.json 1회 + 보고서별 zip 다운로드(rcept_no 캐시).
    """
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        return 0

    bgn_de = f"{min(int(p.split('.')[0]) for p in periods)}0101"
    reports = dart.find_all_periodic_reports(settings.dart_api_key, corp_code, bgn_de, session)
    if not reports:
        logger.warning("SCE migration %s: 보고서 목록 없음", code)
        return 0

    # rcept_no → zip 메모리 캐시(같은 종목 여러 기간이 같은 보고서를 공유).
    zip_cache: dict[str, bytes | None] = {}
    # (기말날짜, 연결구분) → SCE 아이템 목록. 같은 날짜가 여러 보고서(본인 기말·후기 전기·정정)에
    # 등장하므로 전부 후보로 남긴다 — _match_sce_table 이 CFS BS 와의 일치로 최선을 고른다.
    # (최신 접수만 취하면 후기 보고서의 오파싱 전기 블록이 본인 보고서의 정상 기말 블록을 가린다.)
    by_end: dict[tuple[tuple[int, int, int], str], list[list[dict]]] = {}
    for r in reports:
        rcept_no = r.get("rcept_no")
        if not rcept_no:
            continue
        if rcept_no not in zip_cache:
            zip_cache[rcept_no] = fetch_report_zip(
                settings.dart_api_key, rcept_no, session
            )
        raw = zip_cache[rcept_no]
        if not raw:
            continue
        for table_type, blocks in parse_sce_tables_from_zip(raw):
            for end_date, items in blocks:
                by_end.setdefault((end_date, table_type), []).append(items)

    updated = 0
    for period in periods:
        end_date = _period_end_date(period)
        # 보고서 미제출 기간(예: 2025.12 사업보고서 미제출)은 다음 보고서 전기 블록이 커버.
        # 같은 (날짜, 연결구분)의 모든 블록을 후보로 — 매처가 BS 일치로 최선을 고른다.
        candidates = [
            (table_type, [(d, items)])
            for (d, table_type), items_list in by_end.items()
            if d == end_date
            for items in items_list
        ]
        if not candidates:
            logger.info("SCE migration %s %s: 기말자본 %s 블록 없음 — skip", code, period, end_date)
            continue
        row = db.scalars(
            select(FinancialStatement).where(
                FinancialStatement.stock_code == code,
                FinancialStatement.period == period,
                FinancialStatement.fs_div == "CFS",
            )
        ).first()
        if not row:
            continue
        matched = _match_sce_table(candidates, row.data.get("BS", []))
        if not matched:
            logger.info("SCE migration %s %s: BS 값 불일치(연결/별도 판별 불가) — skip", code, period)
            continue
        sce = matched[0][1][0][1]
        row.data = {**row.data, "SCE": sce}
        updated += 1
        logger.info("%s %s SCE 채움(%s): %d rows", code, period, matched[0][0], len(sce))

    if updated:
        db.execute(
            FinancialStatementCache.__table__.delete().where(
                FinancialStatementCache.stock_code == code
            )
        )
        db.commit()
    return updated


def run_sce_migration(db: Session, settings: Settings | None = None, per_run: int = 500) -> dict:
    """SCE 누락 기간을 점진 마이그레이션. per_run 은 처리할 기간(row) 수.

    종목별로 모아 한 세션으로 DART 를 호출해 효율을 높인다. DART 예산/한도 감시.
    """
    settings = settings or get_settings()
    if not settings.dart_api_key:
        logger.warning("no DART key; skip SCE migration")
        return {"done": 0, "failed": 0, "remaining": 0}

    pending_rows = _sce_pending_rows(db)
    if not pending_rows:
        return {"done": 0, "failed": 0, "remaining": 0}

    # per_run 기간 수만큼 잘라 종목별 그룹화.
    batch = pending_rows[:per_run]
    by_code: dict[str, list[str]] = {}
    for code, period in batch:
        by_code.setdefault(code, []).append(period)

    done_periods = failed = 0
    quota_hit = budget_hit = False
    with requests.Session() as session:
        for code, periods in by_code.items():
            if dart_throttle.backfill_budget_exhausted():
                budget_hit = True
                logger.info("SCE migration: 백필 예산 소진 — 조기 중단")
                break
            try:
                done_periods += _run_sce_migration_for_code(db, settings, code, periods, session)
            except dart.DartQuotaExceeded:
                db.rollback()
                quota_hit = True
                logger.warning("SCE migration: DART 한도초과 — 배치 중단")
                break
            except Exception as e:
                db.rollback()
                failed += len(periods)
                logger.warning("SCE migration failed for %s: %s", code, e)

    remaining = len(pending_rows) - done_periods
    logger.info(
        "SCE migration: done=%d failed=%d remaining=%d quota_hit=%s budget_hit=%s",
        done_periods,
        failed,
        remaining,
        quota_hit,
        budget_hit,
    )
    return {
        "done": done_periods,
        "failed": failed,
        "remaining": remaining,
        "quota_hit": quota_hit,
        "budget_hit": budget_hit,
    }


def run_ofs_statements_backfill(
    db: Session, settings: Settings | None = None, per_run: int = _PER_RUN
) -> dict:
    """유니버스 종목의 별도재무제표를 점진 백필한다(하룻밤 per_run 개, 재개 가능)."""
    settings = settings or get_settings()
    if not settings.dart_api_key:
        logger.warning("no DART key; skip ofs statements backfill")
        return {"done": 0, "failed": 0, "remaining": 0}
    codes = _universe_codes(db)
    if not codes:
        return {"done": 0, "failed": 0, "remaining": 0}

    pending = _ofs_pending_codes(db, codes)
    per_run = _budget_aware_per_run(per_run)  # 남은 예산으로 상한 보정(부족 시 축소).
    batch = pending[:per_run]
    done = failed = 0
    quota_hit = budget_hit = False
    for code in batch:
        if dart_throttle.backfill_budget_exhausted():
            budget_hit = True
            logger.info(
                "ofs statements backfill: 백필 예산 소진 — 조기 중단(%d 종목 처리 후)", done
            )
            break
        try:
            if backfill_ofs_stock(db, settings, code):
                sync_state.mark(db, _OFS_STATEMENTS_DOMAIN, code)
                db.commit()
                done += 1
            else:
                failed += 1
        except dart.DartQuotaExceeded:
            db.rollback()
            quota_hit = True
            logger.warning(
                "ofs statements backfill: DART 한도초과 — 배치 중단(%d 종목 처리 후)", done
            )
            break
        except Exception as e:
            db.rollback()
            failed += 1
            logger.warning("ofs statements backfill failed for %s: %s", code, e)

    remaining = len(pending) - done
    logger.info(
        "ofs statements backfill: done=%d failed=%d remaining=%d quota_hit=%s budget_hit=%s",
        done,
        failed,
        remaining,
        quota_hit,
        budget_hit,
    )
    return {
        "done": done,
        "failed": failed,
        "remaining": remaining,
        "quota_hit": quota_hit,
        "budget_hit": budget_hit,
    }
