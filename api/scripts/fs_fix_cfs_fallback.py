"""CFS 갭 + 음수 discrete 매출 재백필.

대상 판정:
- 갭 종목: financials CFS revenue 가 있는데 해당 (period, fs_div) 의
  FinancialStatement 원문이 없는 종목 — fnlttSinglAcntAll 이 013(데이터없음)이라
  과거 백필이 stale 값을 남긴 구간. 새 백필은 fnlttSinglAcnt 폴백으로 채운다.
- 캐시 음수 discrete 종목: FS 원문에서 분기 개별 매출(4Q=연간-누적)이 음수인 종목 —
  새 백필이 null_fields 로 명시 NULL 을 써 기존 stale 값을 제거한다.
"""
import sys
sys.path.insert(0, "/Users/iljoyoo/workspace/reporter/api")

import logging

from sqlalchemy import text

from app.adapters.dart import client as dart
from app.config import get_settings
from app.db.session import get_session
from app.services import financials_backfill as fb, sync_state
from app.services.fs_parse import parse_income_equity_from_fs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("fs_fix_cfs_fallback")


def _target_stocks(db) -> set[str]:
    """갭 종목 ∪ 캐시 음수 discrete 종목."""
    gap = set(db.scalars(text(
        "SELECT DISTINCT f.stock_code FROM financials f "
        "LEFT JOIN financial_statements fs "
        "  ON fs.stock_code = f.stock_code AND fs.period = f.period AND fs.fs_div = f.fs_div "
        "WHERE f.fs_div = 'CFS' AND f.revenue IS NOT NULL AND fs.stock_code IS NULL"
    )))
    # 캐시(FS 원문)에서 분기 개별 매출 계산 → 음수인 종목(백필의 discrete_quarter 와 동일 규칙).
    # CFS/OFS 모두 검사 — OFS 만 음수인 종목(예: 348370 2025.09 OFS)도 stale 값 제거 대상.
    cache = {}
    for code, per, fs_div, data in db.execute(text(
        "SELECT stock_code, period, fs_div, data FROM financial_statements"
    )).all():
        try:
            fin = parse_income_equity_from_fs(data)
            r = fin.revenue if fin else None
        except Exception:
            r = None
        cache[(code, per, fs_div)] = r
    neg: set[str] = set()
    for (code, per, fs_div), r in cache.items():
        if r is None:
            continue
        if per.endswith(".12"):
            y = per[:4]
            annual = r
            qs = [cache.get((code, f"{y}.{m}", fs_div)) for m in ("03", "06", "09")]
            if annual is None or any(q is None for q in qs):
                continue
            if annual - sum(qs) < 0:
                neg.add(code)
        elif r < 0:
            neg.add(code)
    logger.info("갭 종목 %d, 캐시 음수 discrete 종목 %d", len(gap), len(neg))
    return gap | neg


def main() -> None:
    db = next(get_session())
    settings = get_settings()
    dart.configure_from_settings(settings)

    pending = sorted(_target_stocks(db))
    logger.info("재백필 대상 %d 종목", len(pending))

    done = failed = 0
    for i, code in enumerate(pending, 1):
        try:
            if fb.backfill_stock(db, settings, code):
                sync_state.mark(db, fb._BACKFILL_DOMAIN, code)
                db.commit()
                done += 1
            else:
                failed += 1
        except Exception as e:
            db.rollback()
            failed += 1
            logger.warning("failed %s: %s", code, e)
        if i % 20 == 0:
            logger.info("진행 %d/%d (done=%d failed=%d)", i, len(pending), done, failed)
    logger.info("완료: done=%d failed=%d", done, failed)


if __name__ == "__main__":
    main()
