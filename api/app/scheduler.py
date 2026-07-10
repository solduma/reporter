"""수집 스케줄러. 별도 worker 프로세스에서 실행한다 (launchd 대체).

기존 CLI 의 launchd/cron 은 텔레그램 발송용으로 그대로 두고, 이 스케줄러는
웹서비스 DB 적재를 담당한다. 멱등 수집(read_url/조합키 dedup)이라 매 실행마다
신규 리포트만 저장·분석한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings, get_settings
from app.db.session import SessionLocal, init_db
from app.services import broadcast_ingest, ingest, intraday, universe_ingest

logger = logging.getLogger(__name__)

_TZ = "Asia/Seoul"

# 리포트는 장 시작 후 순차 발행되므로 넉넉히 커버한다. 멱등이라 중복 실행 무해.
# timezone 을 트리거에 직접 지정: 지정하지 않으면 프로세스 로컬 tz(컨테이너=UTC)로
# 고정되고, BlockingScheduler(timezone=...) 는 이미 tz 를 가진 트리거를 덮어쓰지 않는다.
# (19:30 도 발화 — 장 마감 후 늦게 올라오는 리포트를 잡기 위해 의도한 것.)
_CRON = CronTrigger(day_of_week="mon-fri", hour="9-19", minute="0,30", timezone=_TZ)
# 야간 배치: 마감 후 18시 유니버스 스냅샷 + 성장지표(재무·모멘텀, ~20분).
_NIGHTLY_CRON = CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=_TZ)
# 봉 배치: 유니버스 스냅샷(18시) 이후 19:30 에 전 종목 일/주/30분봉 증분 + 변동 시 재적재.
_CANDLE_CRON = CronTrigger(day_of_week="mon-fri", hour=19, minute=30, timezone=_TZ)
# 10년 일봉 점진 백필: 매일 02:00, 미완 종목 per_run 개씩(재개 가능).
# 봉 배치(19:30~)는 전 종목 순회라 한 시간을 훌쩍 넘길 수 있어, 겹치면 네이버 동시 호출이
# 두 배가 되고 price_candles 를 함께 변경한다. 깊은 새벽으로 빼 저녁 배치와 확실히 분리한다.
# 주말도 실행해 전체 완성을 앞당긴다(새 봉은 없지만 미완 종목 백필은 요일 무관).
_BACKFILL_CRON = CronTrigger(hour=2, minute=0, timezone=_TZ)


def run_ingest_cycle(settings: Settings | None = None) -> dict:
    """수집 1회. 스케줄러 잡이자 수동 호출 진입점. 신규 리포트 수와 시황 갱신 여부를 반환."""
    settings = settings or get_settings()
    session = SessionLocal()
    try:
        reports = ingest.ingest_reports(session, settings)
        market = ingest.build_market_brief(session, settings)
        # 30분봉 누적: 네이버 분봉 보존이 짧아 매 거래일 30분마다 쌓아 2주 윈도우를 완성한다.
        intraday_codes = intraday.accumulate_intraday(session)
        # CLI 텔레그램 발송이 남긴 브로드캐스트 스풀을 DB 로 흡수(멱등).
        broadcasts = broadcast_ingest.ingest_broadcasts(session, settings)
        result = {
            "reports_ingested": reports,
            "market_brief": bool(market),
            "intraday_codes": intraday_codes,
            "broadcasts_ingested": broadcasts,
        }
        logger.info("ingest cycle done: %s", result)
        return result
    finally:
        session.close()


def run_nightly_batch(settings: Settings | None = None) -> dict:
    """야간 배치: 유니버스 스냅샷 + 성장지표 + judal 섹터 매핑. 스크리너·섹터 데이터 갱신."""
    from app.services import growth_ingest, sector_ingest  # 무거운 의존성 → 지연 임포트

    session = SessionLocal()
    try:
        today = datetime.now().date()
        rows = universe_ingest.snapshot_universe(session, today)
        growth = growth_ingest.run_growth_batch(session)
        # judal 섹터·종목 매핑 갱신(테마당 1요청이라 느림 → 야간 배치에서 처리).
        sectors = sector_ingest.refresh_sectors(session)
        result = {"universe_rows": rows, "growth": growth, "sectors": sectors}
        logger.info("nightly batch done: %s", result)
        return result
    finally:
        session.close()


def run_candle_batch(settings: Settings | None = None) -> dict:
    """매일 저녁 봉 배치: 유니버스 전 종목 일/주/30분봉 증분 + 주식변동 시 전체 재적재."""
    from app.services import candle_ingest  # 무거운 의존성 → 지연 임포트

    session = SessionLocal()
    try:
        return candle_ingest.run_candle_batch(session, settings)
    finally:
        session.close()


def run_backfill_progressive(settings: Settings | None = None) -> dict:
    """10년 일봉 점진 백필 1회분(미완 종목 per_run 개). 여러 밤에 걸쳐 전체 완성."""
    from app.services import candle_ingest

    session = SessionLocal()
    try:
        return candle_ingest.run_backfill_progressive(session, settings)
    finally:
        session.close()


def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    """잡이 등록된 스케줄러를 반환한다 (start 는 호출자가)."""
    settings = settings or get_settings()
    scheduler = BlockingScheduler(timezone=_TZ)
    scheduler.add_job(
        run_ingest_cycle,
        trigger=_CRON,
        id="ingest_cycle",
        max_instances=1,  # 이전 사이클이 안 끝났으면 겹쳐 실행하지 않는다
        coalesce=True,  # 슬립 등으로 밀린 실행은 1회로 합친다
        replace_existing=True,
    )
    scheduler.add_job(
        run_nightly_batch,
        trigger=_NIGHTLY_CRON,
        id="nightly_batch",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_candle_batch,
        trigger=_CANDLE_CRON,
        id="candle_batch",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_backfill_progressive,
        trigger=_BACKFILL_CRON,
        id="backfill_10y",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    init_db()
    # 워커도 ingest 중 폴백(차트 네이버→KIS, 마감시황→전체 등)을 일으키므로 DB sink 등록.
    from app.services import fallback_store
    from reporter import fallback

    fallback.register_sink(fallback_store.db_sink)
    scheduler = build_scheduler()
    logger.info("scheduler starting (mon-fri 09-19 every 30min, Asia/Seoul)")
    scheduler.start()


if __name__ == "__main__":
    main()
