"""적재 이력 기록 단위 테스트 — 잡별 결과 dict → (rows, detail) 요약 규칙."""

from __future__ import annotations

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
    rows, detail = ingest_log._summarize(
        "nightly_batch", {"universe_rows": 4295, "growth": 100, "sectors": 30}
    )
    assert rows == 4295
    assert "유니버스 4295" in detail


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
