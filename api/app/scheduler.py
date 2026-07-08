"""수집 스케줄러. 별도 worker 프로세스에서 실행한다 (launchd 대체).

기존 CLI 의 launchd/cron 은 텔레그램 발송용으로 그대로 두고, 이 스케줄러는
웹서비스 DB 적재를 담당한다. 멱등 수집(read_url/조합키 dedup)이라 매 실행마다
신규 리포트만 저장·분석한다.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings, get_settings
from app.db.session import SessionLocal, init_db
from app.services import ingest

logger = logging.getLogger(__name__)

# 리포트는 장 시작 후 순차 발행되므로 넉넉히 커버한다. 멱등이라 중복 실행 무해.
_CRON = CronTrigger(day_of_week="mon-fri", hour="9-19", minute="0,30")


def run_ingest_cycle(settings: Settings | None = None) -> dict:
    """수집 1회. 스케줄러 잡이자 수동 호출 진입점. 신규 리포트 수와 시황 갱신 여부를 반환."""
    settings = settings or get_settings()
    session = SessionLocal()
    try:
        reports = ingest.ingest_reports(session, settings)
        market = ingest.build_market_brief(session, settings)
        result = {"reports_ingested": reports, "market_brief": bool(market)}
        logger.info("ingest cycle done: %s", result)
        return result
    finally:
        session.close()


def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    """잡이 등록된 스케줄러를 반환한다 (start 는 호출자가)."""
    settings = settings or get_settings()
    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        run_ingest_cycle,
        trigger=_CRON,
        id="ingest_cycle",
        max_instances=1,  # 이전 사이클이 안 끝났으면 겹쳐 실행하지 않는다
        coalesce=True,  # 슬립 등으로 밀린 실행은 1회로 합친다
        replace_existing=True,
    )
    return scheduler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    init_db()
    scheduler = build_scheduler()
    logger.info("scheduler starting (mon-fri 09-19 every 30min, Asia/Seoul)")
    scheduler.start()


if __name__ == "__main__":
    main()
