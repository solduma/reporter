"""judal 테마(섹터)·종목 매핑 적재 — 일 1회 갱신 캐시.

judal 스크래퍼로 테마 목록·구성 종목을 긁어 sector_theme / sector_theme_stock 에
멱등 upsert 한다. 테마당 1회 HTTP 요청이라 전체 갱신은 무겁다(일 배치 전제).
"""

from __future__ import annotations

import logging
import time

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import SectorTheme, SectorThemeStock
from reporter import judal, sector_etf

logger = logging.getLogger(__name__)


def sector_stock_codes(db: Session, sector: str) -> list[str]:
    """섹터명(ETF 섹터명 또는 산업명)에 속하는 judal 테마 종목코드 목록.

    산업명을 대표 섹터로 접고(예: 'IT'→IT, '게임'→IT), 같은 섹터로 분류되는 judal
    테마의 종목을 모은다('반도체'와 '반도체 소부장'을 섞지 않는다). 대표 섹터로 접히면
    전체 테마를 분류해 매칭하고(테마명이 산업명과 안 겹쳐도 잡힘), 아니면 이름 부분일치.
    """
    target = sector_etf.themes_to_kr_sector([sector])
    all_themes = db.execute(select(SectorTheme.judal_idx, SectorTheme.name)).all()
    # 1순위: 테마명 부분일치(산업명에 정확 — '게임'은 게임 테마만). 단 대표섹터가 있으면
    # 그 섹터로 분류되는 것만 남겨 반도체/소부장 분리를 유지한다.
    name_hits = [(idx, name) for idx, name in all_themes if sector in name]
    if target:
        theme_idxs = [idx for idx, name in name_hits if sector_etf.themes_to_kr_sector([name]) == target]
        if not theme_idxs:
            # 이름이 안 겹치는 합성/영문 섹터명('반도체 소부장','IT')은 대표 섹터 분류 전체로.
            theme_idxs = [
                idx for idx, name in all_themes if sector_etf.themes_to_kr_sector([name]) == target
            ]
    else:
        theme_idxs = [idx for idx, _ in name_hits]
    if not theme_idxs:
        return []
    return list(
        db.scalars(
            select(SectorThemeStock.stock_code)
            .where(SectorThemeStock.judal_idx.in_(theme_idxs))
            .distinct()
        ).all()
    )

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
