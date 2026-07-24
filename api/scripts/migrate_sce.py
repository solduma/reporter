#!/usr/bin/env python3
"""SCE(자본변동표) 데이터 마이그레이션 스크립트.

기존 FinancialStatement.data 에 SCE 키가 없는 행을 골라 DART 를 재조회하고,
sj_div=SCE 라인아이템을 채운 뒤 upsert 한다. API 응답 캐시도 함께 날린다.

Usage:
    cd api && uv run python -m scripts.migrate_sce <stock_code>

예) uv run python -m scripts.migrate_sce 000660
"""

from __future__ import annotations

import logging
import sys

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.adapters.dart import client as dart_client
from app.config import get_settings
from app.db.models import CorpCodeMap, FinancialStatement, FinancialStatementCache

logger = logging.getLogger(__name__)


def _quarter_from_period(period: str) -> tuple[int, int]:
    """'2026.03' -> (2026, 1). DART reprt_code 분기로 환산."""
    year_str, month_str = period.split(".")
    month = int(month_str)
    quarter = {3: 1, 6: 2, 9: 3, 12: 4}[month]
    return int(year_str), quarter


def migrate_stock(db: Session, code: str, api_key: str) -> int:
    """한 종목의 SCE 누락 기간을 DART 재조회로 채운다. 변경된 기간 수 반환."""
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code:
        logger.warning("corp_code 없음: %s", code)
        return 0

    rows = db.scalars(
        select(FinancialStatement).where(
            FinancialStatement.stock_code == code,
            ~FinancialStatement.data.has_key("SCE"),  # type: ignore[operator]
        )
    ).all()
    if not rows:
        logger.info("%s: SCE 누락 기간 없음", code)
        return 0

    updated = 0
    with requests.Session() as session:
        dart_client.configure_from_settings(get_settings())
        for row in rows:
            year, quarter = _quarter_from_period(row.period)
            fs_div = row.fs_div
            full = dart_client._fetch_full_statements_for_fs_div(
                api_key, corp_code, year, quarter, fs_div, session
            )
            sce = full.get("SCE") if full else None
            if sce:
                # SQLAlchemy JSONB 변경 감지를 위해 dict 를 새로 할당한다.
                row.data = {**row.data, "SCE": sce}
                updated += 1
                logger.info(
                    "%s %s %s SCE 채움: %d rows", code, row.period, fs_div, len(sce)
                )
            else:
                logger.info("%s %s %s: SCE 데이터 없음", code, row.period, fs_div)
    db.commit()

    if updated:
        db.execute(
            FinancialStatementCache.__table__.delete().where(
                FinancialStatementCache.stock_code == code
            )
        )
        db.commit()
        logger.info("%s: 응답 캐시 %d 기간 invalidation 완료", code, updated)
    return updated


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if len(sys.argv) != 2:
        print("Usage: uv run python -m scripts.migrate_sce <stock_code>")
        return 1

    code = sys.argv[1]
    settings = get_settings()
    engine = create_engine(settings.postgres_url)
    with Session(engine) as db:
        updated = migrate_stock(db, code, settings.dart_api_key)
    logger.info("완료: %s 기간 SCE 마이그레이션", updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
