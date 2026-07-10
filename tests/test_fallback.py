"""폴백 기록 공용 모듈 단위 테스트 — sink 등록/전달/격리, 실패 흡수."""

from __future__ import annotations

import logging

import pytest

from reporter import fallback


@pytest.fixture(autouse=True)
def _isolate_sinks():
    """각 테스트가 전역 sink 레지스트리를 오염시키지 않도록 격리."""
    fallback.clear_sinks()
    yield
    fallback.clear_sinks()


def test_sink_receives_key_reason_detail_context():
    received = []
    fallback.register_sink(lambda k, r, d, c: received.append((k, r, d, c)))

    fallback.log_fallback("chart.naver_to_kis", reason="네이버 실패", detail="005930/day", bars=500)

    assert received == [("chart.naver_to_kis", "네이버 실패", "005930/day", {"bars": 500})]


def test_detail_and_context_default_when_omitted():
    received = []
    fallback.register_sink(lambda k, r, d, c: received.append((d, c)))

    fallback.log_fallback("market_brief.closing_to_all", reason="마감시황 미발행")

    assert received == [(None, {})]


def test_register_sink_is_idempotent():
    calls = []

    def sink(k, r, d, c):
        calls.append(k)

    fallback.register_sink(sink)
    fallback.register_sink(sink)  # 중복 등록 무시
    fallback.log_fallback("x.y", reason="z")

    assert calls == ["x.y"]


def test_sink_failure_does_not_break_fallback_path():
    """sink 가 던져도 폴백 경로(본 기능)는 계속돼야 한다 — 예외를 흡수한다."""
    good = []

    def bad_sink(k, r, d, c):
        raise RuntimeError("sink down")

    fallback.register_sink(bad_sink)
    fallback.register_sink(lambda k, r, d, c: good.append(k))

    fallback.log_fallback("a.b", reason="r")  # 예외 전파 안 됨

    assert good == ["a.b"]  # 뒤 sink 는 정상 호출됨


def test_logs_marker_prefix(caplog):
    with caplog.at_level(logging.WARNING, logger="reporter.fallback"):
        fallback.log_fallback("chart.naver_to_kis", reason="네이버 실패", detail="005930/day")

    assert "FALLBACK[chart.naver_to_kis]" in caplog.text
    assert "005930/day" in caplog.text
