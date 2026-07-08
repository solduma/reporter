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


def test_run_ingest_cycle_calls_ingest_and_market(monkeypatch):
    calls = {}
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(
        scheduler.ingest, "ingest_reports", lambda db, s: calls.setdefault("reports", 3) or 3
    )
    monkeypatch.setattr(
        scheduler.ingest, "build_market_brief", lambda db, s: calls.setdefault("market", "brief")
    )

    result = scheduler.run_ingest_cycle(_settings())

    assert result == {"reports_ingested": 3, "market_brief": True}
    assert calls == {"reports": 3, "market": "brief"}


def test_run_ingest_cycle_closes_session(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: session)
    monkeypatch.setattr(scheduler.ingest, "ingest_reports", lambda db, s: 0)
    monkeypatch.setattr(scheduler.ingest, "build_market_brief", lambda db, s: None)

    scheduler.run_ingest_cycle(_settings())

    session.close.assert_called_once()
