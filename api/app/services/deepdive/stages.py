"""딥다이브 5단계 정의 — 각 단계의 목표·결과 스키마·초기 컨텍스트 수집.

각 단계는 (1) 필요한 기본 데이터를 코드로 모아 context_data 로 만들고 (2) agent.run_stage 로 mini
tool-loop 를 돌려 구조화 결과를 얻는다. 단계는 이전 단계 결과(prior)를 컨텍스트로 누적 받는다.
레드플래그 정량 판정은 domain.deepdive_rules(순수 룰)가 소유하고, LLM 은 그 위에서 서술·심화한다.
"""

from __future__ import annotations

import logging

from app.domain import deepdive_rules
from app.ports.llm import LLMPort
from app.services.deepdive import agent, valuation_stage
from app.services.deepdive.tools import ToolContext, dispatch

logger = logging.getLogger(__name__)


def _fin_series(ctx: ToolContext) -> list[dict]:
    """실적(비추정) 재무 시계열 — 여러 단계가 공유하는 기본 컨텍스트."""
    fin = dispatch("financials", ctx, {})
    return [p for p in fin.get("periods", []) if not p.get("is_estimate")]


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
    return agent.run_stage(llm, model, ctx, stage_goal=goal, result_schema=schema,
                           context_data=context, max_tool_calls=3)


# ── 2단계 Red Flags ───────────────────────────────────────────────────
def stage_redflags(llm: LLMPort, model: str, ctx: ToolContext, prior: dict) -> dict:
    series = _fin_series(ctx)
    latest = series[-1] if series else {}
    prior_y = series[-5] if len(series) >= 5 else (series[0] if series else {})
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
    return agent.run_stage(llm, model, ctx, stage_goal=goal, result_schema=schema,
                           context_data=context, max_tool_calls=4)


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
    return agent.run_stage(llm, model, ctx, stage_goal=goal, result_schema=schema,
                           context_data=context, max_tool_calls=6)


# ── 4단계 Thesis & Risks ──────────────────────────────────────────────
def stage_thesis(llm: LLMPort, model: str, ctx: ToolContext, prior: dict) -> dict:
    context = {
        "overview": prior.get("overview", {}),
        "redflags": prior.get("redflags", {}),
        "business": prior.get("business", {}),
        "financials_series": _fin_series(ctx)[-12:],
    }
    goal = (
        "실적 기반의 명확한 인과관계로 투자 아이디어를 세운다. 막연한 기대가 아니라 '회사 실적이 실제로 "
        "좋아져서 주가가 오를 수밖에 없는' 근거를 세우고, 업종 특성(IT·소재/소재가공 스프레드·금융·소비재 "
        "등)에 맞게 차별화한다. 하방 리스크(대주주 도덕적 해이 이력, 부채 급증 등 아이디어가 틀어질 요인)를 "
        "선제적으로 점검한다. 필요하면 ownership·disclosures·web_search 로 보강."
    )
    schema = (
        '{"thesis": "실적 기반 투자 아이디어(인과관계 명확히)", "thesis_type": "성장주|자산주/역발상|기타", '
        '"drivers": ["실적 개선 동인"], "downside_risks": ["하방 리스크"], '
        '"industry_angle": "업종별 차별화 논리"}'
    )
    return agent.run_stage(llm, model, ctx, stage_goal=goal, result_schema=schema,
                           context_data=context, max_tool_calls=4)


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
