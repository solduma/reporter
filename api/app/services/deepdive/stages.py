"""딥다이브 5단계 정의 — 각 단계의 목표·결과 스키마·초기 컨텍스트 수집.

각 단계는 (1) 필요한 기본 데이터를 코드로 모아 context_data 로 만들고 (2) agent.run_stage 로 mini
tool-loop 를 돌려 구조화 결과를 얻는다. 단계는 이전 단계 결과(prior)를 컨텍스트로 누적 받는다.
레드플래그 정량 판정은 domain.deepdive_rules(순수 룰)가 소유하고, LLM 은 그 위에서 서술·심화한다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.domain import deepdive_rules
from app.ports.llm import LLMPort
from app.services.deepdive import agent, review_loop, valuation_stage
from app.services.deepdive.tools import ToolContext, dispatch

logger = logging.getLogger(__name__)

# feedback(이전 라운드 절차 지적)을 단계 goal 뒤에 주입하는 공통 접두. run_stage 는 goal 텍스트만
# 받으므로(HITL 과 동일 메커니즘) reviewer 지적을 goal 에 이어붙여 재작업시킨다.
_FEEDBACK_HEADER = "\n\n**[이전 검토에서 지적된 절차 미비 — 이번엔 반드시 보완하라]**\n"


def _with_feedback(goal: str, feedback: str | None) -> str:
    return goal + _FEEDBACK_HEADER + feedback if feedback else goal


def _fin_series(ctx: ToolContext) -> list[dict]:
    """실적(비추정) 재무 시계열 — 여러 단계가 공유하는 기본 컨텍스트."""
    fin = dispatch("financials", ctx, {})
    return [p for p in fin.get("periods", []) if not p.get("is_estimate")]


# ── 단계별 Process-Reviewer 프롬프트(절차 감사 체크리스트) ─────────────────
# 공통 철학·출력 스키마는 review_loop 가 소유하고, 여기서는 각 단계가 '무슨 절차'를 지켰는지만 정의한다.
_REVIEW_SYSTEM = {
    "overview": (
        "너는 딥다이브 개요 단계의 절차 감사자다. 다음 절차를 점검한다:\n"
        "1) per/pbr/market_cap 이 실제 데이터(financials·price)에서 왔나 — 추정·임의값 방치 아님.\n"
        "2) 사업 개요·주주구성이 최신 정기보고서(report_kind 명시)에 근거하나.\n"
        "3) major_shareholders 에 대주주·지분이 구체적으로 담겼나(막연한 서술 아님)."
    ),
    "redflags": (
        "너는 딥다이브 재무 특이점 단계의 절차 감사자다. 다음 절차를 점검한다:\n"
        "1) 자동 탐지 레드플래그(auto_flags)를 누락·왜곡 없이 반영했나.\n"
        "2) 각 flag 의 detail 에 근거 수치(매출채권/재고/OCF/무형자산 등)가 있나 — 근거 없는 단정 아님.\n"
        "3) severity 종합 판정이 auto_severity 와 정합적인가.\n"
        "4) cash_trend·notes 가 시계열·주석 조회에 근거하나(추측 아님)."
    ),
    "business": (
        "너는 딥다이브 사업모델 단계의 절차 감사자다. 다음 절차를 점검한다:\n"
        "1) 밸류체인/벤더/고객/경쟁사를 추측이 아니라 사업보고서·리포트·웹 조회로 확인했나(출처 흔적).\n"
        "2) 탐색 순서(사업보고서→리포트→웹)를 밟았나 — 근거 없이 일반론으로 채우지 않았나.\n"
        "3) item_mix_change 에 과거 대비 비중 변화의 시계열·정량 근거가 있나.\n"
        "4) vendors/customers/competitors 가 실제 기업명 등 구체 항목인가(빈 껍데기 아님)."
    ),
    "thesis": (
        "너는 딥다이브 투자 아이디어 단계의 절차 감사자다. 다음 절차를 점검한다:\n"
        "1) **시점 유효성**: catalysts 가 미래·진행중 이벤트만 담았나(과거 종료·이미 반영 이벤트 제외). "
        "event_risks 가 현재 유효한 리스크만 담았나. 각 항목 timing 이 과거면 제외됐나.\n"
        "2) **주체 검증**: 이 종목이 주체인 이벤트만 넣었나(타사 언급·비교 기사 제외, 모/자회사는 관계 명시).\n"
        "3) catalysts/event_risks 에 출처(source)가 있나 — events 컨텍스트에 근거하나.\n"
        "4) thesis 가 drivers 와 인과적으로 연결되나 — 막연한 기대가 아니라 실적 기반인가."
    ),
}


# ── 1단계 Overview ────────────────────────────────────────────────────
def stage_overview(llm: LLMPort, model: str, ctx: ToolContext, prior: dict) -> dict:
    context = {
        "price": dispatch("price_context", ctx, {}),
        "financials_recent": _fin_series(ctx)[-8:],
        "periodic_report": dispatch("recent_periodic_report", ctx, {}),
        "ownership": dispatch("ownership", ctx, {}),
    }
    goal = (
        "이 종목의 기본 개요를 정리한다. 밸류에이션(PER·PBR·시총), 주주구성(대주주·지분), "
        "사업 개요(무엇을 파는 회사인지 한두 문단). 최신 정기보고서 본문을 근거로 한다."
    )
    schema = (
        '{"per": 숫자|null, "pbr": 숫자|null, "market_cap": 숫자|null, '
        '"major_shareholders": "대주주·지분 요약", "business_summary": "사업 개요 2~3문장", '
        '"report_kind": "참조한 정기보고서 종류"}'
    )
    return review_loop.run_with_review(
        llm, model,
        lambda fb: agent.run_stage(llm, model, ctx, stage_goal=_with_feedback(goal, fb),
                                   result_schema=schema, context_data=context, max_tool_calls=3),
        _REVIEW_SYSTEM["overview"], label=f"overview:{ctx.code}",
    )


def _period_ym(period: str) -> tuple[int, int] | None:
    """'YYYY.MM' → (year, month). 파싱 실패 시 None(추정치 '(E)' 포함해도 앞 6자만)."""
    if not period or len(period) < 7 or period[4] != ".":
        return None
    try:
        return int(period[:4]), int(period[5:7])
    except ValueError:
        return None


# ── 2단계 Red Flags ───────────────────────────────────────────────────
def stage_redflags(llm: LLMPort, model: str, ctx: ToolContext, prior: dict) -> dict:
    series = _fin_series(ctx)
    latest = series[-1] if series else {}
    # 전년 동기 = 같은 '월'(분기), 연도 −1. 행 개수 오프셋(series[-5])은 분기·연간 혼재 시 계절성
    # 어긋난 잘못된 기준을 잡으므로 기간(period)으로 매칭한다. 없으면 결측(비교 생략).
    lk = _period_ym(latest.get("period", "")) if latest else None
    prior_y = {}
    if lk:
        prior_y = next(
            (r for r in series if _period_ym(r.get("period", "")) == (lk[0] - 1, lk[1])), {}
        )
    # 정량 레드플래그(순수 룰). 재무제표에 매출채권·재고·OCF·무형자산이 없으면 해당 항목은 결측.
    flags = deepdive_rules.check_red_flags(
        revenue=latest.get("revenue"), revenue_prior=prior_y.get("revenue"),
        receivables=latest.get("receivables"), receivables_prior=prior_y.get("receivables"),
        inventory=latest.get("inventory"), inventory_prior=prior_y.get("inventory"),
        ocf=latest.get("ocf"), net_income=latest.get("net_income"),
        intangibles=latest.get("intangibles"), total_assets=latest.get("total_assets"),
    )
    context = {
        "financials_series": series[-12:],
        "auto_flags": [{"code": f.code, "label": f.label, "severity": f.severity, "detail": f.detail} for f in flags],
        "auto_severity": deepdive_rules.summarize_severity(flags),
    }
    goal = (
        "재무제표 특이점(이익의 질·잠재 리스크)을 검증한다. 자동 탐지된 레드플래그(auto_flags)를 "
        "출발점으로, 필요하면 재무제표 주석(disclosure_text)·시계열을 조회해 매출채권/재고/OCF 괴리, "
        "무형자산 상각 리스크, 현금성 자산 변동의 찜찜한 특이사항을 심화 검증한다."
    )
    schema = (
        '{"severity": "위험|주의|양호", "flags": [{"label":"", "severity":"", "detail":"근거 수치 포함"}], '
        '"cash_trend": "현금성 자산 흐름 코멘트", "notes": "주석에서 발견한 특이사항"}'
    )
    return review_loop.run_with_review(
        llm, model,
        lambda fb: agent.run_stage(llm, model, ctx, stage_goal=_with_feedback(goal, fb),
                                   result_schema=schema, context_data=context, max_tool_calls=4),
        _REVIEW_SYSTEM["redflags"], label=f"redflags:{ctx.code}",
    )


# ── 3단계 Business Deep Dive ──────────────────────────────────────────
def stage_business(llm: LLMPort, model: str, ctx: ToolContext, prior: dict) -> dict:
    # 탐색 순서: 사업보고서 → 리포트(개별+산업+언급) → 웹(네이버 블로그). LLM 이 부족분만 웹으로 보강.
    context = {
        "periodic_report": dispatch("recent_periodic_report", ctx, {}),
        "reports": dispatch("reports", ctx, {}),
        "overview": prior.get("overview", {}),
    }
    goal = (
        "사업모델을 '남에게 완벽히 설명할 수 있을 때까지' 파고든다. 탐색 순서는 사업보고서 → 리포트"
        "(개별 커버가 없으면 산업 리포트·타종목 리포트의 언급 활용) → 부족하면 web_search 로 네이버 "
        "블로그 기업 리서치 글 등 웹 보강. 밸류체인(전방/후방·핵심 공정·소재/제품 용도), 벤더(공급사)와 "
        "납품처(고객사), 핵심 경쟁사, 과거 대비 사업·아이템 비중 변화를 파악한다."
    )
    schema = (
        '{"value_chain": "전방/후방·공정·소재/제품 용도", "vendors": ["주요 공급사"], '
        '"customers": ["주요 납품처"], "competitors": ["핵심 경쟁사"], '
        '"item_mix_change": "아이템 비중 변화 추이", "moat": "경쟁우위·해자"}'
    )
    return review_loop.run_with_review(
        llm, model,
        lambda fb: agent.run_stage(llm, model, ctx, stage_goal=_with_feedback(goal, fb),
                                   result_schema=schema, context_data=context, max_tool_calls=6),
        _REVIEW_SYSTEM["business"], label=f"business:{ctx.code}",
    )


# ── 4단계 Thesis & Risks (미래 촉매·이벤트 리스크 포함) ─────────────────
def stage_thesis(llm: LLMPort, model: str, ctx: ToolContext, prior: dict) -> dict:
    # 이벤트 탐색을 컨텍스트로 선주입 — 섹터별 촉매(수주·계약·증설)·리스크(소송·유증·우발부채)
    # 뉴스 본문 + DART 공시. LLM 이 놓치지 않게 코드가 먼저 수집해 넣는다.
    today = datetime.now(UTC).date().isoformat()
    context = {
        "as_of_date": today,  # 분석 기준일 — 미래 유효 이벤트만 판정하도록.
        "overview": prior.get("overview", {}),
        "redflags": prior.get("redflags", {}),
        "business": prior.get("business", {}),
        "events": dispatch("event_search", ctx, {"side": "both"}),
        "financials_series": _fin_series(ctx)[-12:],
    }
    goal = (
        f"오늘({today}) 시점 기준으로 실적 기반의 명확한 인과관계로 투자 아이디어를 세운다. 막연한 기대가 "
        "아니라 '회사 실적이 실제로 좋아져서 주가가 오를 수밖에 없는' 근거를 세운다. **미래 촉매**(신규 수주·"
        "대형 계약·증설·인수·파트너십 등 실적을 끌어올릴 예정 이벤트)와 **이벤트 리스크**(소송·유상증자·"
        "우발부채·리콜·규제 등)를 events 컨텍스트(뉴스 본문·DART 공시)에서 구체적으로 뽑아 근거·출처·예상 "
        "영향과 함께 정리한다. "
        f"**시점 유효성(중요): 오늘은 {today} 다. catalysts 에는 아직 실현되지 않았거나 진행 중인(=미래에 "
        "실적·주가에 영향 줄) 이벤트만 넣는다. 이미 종료·소멸했거나 실적에 이미 반영된 과거 이벤트, 지난 목표주가·"
        "지난 실적 전망은 제외한다(과거 실적 추세는 drivers 로만 반영). event_risks 도 현재 유효한(아직 해소되지 "
        "않은) 리스크만 넣는다. 각 항목의 timing 이 과거면 제외하거나 '이미 반영'으로 명시.** "
        "**events 뉴스 중에는 이 종목이 주체가 아니라 단순 언급·비교된 기사(다른 회사의 수주·인수 등)가 섞여 "
        "있을 수 있다. 반드시 이 종목(분석 대상)이 주체인 이벤트만 넣고 타사 이벤트는 제외한다. 모회사·자회사 "
        "이벤트는 지분·지배구조 영향이 분명할 때만 그 관계를 명시해 포함한다.** "
        "events 가 부족하면 event_search·disclosures·web_search 로 보강한다. 업종 특성에 맞게 차별화."
    )
    schema = (
        '{"thesis": "실적 기반 투자 아이디어(인과관계 명확히)", "thesis_type": "성장주|자산주/역발상|기타", '
        '"drivers": ["실적 개선 동인"], "downside_risks": ["하방 리스크"], '
        '"catalysts": [{"event": "미래 촉매 이벤트", "impact": "예상 실적·주가 영향", "source": "뉴스/공시 출처", "timing": "예상 시점"}], '
        '"event_risks": [{"event": "이벤트 리스크", "impact": "예상 악영향", "source": "출처"}], '
        '"industry_angle": "업종별 차별화 논리"}'
    )
    return review_loop.run_with_review(
        llm, model,
        lambda fb: agent.run_stage(llm, model, ctx, stage_goal=_with_feedback(goal, fb),
                                   result_schema=schema, context_data=context, max_tool_calls=5),
        _REVIEW_SYSTEM["thesis"], label=f"thesis:{ctx.code}",
    )


# ── 5단계 Valuation & Target ──────────────────────────────────────────
def stage_valuation(llm: LLMPort, model: str, ctx: ToolContext, prior: dict) -> dict:
    """8개 밸류에이션 방식(PER·PBR·EV/EBITDA·DCF·DDM·자산가치·Fama-French·APT) 종합 → 최종 목표가.

    LLM 은 방식별 *가정*만 근거와 함께 내고, 산식·목표가·과정 서술·blend 는 domain.valuation 이
    결정론적으로 계산한다(valuation_stage 모듈). 환각 없는 재현 가능한 목표가."""
    return valuation_stage.run_valuation(llm, model, ctx, prior, _fin_series(ctx))


# 단계 순서(orchestrator 가 순회). (key, 함수) — key 는 DeepDiveReport 의 *_json 및 prior 누적 키.
STAGES: list[tuple[str, object]] = [
    ("overview", stage_overview),
    ("redflags", stage_redflags),
    ("business", stage_business),
    ("thesis", stage_thesis),
    ("valuation", stage_valuation),
]
