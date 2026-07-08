"""스케줄러 단위 테스트 — 잡 등록(cron 설정)과 수집 사이클 호출을 검증한다.

실제 DB·네트워크·GLM 없이 ingest 함수와 세션을 목킹한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from apscheduler.triggers.cron import CronTrigger

from app import scheduler


def _settings() -> MagicMock:
    return MagicMock()


def test_build_scheduler_registers_ingest_job():
    sched = scheduler.build_scheduler(_settings())
    job = sched.get_job("ingest_cycle")
    assert job is not None
    assert job.max_instances == 1
    assert isinstance(job.trigger, CronTrigger)


def test_cron_fields_are_weekday_business_hours_every_30min():
    fields = {f.name: str(f) for f in scheduler._CRON.fields}
    assert fields["day_of_week"] == "mon-fri"
    assert fields["hour"] == "9-19"
    assert fields["minute"] == "0,30"


def test_cron_trigger_is_seoul_timezone():
    # tz 를 트리거에 직접 지정하지 않으면 프로세스 로컬(컨테이너=UTC)로 고정되어
    # KST 09-19 시가 아니라 UTC 09-19 시에 실행되는 회귀 방지.
    assert str(scheduler._CRON.timezone) == "Asia/Seoul"


def test_registered_job_keeps_seoul_timezone():
    # add_job 은 이미 tz 를 가진 트리거를 스케줄러 tz 로 덮어쓰지 않으므로,
    # 등록된 잡의 트리거 tz 가 서울인지 확인한다.
    job = scheduler.build_scheduler(_settings()).get_job("ingest_cycle")
    assert str(job.trigger.timezone) == "Asia/Seoul"


def test_run_ingest_cycle_calls_ingest_market_and_intraday(monkeypatch):
    calls = {}
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(
        scheduler.ingest, "ingest_reports", lambda db, s: calls.setdefault("reports", 3) or 3
    )
    monkeypatch.setattr(
        scheduler.ingest, "build_market_brief", lambda db, s: calls.setdefault("market", "brief")
    )
    monkeypatch.setattr(
        scheduler.intraday, "accumulate_intraday", lambda db: calls.setdefault("intraday", 2) or 2
    )

    result = scheduler.run_ingest_cycle(_settings())

    assert result == {"reports_ingested": 3, "market_brief": True, "intraday_codes": 2}
    assert calls == {"reports": 3, "market": "brief", "intraday": 2}


def test_run_ingest_cycle_closes_session(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: session)
    monkeypatch.setattr(scheduler.ingest, "ingest_reports", lambda db, s: 0)
    monkeypatch.setattr(scheduler.ingest, "build_market_brief", lambda db, s: None)
    monkeypatch.setattr(scheduler.intraday, "accumulate_intraday", lambda db: 0)

    scheduler.run_ingest_cycle(_settings())

    session.close.assert_called_once()
