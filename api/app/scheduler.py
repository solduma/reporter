"""수집 스케줄러. 별도 worker 프로세스에서 실행한다 (launchd 대체).

기존 CLI 의 launchd/cron 은 텔레그램 발송용으로 그대로 두고, 이 스케줄러는
웹서비스 DB 적재를 담당한다. 멱등 수집(read_url/조합키 dedup)이라 매 실행마다
신규 리포트만 저장·분석한다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.adapters import dart
from app.config import Settings, get_settings
from app.db.session import SessionLocal, init_db
from app.services import broadcast_ingest, ingest, ingest_log, intraday, universe_ingest

logger = logging.getLogger(__name__)

_TZ = "Asia/Seoul"


def _logged(job: str, fn):
    """스케줄러 잡을 감싸 실행 시간·결과를 ingest_log 에 남긴다(성공·실패 모두).

    잡 함수 자체는 수동 호출도 되므로 여기(등록 계층)서만 기록해 직접 호출은 깔끔히 둔다.
    실패해도 예외를 다시 던져 APScheduler 가 정상 처리(로그·다음 실행)하게 한다.
    """

    def _run():
        start = time.monotonic()
        # 잡 진입마다 DART 키 링을 primary 부터 재설정 → 자정 한도 회복 시 백업 대신 primary 우선.
        dart.configure_from_settings(get_settings())
        try:
            result = fn()
            ingest_log.record(
                None, job, result, duration_ms=int((time.monotonic() - start) * 1000)
            )
            return result
        except Exception as e:
            ingest_log.record(
                None, job, status="fail", detail=str(e)[:200],
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            raise

    return _run

# 리포트는 장 시작 후 순차 발행되므로 넉넉히 커버한다. 멱등이라 중복 실행 무해.
# timezone 을 트리거에 직접 지정: 지정하지 않으면 프로세스 로컬 tz(컨테이너=UTC)로
# 고정되고, BlockingScheduler(timezone=...) 는 이미 tz 를 가진 트리거를 덮어쓰지 않는다.
# (19:30 도 발화 — 장 마감 후 늦게 올라오는 리포트를 잡기 위해 의도한 것.)
_CRON = CronTrigger(day_of_week="mon-fri", hour="9-19", minute="0,30", timezone=_TZ)
# 야간 배치: 마감 후 18시 유니버스 스냅샷 + 성장지표(재무·모멘텀, ~20분).
_NIGHTLY_CRON = CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=_TZ)
# 봉 배치: 유니버스 스냅샷(18시) 이후 19:30 에 전 종목 일/주/30분봉 증분 + 변동 시 재적재.
_CANDLE_CRON = CronTrigger(day_of_week="mon-fri", hour=19, minute=30, timezone=_TZ)
# 장중 스크리너 선반영: 09:00~15:30 매 30분 + 마감 직후 15:40(확정봉) 1회. 스냅샷을 오늘로
# 전진하고 전 종목 오늘 일봉을 갱신해 스크리너·상세 점수를 일치시킨다. 6워커 병렬로 ~2~3분.
# 무인증 네이버 연타(밴)를 피하려 30분 간격을 넘기지 않는다. coalesce 로 밀린 실행은 1회 합침.
# 15:40 은 종가 단일가매매(15:20~15:30 체결)가 확정된 뒤라 진짜 종가로 마무리한다(19:30 야간
# 배치가 최종 재확정하지만, 마감~19:30 사이 스크리너를 확정봉으로 채워 상세와 어긋나지 않게).
_INTRADAY_REFRESH_CRON = CronTrigger(
    day_of_week="mon-fri", hour="9-15", minute="0,30", timezone=_TZ
)
_INTRADAY_CLOSE_CRON = CronTrigger(day_of_week="mon-fri", hour=15, minute=40, timezone=_TZ)
# 10년 일봉 병렬 백필: 매일 02:00. 미완 종목을 네이버 스레드풀로 전량 조회(재개 가능).
# 봉 배치(19:30~)는 전 종목 순회라 한 시간을 훌쩍 넘길 수 있어, 겹치면 네이버 동시 호출이
# 몰리고 price_candles 를 함께 변경한다. 깊은 새벽으로 빼 저녁 배치와 확실히 분리한다.
# 주말도 실행해(장 없어도) 중단분 재개·신규 상장 종목을 채운다. 완료되면 즉시 종료(무부하).
_BACKFILL_CRON = CronTrigger(hour=2, minute=0, timezone=_TZ)
# 10년 재무·밸류(PER/PBR/PSR) 점진 백필: 매일 03:30. 종목당 40분기 DART 콜이라 무거워
# 일봉 백필(02:00)과 시차를 둔다. sync_state 로 재개 가능, 완료되면 즉시 종료.
_FIN_BACKFILL_CRON = CronTrigger(hour=3, minute=30, timezone=_TZ)
# 관계사(모/자회사) 수집: 매일 04:30. 종목당 DART 2콜이라 가벼워 재무 백필(03:30) 뒤. 웹서치
# 관련성 판정 alias 원천. sync_state 로 재개 가능.
_RELATED_BACKFILL_CRON = CronTrigger(hour=4, minute=30, timezone=_TZ)
# 보고서 원문 파싱 백필(정밀 감가상각·EV/EBITDA): 매일 05:00. 보고서당 document.xml(수MB)
# 다운로드라 가장 무거워 재무 백필(03:30) 이후로 뺀다. sync_state 로 재개 가능.
_REPORT_BACKFILL_CRON = CronTrigger(hour=5, minute=0, timezone=_TZ)
# 리포트 원문(full_text) 소급 적재: 매일 05:30. 컬럼 추가 이전 리포트를 MinIO PDF 에서 회당 60건씩
# 채운다(재개 가능). 리포트 파싱 백필(05:00) 직후, 뉴스(07:00) 이전.
_REPORT_FULLTEXT_CRON = CronTrigger(hour=5, minute=30, timezone=_TZ)
# 매크로/뉴스 이벤트 분류: 매일 07:00. 뉴스 수집 → LLM 분류 → 테마 구성종목 전파(StockEvent).
# LLM 토큰을 쓰므로 하루 1회. 이벤트드리븐 스크리너의 '뉴스' 이벤트 소스.
_NEWS_EVENTS_CRON = CronTrigger(hour=7, minute=0, timezone=_TZ)
# US 배치는 미국 장 마감(16시 ET ≈ 06시 KST) 후. 유니버스 스냅샷 → 일봉 백필 → 8-K 순.
_US_UNIVERSE_CRON = CronTrigger(hour=6, minute=10, timezone=_TZ)
# US 일봉 10년 점진 백필: 06:20. 유니버스 스냅샷(06:10) 직후 — 최신 심볼 대상. momentum_3m 채움.
_US_CANDLE_BACKFILL_CRON = CronTrigger(hour=6, minute=20, timezone=_TZ)
_US_DISCLOSURE_CRON = CronTrigger(hour=6, minute=40, timezone=_TZ)
# US 재무(SEC EDGAR) 점진 백필: 07:10. 유니버스(06:10)·공시(06:40) 뒤, 종목당 SEC 콜이 많아
# sync_state 로 재개하며 per_run 씩 채운다(전 유니버스 커버까지 며칠).
_US_FINANCIALS_CRON = CronTrigger(hour=7, minute=10, timezone=_TZ)
# 국내 공시 순환 정기 배치: 매일 07:40. 유니버스를 오래된 순으로 per_run 개씩 최근 창 동기화
# (몇 밤에 걸쳐 전수 순환). DART 콜이라 재무·리포트 백필(03:30·05:00)과 시차를 두고, 뉴스(07:00)
# 뒤에 둔다. 온디맨드 타임라인 조회와 같은 DisclosureSyncState 캐시를 공유(중복 조회 방지).
_DISCLOSURE_CRON = CronTrigger(hour=7, minute=40, timezone=_TZ)
# 경제·실적 캘린더: 매일 06:50. FRED 발표일은 자주 안 바뀌므로 하루 1회면 충분. 미국 지표
# 발표(대개 밤)가 반영되도록 아침에 돌려 당일 과거 이벤트에 실적치·LLM 영향이 채워지게 한다.
_CALENDAR_CRON = CronTrigger(hour=6, minute=50, timezone=_TZ)
_RISK_FREE_CRON = CronTrigger(hour=6, minute=45, timezone=_TZ)  # 무위험금리(ECOS 국고채) 매일 1회
_MARKET_PREMIUM_CRON = CronTrigger(day=1, hour=6, minute=55, timezone=_TZ)  # ERP(Damodaran) 월 1회


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
    """매일 저녁 봉 배치: 유니버스 전 종목 일/주/30분봉 증분 + 주식변동 시 전체 재적재.

    봉 갱신 직후 RS Rating(전종목 가격 모멘텀 백분위)을 최신 종가로 다시 매긴다.
    """
    from app.services import candle_ingest, rs_rating_ingest, trend  # 무거운 의존성 → 지연 임포트

    session = SessionLocal()
    try:
        result = candle_ingest.run_candle_batch(session, settings)
        rs = rs_rating_ingest.run_rs_rating_batch(session)
        # 확정봉 기준 /trend 응답 사전계산(상세페이지 매 요청 재계산 제거).
        tc = trend.run_trend_precompute_batch(session)
        return {**result, "rs_rating": rs, "trend_precompute": tc}
    finally:
        session.close()


def run_intraday_refresh(settings: Settings | None = None) -> dict:
    """장중 스크리너 선반영: 스냅샷을 오늘로 전진 + 전 종목 오늘 일봉 갱신 + 추세/RS/모멘텀 재계산.

    스크리너는 UniverseSnapshot 을 읽고, 상세페이지는 오늘 일봉으로 추세를 라이브 재계산한다.
    이 사이클이 (1) 스냅샷을 오늘로 올려 최신 시세·거래대금을 반영하고, (2) 전 종목 오늘 일봉을
    price_candles 에 써서 상세페이지의 자체 fetch(is_stale)를 없애 배치와 같은 봉을 읽게 하고,
    (3) 무네트워크로 추세·RS·모멘텀을 재계산해 스냅샷에 폴딩한다 → 스크리너·상세 점수가 일치한다.
    """
    from app.services import candle_ingest, rs_rating_ingest  # 무거운 의존성 → 지연 임포트

    session = SessionLocal()
    try:
        today = datetime.now().date()
        # 순서 중요: 가장 긴 일봉 갱신(~2~3분)을 스냅샷이 아직 완전한 어제 상태일 때 먼저 돌려
        # 스크리너가 그동안 어제 값으로 정합하게 보이게 한다. 그 뒤 스냅샷을 오늘로 올리고
        # 곧바로 재계산해, 오늘 행의 파생필드(추세·RS·모멘텀)가 비는 창을 ~1분으로 줄인다.
        candles = candle_ingest.refresh_today_day_candles(session, settings)
        rows = universe_ingest.snapshot_universe(session, today)
        rs = rs_rating_ingest.run_rs_rating_batch(session, with_momentum=True)
        result = {"candles": candles, "universe_rows": rows, "rs_trend": rs}
        logger.info("intraday refresh done: %s", result)
        return result
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


def run_financials_backfill(settings: Settings | None = None) -> dict:
    """10년 재무·밸류 점진 백필 1회분(미완 종목 per_run 개). 여러 밤에 걸쳐 전체 완성."""
    from app.services import financials_backfill

    session = SessionLocal()
    try:
        return financials_backfill.run_backfill_progressive(session, settings)
    finally:
        session.close()


def run_related_company_backfill(settings: Settings | None = None) -> dict:
    """관계사(모/자회사) 수집 1회분(미완 종목 per_run 개). 여러 밤에 걸쳐 전체 완성."""
    from app.services import related_company_ingest

    session = SessionLocal()
    try:
        return related_company_ingest.run_backfill_progressive(session, settings)
    finally:
        session.close()


def run_report_backfill(settings: Settings | None = None) -> dict:
    """보고서 원문 파싱 백필 1회분(정밀 감가상각·EV/EBITDA). 여러 밤에 걸쳐 전체 완성."""
    from app.services import report_ingest

    session = SessionLocal()
    try:
        return report_ingest.run_backfill_progressive(session, settings)
    finally:
        session.close()


def run_report_fulltext_backfill(settings: Settings | None = None) -> dict:
    """리포트 원문(full_text) 소급 적재 1회분 — 컬럼 추가 이전 리포트를 MinIO PDF 에서 채운다."""
    session = SessionLocal()
    try:
        return ingest.backfill_full_text(session, settings)
    finally:
        session.close()


def run_news_events(settings: Settings | None = None) -> dict:
    """매크로/뉴스 수집·LLM 분류·테마 전파 → StockEvent 적재(이벤트드리븐 스크리너 소스)."""
    from app.services import news_events

    session = SessionLocal()
    try:
        return news_events.run_news_events(session, settings)
    finally:
        session.close()


def run_disclosure_batch(settings: Settings | None = None) -> dict:
    """국내 공시 순환 정기 동기화 1회분(오래된 순 per_run 개, 최근 창). 여러 밤에 걸쳐 전수 순환."""
    from app.services import dart_ingest

    session = SessionLocal()
    try:
        return dart_ingest.run_disclosure_batch(session, settings or get_settings())
    finally:
        session.close()


def run_us_universe_batch(settings: Settings | None = None) -> dict:
    """US 유니버스 스냅샷(S&P500+보충 네이버 시세). US 스크리너 소스."""
    from app.services import us_universe_ingest

    session = SessionLocal()
    try:
        return us_universe_ingest.snapshot_us_universe(session)
    finally:
        session.close()


def run_us_candle_backfill(settings: Settings | None = None) -> dict:
    """US 유니버스 일봉 10년 점진 백필 1회분(재개 가능) + momentum_3m 재계산."""
    from app.services import us_universe_ingest

    session = SessionLocal()
    try:
        return us_universe_ingest.run_candle_backfill_progressive(session)
    finally:
        session.close()


def run_deepdive_queue(settings: Settings | None = None) -> dict:
    """딥다이브 DB 폴링 큐 — pending job 1건을 잡아 5단계 파이프라인 실행(직렬).

    짧은 interval 로 폴링. 실행 중(오래 걸리는 job)엔 다음 tick 이 겹치지 않게 max_instances=1 로
    등록한다. pending 없으면 즉시 반환(부하 0).
    """
    from app.services.deepdive import orchestrator

    session = SessionLocal()
    try:
        job = orchestrator.claim_next(session)
        if job is None:
            return {"claimed": 0}
        orchestrator.run_job(session, job, settings or get_settings())
        return {"claimed": 1, "job_id": job.id, "code": job.stock_code, "status": job.status}
    finally:
        session.close()


def run_ir_interview_queue(settings: Settings | None = None) -> dict:
    """주담(IR) 인터뷰 DB 폴링 큐 — pending job 1건을 잡아 에이전틱 파이프라인 실행(직렬).

    딥다이브와 독립된 큐. 짧은 interval 폴링, max_instances=1 로 겹침 방지. pending 없으면 부하 0."""
    from app.services import ir_interview

    session = SessionLocal()
    try:
        job = ir_interview.claim_next(session)
        if job is None:
            return {"claimed": 0}
        job.model = (settings or get_settings()).insight_model
        session.commit()
        ir_interview.run_job(session, job, settings or get_settings())
        return {"claimed": 1, "job_id": job.id, "code": job.stock_code, "status": job.status}
    finally:
        session.close()


def run_us_disclosure_batch(settings: Settings | None = None) -> dict:
    """US 유니버스 종목의 최근 SEC 8-K 수집."""
    from app.services import us_disclosure_ingest

    session = SessionLocal()
    try:
        return us_disclosure_ingest.run_us_disclosure_batch(session, settings)
    finally:
        session.close()


def run_us_financials_backfill(settings: Settings | None = None) -> dict:
    """US 유니버스 종목의 SEC 재무를 점진 백필(재개 가능)."""
    from app.services import us_company_service

    session = SessionLocal()
    try:
        return us_company_service.run_financials_backfill(session, settings)
    finally:
        session.close()


def run_calendar_batch(settings: Settings | None = None) -> dict:
    """경제/실적 캘린더 수집(FRED 미국 매크로 + 고정일정) + LLM 영향/기대치 텍스트 + 사후 업데이트."""
    from app.services import calendar_ingest, calendar_llm

    session = SessionLocal()
    try:
        counts = calendar_ingest.ingest_calendar(session, settings)
        # 금통위 등 고정 이벤트 사후 actual 채움(ECOS 기준금리)
        counts["post_updated"] = calendar_ingest.update_past_fixed_events(session, settings)
        counts["texts"] = calendar_llm.generate_pending(session, settings)
        return counts
    finally:
        session.close()


def run_risk_free_batch(settings: Settings | None = None) -> dict:
    """무위험수익률 수집(ECOS 국고채 3년/10년) — DCF·factor model 이 상수 대신 최신 시장금리 사용."""
    from app.services import risk_free_ingest

    session = SessionLocal()
    try:
        return risk_free_ingest.ingest_risk_free_rates(session, settings)
    finally:
        session.close()


def run_capex_backfill(settings: Settings | None = None) -> dict:
    """CAPEX 경량 백필(구조화 API) — 기존 연간 재무행의 capex 결측 채움(진짜 FCFF 산출용)."""
    from app.services import report_ingest

    session = SessionLocal()
    try:
        return report_ingest.backfill_capex(session, settings)
    finally:
        session.close()


def run_market_premium_batch(settings: Settings | None = None) -> dict:
    """시장 ERP 수집(Damodaran) — CAPM COE 가 상수 대신 실측 ERP 사용."""
    from app.services import market_premium_ingest

    session = SessionLocal()
    try:
        return market_premium_ingest.ingest_erp(session)
    finally:
        session.close()


# 수동 실행 가능한 배치 레지스트리 — (key, 표시명, 함수). TUI '운영' 탭이 이 목록으로 버튼을 만든다.
# 함수는 (settings) → dict 시그니처로 통일돼 있어 TUI 가 일괄 실행·이력 기록한다.
MANUAL_BATCHES: list[tuple[str, str, object]] = [
    ("ingest_cycle", "리포트·시황 수집", run_ingest_cycle),
    ("candle_batch", "일봉 수집", run_candle_batch),
    ("intraday_refresh", "30분봉 갱신", run_intraday_refresh),
    ("nightly_batch", "성장·RS 야간배치", run_nightly_batch),
    ("news_events", "뉴스·종목이벤트", run_news_events),
    ("disclosure_batch", "공시 수집", run_disclosure_batch),
    ("financials_backfill", "재무 백필(10년)", run_financials_backfill),
    ("related_company", "관계사 수집", run_related_company_backfill),
    ("report_backfill", "리포트 백필(10년)", run_report_backfill),
    ("report_fulltext", "리포트 원문 소급적재", run_report_fulltext_backfill),
    ("backfill_progressive", "일봉 백필(10년)", run_backfill_progressive),
    ("us_universe", "US 유니버스", run_us_universe_batch),
    ("us_candle_backfill", "US 일봉 백필(10년)", run_us_candle_backfill),
    ("us_disclosure", "US 공시(8-K)", run_us_disclosure_batch),
    ("us_financials", "US 재무 백필(SEC)", run_us_financials_backfill),
    ("calendar", "경제·실적 캘린더", run_calendar_batch),
    ("risk_free", "무위험금리(국고채)", run_risk_free_batch),
    ("capex_backfill", "CAPEX 백필(FCFF)", run_capex_backfill),
    ("market_premium", "시장 ERP(Damodaran)", run_market_premium_batch),
]


def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    """잡이 등록된 스케줄러를 반환한다 (start 는 호출자가)."""
    settings = settings or get_settings()
    scheduler = BlockingScheduler(timezone=_TZ)
    scheduler.add_job(
        _logged("ingest_cycle", run_ingest_cycle),
        trigger=_CRON,
        id="ingest_cycle",
        max_instances=1,  # 이전 사이클이 안 끝났으면 겹쳐 실행하지 않는다
        coalesce=True,  # 슬립 등으로 밀린 실행은 1회로 합친다
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("nightly_batch", run_nightly_batch),
        trigger=_NIGHTLY_CRON,
        id="nightly_batch",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("candle_batch", run_candle_batch),
        trigger=_CANDLE_CRON,
        id="candle_batch",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("intraday_refresh", run_intraday_refresh),
        trigger=_INTRADAY_REFRESH_CRON,
        id="intraday_refresh",
        max_instances=1,  # 이전 사이클(~2~3분)이 안 끝났으면 겹쳐 실행하지 않는다
        coalesce=True,  # 슬립 등으로 밀린 실행은 1회로 합친다
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("intraday_refresh", run_intraday_refresh),
        trigger=_INTRADAY_CLOSE_CRON,
        id="intraday_close",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("backfill_10y", run_backfill_progressive),
        trigger=_BACKFILL_CRON,
        id="backfill_10y",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("financials_10y", run_financials_backfill),
        trigger=_FIN_BACKFILL_CRON,
        id="financials_10y",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("related_company", run_related_company_backfill),
        trigger=_RELATED_BACKFILL_CRON,
        id="related_company",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("report_10y", run_report_backfill),
        trigger=_REPORT_BACKFILL_CRON,
        id="report_10y",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("report_fulltext", run_report_fulltext_backfill),
        trigger=_REPORT_FULLTEXT_CRON,
        id="report_fulltext",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("news_events", run_news_events),
        trigger=_NEWS_EVENTS_CRON,
        id="news_events",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("us_universe", run_us_universe_batch),
        trigger=_US_UNIVERSE_CRON,
        id="us_universe",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("us_candle_backfill", run_us_candle_backfill),
        trigger=_US_CANDLE_BACKFILL_CRON,
        id="us_candle_backfill",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("us_disclosure", run_us_disclosure_batch),
        trigger=_US_DISCLOSURE_CRON,
        id="us_disclosure",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("us_financials", run_us_financials_backfill),
        trigger=_US_FINANCIALS_CRON,
        id="us_financials",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("disclosures", run_disclosure_batch),
        trigger=_DISCLOSURE_CRON,
        id="disclosures",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("calendar", run_calendar_batch),
        trigger=_CALENDAR_CRON,
        id="calendar",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("risk_free", run_risk_free_batch),
        trigger=_RISK_FREE_CRON,
        id="risk_free",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _logged("market_premium", run_market_premium_batch),
        trigger=_MARKET_PREMIUM_CRON,
        id="market_premium",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # 딥다이브 큐 — 짧은 interval 폴링. 긴 job(수분) 실행 중엔 다음 tick 겹치지 않게 max_instances=1.
    scheduler.add_job(
        _logged("deepdive_queue", run_deepdive_queue),
        trigger=IntervalTrigger(seconds=15, timezone=_TZ),
        id="deepdive_queue",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # 주담(IR) 인터뷰 큐 — 딥다이브와 독립된 폴링 큐(별개 호흡).
    scheduler.add_job(
        _logged("ir_interview_queue", run_ir_interview_queue),
        trigger=IntervalTrigger(seconds=15, timezone=_TZ),
        id="ir_interview_queue",
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
