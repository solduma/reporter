"""센티먼트 분류기 단위 테스트 — LLM 응답을 목킹해 JSON 파싱·폴백을 검증한다."""

from __future__ import annotations

from app.ports.llm import LLMError
from app.services import sentiment


class _FakeLLM:
    """LLMPort 를 만족하는 테스트 이중(포트 치환성) — 정해둔 응답/예외를 그대로 낸다."""

    def __init__(self, reply):
        self._reply = reply

    def chat(self, model, system, user, temperature=0.3):
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _classify(reply):
    return sentiment.classify(_FakeLLM(reply), "m", "company", "삼성전자", "본문")


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


def test_llm_error_falls_back_to_hold():
    r = _classify(LLMError("timeout"))
    assert r.sentiment == "HOLD"
    assert r.one_liner == ""


def test_lowercase_sentiment_is_normalized():
    r = _classify('{"sentiment": "buy", "one_liner": "a", "rationale": "b"}')
    assert r.sentiment == "BUY"


def test_recovers_first_object_with_trailing_text():
    # 후행 텍스트나 두 번째 객체가 붙어도 첫 유효 객체를 복구해야 한다 (greedy 정규식 회귀 방지)
    reply = '설명: {"sentiment": "SELL", "one_liner": "고평가", "rationale": "PER 과열"} 추가 잡담 {망가진'
    r = _classify(reply)
    assert r.sentiment == "SELL"
    assert r.one_liner == "고평가"


class _CaptureLLM:
    """chat 의 user 프롬프트를 캡처해 소유변동 요약 주입을 검증한다."""

    def __init__(self, reply='{"sentiment": "BUY", "rationale": "x"}'):
        self._reply = reply
        self.user = ""

    def chat(self, model, system, user, temperature=0.3):
        self.user = user
        return self._reply


def test_classify_disclosure_injects_ownership_summary():
    llm = _CaptureLLM()
    summary = "보고자: 윤원일 (사장)\n소유 증감: +3,000주 취득 (변동후 9,214주 보유)\n변동사유: 장내매수"
    sentiment.classify_disclosure(llm, "m", "임원ㆍ주요주주특정증권등소유상황보고서", "본문", summary)
    assert "[소유변동 요약]" in llm.user
    assert "+3,000주 취득" in llm.user
    assert "장내매수" in llm.user


def test_classify_disclosure_without_ownership_omits_section():
    llm = _CaptureLLM()
    sentiment.classify_disclosure(llm, "m", "주요사항보고서", "본문")
    assert "[소유변동 요약]" not in llm.user
