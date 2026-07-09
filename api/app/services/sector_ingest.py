"""judal 테마(섹터)·종목 매핑 적재 — 일 1회 갱신 캐시.

judal 스크래퍼로 테마 목록·구성 종목을 긁어 sector_theme / sector_theme_stock 에
멱등 upsert 한다. 테마당 1회 HTTP 요청이라 전체 갱신은 무겁다(일 배치 전제).
"""

from __future__ import annotations

import logging
import time

import requests
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import SectorTheme, SectorThemeStock
from reporter import judal

logger = logging.getLogger(__name__)

_REQUEST_INTERVAL = 0.3  # judal 부하 완화(테마당 요청 사이 간격)


def refresh_sectors(db: Session, max_themes: int | None = None) -> int:
    """judal 테마·종목 매핑을 갱신한다. 적재한 테마 수를 반환한다.

    max_themes 로 갱신 개수를 제한할 수 있다(부분 갱신·테스트용).
    """
    session = requests.Session()
    themes = judal.fetch_themes(session)
    if not themes:
        logger.warning("judal returned no themes; skip sector refresh")
        return 0
    if max_themes:
        themes = themes[:max_themes]

    done = 0
    for theme in themes:
        stmt = insert(SectorTheme).values(
            judal_idx=theme.idx, name=theme.name, stock_count=theme.stock_count
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_sector_theme_idx",
            set_={"name": stmt.excluded.name, "stock_count": stmt.excluded.stock_count},
        )
        db.execute(stmt)

        detail = judal.fetch_theme_stocks(theme.idx, session)
        for code, stock_name in detail.stocks:
            s = insert(SectorThemeStock).values(
                judal_idx=theme.idx, stock_code=code, stock_name=stock_name
            )
            s = s.on_conflict_do_update(
                constraint="uq_theme_stock", set_={"stock_name": s.excluded.stock_name}
            )
            db.execute(s)
        db.commit()  # 테마 단위 커밋 — 중간 실패해도 앞선 테마는 보존
        done += 1
        time.sleep(_REQUEST_INTERVAL)

    logger.info("sector refresh done: %d themes", done)
    return done
