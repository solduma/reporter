"""센티먼트 분류기 단위 테스트 — GLM 응답을 목킹해 JSON 파싱·폴백을 검증한다."""

from __future__ import annotations

from app.services import sentiment
from reporter.ollama_client import OllamaError


class _FakeClient:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, model, system, user, temperature=0.3):
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _classify(reply):
    return sentiment.classify(_FakeClient(reply), "m", "company", "삼성전자", "본문")


def test_parses_clean_json():
    r = _classify('{"sentiment": "BUY", "one_liner": "목표가 상향", "rationale": "2Q 호실적"}')
    assert r.sentiment == "BUY"
    assert r.one_liner == "목표가 상향"
    assert r.rationale == "2Q 호실적"


def test_extracts_json_from_codefence_and_noise():
    reply = "분석 결과입니다:\n```json\n{\"sentiment\":\"SELL\",\"one_liner\":\"밸류 부담\",\"rationale\":\"PER 과열\"}\n```"
    r = _classify(reply)
    assert r.sentiment == "SELL"
    assert r.rationale == "PER 과열"


def test_invalid_sentiment_value_falls_back_to_hold():
    r = _classify('{"sentiment": "매수", "one_liner": "x", "rationale": "y"}')
    assert r.sentiment == "HOLD"  # BUY/SELL/HOLD 이외 값은 HOLD


def test_non_json_response_falls_back_to_hold():
    r = _classify("그냥 자유 텍스트 응답 BUY 일지도")
    assert r.sentiment == "HOLD"
    assert r.one_liner  # 원문 앞부분을 one_liner 로 보존


def test_glm_error_falls_back_to_hold():
    r = _classify(OllamaError("timeout"))
    assert r.sentiment == "HOLD"
    assert r.one_liner == ""


def test_lowercase_sentiment_is_normalized():
    r = _classify('{"sentiment": "buy", "one_liner": "a", "rationale": "b"}')
    assert r.sentiment == "BUY"
