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

import logging
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import (
    CorpCodeMap,
    Financial,
    PriceCandle,
    SyncState,
    Timeframe,
    UniverseSnapshot,
)
from app.services import dart, quote, sync_state

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


def _discrete(annual_cumulative: dict[tuple[int, int], float | None], yq: tuple[int, int]) -> float | None:
    """DART 값을 분기 개별값으로 환산. 1~3Q 는 그대로(당기 3개월), 4Q=연간-(1Q+2Q+3Q)."""
    year, q = yq
    val = annual_cumulative.get(yq)
    if val is None:
        return None
    if q != 4:
        return val
    parts = [annual_cumulative.get((year, i)) for i in (1, 2, 3)]
    if any(p is None for p in parts):
        return None
    return val - sum(parts)


def _ttm(discrete: dict[tuple[int, int], float | None], yq: tuple[int, int]) -> float | None:
    """yq 포함 연속 4개 분기 개별값 합(TTM). 하나라도 결측이면 None."""
    total = 0.0
    year, q = yq
    for _ in range(4):
        v = discrete.get((year, q))
        if v is None:
            return None
        total += v
        year, q = (year - 1, 4) if q == 1 else (year, q - 1)
    return total


def _period_str(year: int, quarter: int) -> str:
    return f"{year}.{_QUARTER_MONTH[quarter]:02d}"


def backfill_stock(db: Session, settings: Settings, code: str) -> bool:
    """한 종목의 10년 분기 재무·PER/PBR/PSR 을 계산해 Financial 에 upsert 한다.

    성공(또는 데이터없음 확정) 시 True — 호출측이 완료 마킹한다. 일시 실패면 False(재시도).
    """
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        return True  # 매핑 없음(비상장 등) → 완료 처리(재시도 불필요)

    session = requests.Session()
    today = datetime.now(UTC).date()
    yqs = _target_year_quarters(today)

    # DART 원자료 수집(account_id 매칭). 값 없는 분기는 건너뛴다.
    rev_raw: dict[tuple[int, int], float | None] = {}
    ni_raw: dict[tuple[int, int], float | None] = {}
    eps_raw: dict[tuple[int, int], float | None] = {}
    equity: dict[tuple[int, int], float | None] = {}
    any_data = False
    for year, q in yqs:
        fin = dart.fetch_income_and_equity(settings.dart_api_key, corp_code, year, q, session)
        if fin is None:
            continue
        any_data = True
        rev_raw[(year, q)] = fin.revenue
        ni_raw[(year, q)] = fin.net_income
        eps_raw[(year, q)] = fin.eps
        equity[(year, q)] = fin.equity

    if not any_data:
        return True  # 재무 공시 없음 → 완료 처리

    # 분기 개별값 환산(4Q=연간-누적). 매출·순이익은 총액(원), EPS 는 표시용.
    rev_q = {yq: _discrete(rev_raw, yq) for yq in rev_raw}
    ni_q = {yq: _discrete(ni_raw, yq) for yq in ni_raw}
    eps_q = {yq: _discrete(eps_raw, yq) for yq in eps_raw}

    shares = quote.fetch_shares_outstanding(code, session)

    updated = 0
    for year, q in yqs:
        yq = (year, q)
        if yq not in rev_raw:
            continue
        close = _quarter_end_close(db, code, year, q)
        # 과거 시총 근사 = 분기말 수정종가 x 현재 주식수(수정주가라 분할 소급 상쇄).
        cap = (close * shares) if (close and shares) else None
        ttm_ni = _ttm(ni_q, yq)  # 원(총액)
        ttm_rev = _ttm(rev_q, yq)  # 원(총액)
        eq = equity.get(yq)  # 지배자본(원, 시점값)

        # 총액 기준(분할 무관): PER=시총/순이익, PBR=시총/자본, PSR=시총/매출.
        per = round(cap / ttm_ni, 2) if (cap and ttm_ni and ttm_ni > 0) else None
        pbr = round(cap / eq, 2) if (cap and eq and eq > 0) else None
        psr = round(cap / ttm_rev, 2) if (cap and ttm_rev and ttm_rev > 0) else None
        # BPS 표시용(현재 주식수 기준 근사, 원).
        bps = (eq / shares) if (eq and shares) else None

        rev_q_val = rev_q.get(yq)
        ni_q_val = ni_q.get(yq)
        # 표시 단위: 매출·순이익은 억원(기존 quote 저장 단위와 일치), EPS/BPS 는 원.
        _upsert_financial(
            db,
            code,
            _period_str(year, q),
            revenue=(rev_q_val / 1e8) if rev_q_val is not None else None,
            net_income=(ni_q_val / 1e8) if ni_q_val is not None else None,
            eps=eps_q.get(yq),
            bps=bps,
            per=per,
            pbr=pbr,
            psr=psr,
        )
        updated += 1

    db.commit()
    logger.info("financials 10y backfill %s: %d periods (shares=%s)", code, updated, shares)
    return True


def _upsert_financial(db: Session, code: str, period: str, **vals) -> None:
    """Financial 행 upsert(백필 소유 필드만 갱신: 재무·PER/PBR/PSR). 추정치 아님."""
    stmt = insert(Financial).values(stock_code=code, period=period, is_estimate=False, **vals)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_financial",
        set_={k: getattr(stmt.excluded, k) for k in vals},
    )
    db.execute(stmt)


# ── 야간 점진 백필 (재개 가능) ─────────────────────────────────────────
_PER_RUN = 100  # 종목당 40분기 DART 콜이라 무겁다 → 하룻밤 소수만


def _universe_codes(db: Session) -> list[str]:
    as_of = db.scalar(select(func.max(UniverseSnapshot.snapshot_date)))
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

    pending = [c for c in codes if c not in _done_codes(db)]
    batch = pending[:per_run]
    done = failed = 0
    for code in batch:
        try:
            if backfill_stock(db, settings, code):
                sync_state.mark(db, _BACKFILL_DOMAIN, code)
                db.commit()
                done += 1
            else:
                failed += 1
        except Exception as e:  # 한 종목 실패가 배치를 막지 않도록
            db.rollback()
            failed += 1
            logger.warning("financials 10y backfill failed for %s: %s", code, e)

    remaining = len(pending) - done
    logger.info("financials 10y backfill: done=%d failed=%d remaining=%d", done, failed, remaining)
    return {"done": done, "failed": failed, "remaining": remaining}
