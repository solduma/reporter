"""비율 계산 엔진.

온톨로지 ratios/*.yaml 의 formula(사람 가독용 서술)를 정제해 안전 평가한다.
formula 에는 한국어 서술(`또는 …`, `(취득액 음수 가정)`)·기간 접미(`_평균`/`_기초`/`_기말`)·
외부 입력(`shares_outstanding`, `market_price`)·타 비율 참조(`배당성향`)가 섞일 수 있다.

값 namespace 규칙(caller):
  - 평균/단일 기간값: key = ontology ID (예: `BS_EQ_PARENT`). closing 으로 간주.
  - 명시 기간: `BS_EQ_PARENT:opening` / `:closing` / `:prior` / `:current`.
  - 외부 입력: `shares_outstanding`, `market_price`, `cost_of_equity` … (formula 그대로 참조).

평가 전략(솔직한 v1):
  1. 기간 접미 식별자(`BASE_평균`/`_기초`/`_기말`/`_당기`/`_전기` 및 영문 alias) →
     ASCII 키 `BASE__avg`/`__opening`/`__closing`/`__current`/`__prior` 로 치환.
  2. namespace 구축: caller 값(colon 키 → 이중밑줄 키 변환) + 평균 조립 + 본 ID 보강.
  3. 한국어 서술 괄호 블록(한글 포함 `( … )`, 예: `(또는 …)`) 제거.
  4. "=" 설명 분리("A = B" prose — 비율 formula 는 비교연산 없음).
  5. 잔여 한글 있으면 지원 불가(composite/manual) — 잘못된 값 반환 안 함, reason 명시.
  6. safe_eval 평가. 결측/0분할 결과는 reason·missing·warnings 로 보고.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from ._eval import SafeEvalError, safe_eval
from .models import Ontology

# formula 접미 → namespace 기간 키
_PERIOD_SUFFIX = {
    "평균": "avg",
    "avg": "avg",
    "기초": "opening",
    "opening": "opening",
    "기말": "closing",
    "closing": "closing",
    "당기": "current",
    "current": "current",
    "전기": "prior",
    "prior": "prior",
}
# 식별자(ASCII) 뒤 기간 접미 캡처. base 는 밑줄 포함 가능(온톨로지 ID 가 _ 로 계층 표현);
# non-greedy 로 '_(period)' 직전까지 잡는다. 접미는 명시 목록.
_PERIOD_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*?)_(평균|avg|기초|opening|기말|closing|당기|current|전기|prior)\b"
)
_HANGUL_PAREN_RE = re.compile(r"\([^()]*[가-힣][^()]*\)")
_HANGUL_RE = re.compile(r"[가-힣]")
_PROSE_EQ_RE = re.compile(r"\s+=\s+")


@dataclass
class RatioResult:
    """비율 평가 결과."""

    ratio_id: str
    value: Decimal | None
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reason: str = ""  # value None 사유(unsupported/missing/divzero/unresolved/eval_error/...)

    @property
    def ok(self) -> bool:
        return self.value is not None


class RatioEngine:
    """온톨로지 비율 정의에 기반한 계산 엔진."""

    def __init__(self, ontology: Ontology):
        self._ont = ontology

    def calculate(self, ratio_id: str, values: dict[str, object]) -> RatioResult:
        ratio = self._ont.ratio(ratio_id)
        if ratio is None:
            return RatioResult(ratio_id, None, reason=f"unknown ratio: {ratio_id}")

        expr, sanitize_warnings, period_keys = self._sanitize(ratio.formula)
        if _HANGUL_RE.search(expr):
            return RatioResult(
                ratio_id,
                None,
                warnings=sanitize_warnings,
                reason="composite_or_manual: formula references other ratios/inputs/prose",
            )
        if not expr.strip():
            return RatioResult(ratio_id, None, warnings=sanitize_warnings, reason="empty_formula")

        namespace = self._build_namespace(values, period_keys, sanitize_warnings)
        try:
            value, missing = safe_eval(expr, namespace)
        except SafeEvalError as exc:
            return RatioResult(
                ratio_id, None, warnings=sanitize_warnings, reason=f"eval_error: {exc}"
            )

        warnings = list(sanitize_warnings)
        if value is None:
            if missing:
                req_missing = [a for a in ratio.required_accounts if a not in namespace]
                reason = "missing_values" + (f" (required: {req_missing})" if req_missing else "")
                return RatioResult(
                    ratio_id, None, missing=missing, warnings=warnings, reason=reason
                )
            return RatioResult(ratio_id, None, warnings=warnings, reason="divzero_or_unresolved")
        return RatioResult(ratio_id, value, warnings=warnings)

    def calculate_many(
        self, ratio_ids: list[str], values: dict[str, object]
    ) -> dict[str, RatioResult]:
        return {rid: self.calculate(rid, values) for rid in ratio_ids}

    def required(self, ratio_id: str) -> list[str]:
        """해 비율의 필수 계정 ID. 공시 데이터 충족 여부 사전 점검용."""
        ratio = self._ont.ratio(ratio_id)
        return list(ratio.required_accounts) if ratio else []

    # --- 정제 ---
    def _sanitize(self, formula: str) -> tuple[str, list[str], set[tuple[str, str]]]:
        """formula 를 ASCII 산술식으로 정제. (정제식, 경고, {(base,period)})."""
        if not formula:
            return "", [], set()
        expr = formula
        warnings: list[str] = []
        period_keys: set[tuple[str, str]] = set()

        if _PROSE_EQ_RE.search(expr):
            expr = _PROSE_EQ_RE.split(expr, maxsplit=1)[0]
            warnings.append("formula의 '=' 설명 분리(좌변 채택)")

        def _replace(match: re.Match) -> str:
            base, suffix = match.group(1), _PERIOD_SUFFIX[match.group(2)]
            period_keys.add((base, suffix))
            return f"{base}__{suffix}"

        expr = _PERIOD_RE.sub(_replace, expr)

        prev = None
        while prev != expr:
            prev = expr
            expr = _HANGUL_PAREN_RE.sub("", expr)
        return expr.strip(), warnings, period_keys

    def _build_namespace(
        self, values: dict[str, object], period_keys: set[tuple[str, str]], warnings: list[str]
    ) -> dict[str, object]:
        ns: dict[str, object] = {}
        # caller 값 복사(외부 입력·합성 비율값 포함)
        for k, v in values.items():
            if v is None:
                continue
            ns[k] = v
            # colon 기간 키 → 이중밑줄 키
            if ":" in k:
                base, period = k.rsplit(":", 1)
                ns[f"{base}__{period}"] = v
        # 본 ID(평균/closing) → closing·current 보강
        for k, v in list(values.items()):
            if v is None or ":" in k:
                continue
            ns.setdefault(f"{k}__closing", v)
            ns.setdefault(f"{k}__current", v)
        # 기간 접미 요청 해석
        for base, period in period_keys:
            key = f"{base}__{period}"
            if key in ns:
                continue
            if period == "avg":
                opening = ns.get(f"{base}__opening")
                closing = ns.get(f"{base}__closing")
                if opening is not None and closing is not None:
                    ns[key] = (Decimal(str(opening)) + Decimal(str(closing))) / Decimal(2)
                elif closing is not None:
                    ns[key] = closing
                    warnings.append(f"{base}_평균: opening 미제공 — closing 사용(평균 미적용)")
                elif opening is not None:
                    ns[key] = opening
                    warnings.append(f"{base}_평균: closing 미제공 — opening 사용(평균 미적용)")
            elif period in ("opening", "closing", "current"):
                plain = values.get(base)
                if plain is not None:
                    ns[key] = plain
                    warnings.append(
                        f"{base}_{period}: 명시값 미제공 — 본 ID({base}) 사용(기간평균 미적용)"
                    )
            # prior: 본 ID 폴백 없음(전기는 별개 기간) — 미해결 시 safe_eval missing 처리
        return ns
