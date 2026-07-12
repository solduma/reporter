"""종목 판단 요약 — 3축 점수에서 강점·약점·확인사항 + 신호를 규칙으로 도출(순수·결정적).

투자 자문이 아니라 '이미 계산된 점수를 사람이 읽기 쉽게 요약'하는 계층이다. 신호(fit/watch/
avoid)도 점수의 기계적 분류일 뿐 권유가 아니며, 표시측이 면책 문구를 함께 노출한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 축 점수 임계(analysis_scoring 과 동일 관례: 60↑ 양호, 40↓ 약함).
_STRONG = 60.0
_WEAK = 40.0

# 축 키 → 사람이 읽는 이름(강점·약점 문장 조립용).
_AXIS_NAME = {"growth": "성장", "technical": "기술적 추세", "topdown": "섹터 수급"}


@dataclass
class Judgment:
    signal: str  # "fit"(적합) | "watch"(관망) | "avoid"(회피) | "insufficient"(데이터 부족)
    signal_label: str  # 한글 라벨
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)  # 확인할 점


# 축별 '확인할 점'(약하거나 데이터 없을 때 안내). 초보자가 다음에 무엇을 볼지.
_CHECK = {
    "growth": "매출·이익 성장이 일회성인지 지속인지 리포트·공시로 확인",
    "technical": "추세가 살아있는지 차트에서 눌림·이탈 여부 확인",
    "topdown": "속한 섹터로 자금이 계속 도는지 산업 흐름에서 확인",
}


def summarize(
    overall: float | None, axis_scores: dict[str, float | None]
) -> Judgment:
    """종합·축 점수로 판단 요약을 만든다. 계산 가능한 축이 없으면 insufficient."""
    scored = {k: v for k, v in axis_scores.items() if v is not None}
    if overall is None or not scored:
        return Judgment(
            signal="insufficient",
            signal_label="데이터 부족",
            checks=["재무·시세 데이터가 쌓이면 분석이 채워집니다"],
        )

    strengths = [f"{_AXIS_NAME.get(k, k)} 강함({v:.0f}점)" for k, v in scored.items() if v >= _STRONG]
    weaknesses = [f"{_AXIS_NAME.get(k, k)} 약함({v:.0f}점)" for k, v in scored.items() if v < _WEAK]
    checks = [
        _CHECK[k]
        for k, v in scored.items()
        if k in _CHECK and _WEAK <= v < _STRONG  # 중립 구간은 '확인'으로 유도
    ]
    # 약한 축도 확인 대상에 포함(왜 약한지 점검).
    checks += [_CHECK[k] for k, v in scored.items() if k in _CHECK and v < _WEAK]

    signal, label = _signal(overall, strengths, weaknesses)
    return Judgment(
        signal=signal,
        signal_label=label,
        strengths=strengths,
        weaknesses=weaknesses,
        checks=checks,
    )


def _signal(overall: float, strengths: list[str], weaknesses: list[str]) -> tuple[str, str]:
    """종합 점수 + 강·약축 수로 신호 분류. 권유가 아니라 점수의 기계적 요약."""
    if overall >= _STRONG and not weaknesses:
        return "fit", "매수 적합"
    if overall < _WEAK or len(weaknesses) >= 2:
        return "avoid", "회피"
    return "watch", "관망"
