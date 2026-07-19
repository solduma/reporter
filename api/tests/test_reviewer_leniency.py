"""reviewer 심각도 게이트·IR 1차출처 인정 — 프롬프트 규칙이 유지되는지 회귀 방지.

프롬프트 기반 동작이라 LLM 판정 자체는 유닛으로 못 박지만, '중대 결함만 gap'·'접근 불가 데이터는
gap 아님'·'IR 출처 인정' 같은 핵심 지시가 프롬프트에서 사라지지 않도록 검증한다(과엄격 회귀 방지).
"""

from __future__ import annotations

from app.services.deepdive import hitl, review_loop


def test_review_rule_has_severity_gate():
    rule = review_loop._REVIEW_OUTPUT_RULE
    # 결론을 바꿀 중대 결함만 gap, 사소한 형식은 통과.
    assert "중대한 절차 결함만" in rule
    assert "애매하면 통과" in rule


def test_review_rule_excludes_inaccessible_data():
    rule = review_loop._REVIEW_OUTPUT_RULE
    # 파이프라인이 접근 못 하는 데이터(비공개 IR 등) 미확보는 gap 아님.
    assert "접근할 수 없는 데이터" in rule
    assert "IR" in rule
    assert "재확인 안 된다" in rule  # 검색 재확인 불가만으로 배제 금지


def test_hitl_research_goal_accepts_ir_source():
    goal = hitl._RESEARCH_GOAL
    # IR·회사 제공 1차 출처를 evidence 로 인정, 공개 재확인 불가만으로 배제 금지.
    assert "IR" in goal
    assert "1차 출처" in goal


def test_hitl_schema_is_deterministic_components():
    # 결정론 HITL: LLM 은 계산 결과가 아니라 구성요소(value·unit·target_metric·scope·refuted)만 준다.
    schema = hitl._RESEARCH_SCHEMA
    for field in ("refuted", "value", "unit", "target_metric", "scope"):
        assert field in schema
    # LLM 은 계산하지 않는다(플래너 역할)·이진 반박 판정.
    assert "플래너" in hitl._RESEARCH_GOAL
    assert "계산하지" in hitl._RESEARCH_GOAL


def test_hitl_reviewer_allows_estimated_baseline():
    review = hitl._REVIEW_SYSTEM
    # baseline 을 공개로 못 구해도 IR값·추정+근거면 충족, 아예 빈 방치만 gap.
    assert "추정" in review
    assert "IR" in review
