"""적재 이력 기록 단위 테스트 — 잡별 결과 dict → (rows, detail) 요약 규칙."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, IngestLog
from app.services import ingest_log


def test_summarize_ingest_cycle():
    rows, detail = ingest_log._summarize(
        "ingest_cycle",
        {"reports_ingested": 5, "broadcasts_ingested": 2, "intraday_codes": 30, "market_brief": True},
    )
    assert rows == 5
    assert "리포트 5" in detail
    assert "브로드캐스트 2" in detail
    assert "시황갱신" in detail


def test_summarize_nightly_batch():
    # growth 는 실제로 dict({processed,total})라 대표 수치만 뽑아야 한다(원본 dict 노출 금지).
    rows, detail = ingest_log._summarize(
        "nightly_batch",
        {"universe_rows": 4295, "growth": {"processed": 100, "total": 200}, "sectors": 30},
    )
    assert rows == 4295
    assert "유니버스 4295" in detail
    assert "성장 100" in detail
    assert "{" not in detail  # dict 가 그대로 문자열화되지 않아야 한다


def test_summarize_nightly_batch_alt_growth_shape():
    # growth 가 {financials,momentum} 형태(초기값)여도 깨지지 않아야 한다.
    _, detail = ingest_log._summarize(
        "nightly_batch",
        {"universe_rows": 10, "growth": {"financials": 5, "momentum": 3}, "sectors": 0},
    )
    assert "성장 5" in detail
    assert "{" not in detail


def test_summarize_candle_batch():
    rows, detail = ingest_log._summarize(
        "candle_batch", {"stocks": 2653, "reloaded": 12, "failed": 3}
    )
    assert rows == 2653
    assert "재적재 12" in detail
    assert "실패 3" in detail


def test_summarize_backfill_uses_done():
    rows, detail = ingest_log._summarize(
        "financials_10y", {"done": 100, "failed": 2, "remaining": 500}
    )
    assert rows == 100
    assert "완료 100" in detail
    assert "남음 500" in detail


def test_summarize_unknown_job_stringifies():
    rows, detail = ingest_log._summarize("weird_job", {"a": 1})
    assert rows == 0
    assert "a" in detail


def test_record_explicit_rows_detail_override_result(monkeypatch):
    # rows·detail 을 직접 주면 result 요약보다 우선한다.
    captured = {}

    class _FakeSession:
        def add(self, obj):
            captured["obj"] = obj

        def commit(self):
            captured["committed"] = True

        def rollback(self):
            pass

    ingest_log.record(
        _FakeSession(), "manual_ingest", detail="신규 리포트 3건", rows=3, duration_ms=1200
    )
    obj = captured["obj"]
    assert obj.job == "manual_ingest"
    assert obj.rows == 3
    assert obj.detail == "신규 리포트 3건"
    assert obj.duration_ms == 1200
    assert captured["committed"]


def test_latest_for_jobs_returns_newest_per_job():
    # 잡당 최신 1행만 — DISTINCT ON(postgres 전용) 대신 이식성 있는 group-by 자기조인.
    # test_tui 에서는 latest_for_jobs 전체를 mock 하므로 여기서 실제 SQL 경로를 검증한다.
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[IngestLog.__table__])
    db = sessionmaker(bind=engine)()

    base = datetime(2026, 7, 1, 9, tzinfo=UTC)
    db.add_all([
        IngestLog(job="backfill_10y", ts=base, status="ok", rows=100, detail="old"),
        IngestLog(job="backfill_10y", ts=base.replace(hour=12), status="fail", rows=0, detail="boom"),
        IngestLog(job="financials_10y", ts=base.replace(hour=10), status="ok", rows=50, detail="ok"),
    ])
    db.commit()

    latest = ingest_log.latest_for_jobs(db, ["backfill_10y", "financials_10y", "absent"])
    # 이력 없는 잡(absent)은 제외되고, 잡당 가장 최신 행만.
    assert set(latest) == {"backfill_10y", "financials_10y"}
    assert latest["backfill_10y"].status == "fail"  # 12:00 행(더 최신)
    assert latest["backfill_10y"].detail == "boom"
    assert latest["financials_10y"].rows == 50
    db.close()


def test_latest_for_jobs_empty_jobs_returns_empty():
    assert ingest_log.latest_for_jobs(None, []) == {}
