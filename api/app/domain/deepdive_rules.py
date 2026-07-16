"""딥다이브 재무 레드플래그 — 순수 판정 룰(IO·프레임워크 모름).

2단계 Red Flags 의 정량 필터. 수치를 받아 '회계적 착시' 신호를 플래그로 낸다. LLM 은 이 플래그를
근거로 서술·심화 조사하고, 판정 자체는 여기(재현 가능한 룰)가 소유한다. 임계값은 보수적으로.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedFlag:
    """레드플래그 하나. severity: high|medium|low. metric 은 판정 근거 수치."""

    code: str  # 기계 식별자(예: 'receivables_outpace_revenue')
    label: str  # 사람이 읽는 라벨
    severity: str
    detail: str  # 근거 설명(수치 포함)


def _yoy(curr: float | None, prior: float | None) -> float | None:
    """증가율. prior 가 0/음수/결측이면 None."""
    if curr is None or prior is None or prior <= 0:
        return None
    return (curr - prior) / prior


def check_red_flags(
    *,
    revenue: float | None,
    revenue_prior: float | None,
    receivables: float | None,
    receivables_prior: float | None,
    inventory: float | None,
    inventory_prior: float | None,
    ocf: float | None,  # 영업활동현금흐름
    net_income: float | None,
    intangibles: float | None,
    total_assets: float | None,
) -> list[RedFlag]:
    """재무 수치로 레드플래그 목록을 만든다. 데이터 결측 항목은 건너뛴다(오탐 방지).

    핵심 신호:
    - 매출채권/재고가 매출보다 빨리 증가(밀어내기·매출 착시)
    - 이익은 나는데 OCF 가 음수이거나 순이익 대비 크게 낮음(현금 미창출)
    - 무형자산 비중 과다(향후 상각 리스크)
    """
    flags: list[RedFlag] = []
    rev_g = _yoy(revenue, revenue_prior)

    # 1) 매출채권이 매출보다 빠르게 증가 — 15pp 이상 초과면 플래그.
    recv_g = _yoy(receivables, receivables_prior)
    if rev_g is not None and recv_g is not None and recv_g - rev_g >= 0.15:
        flags.append(RedFlag(
            "receivables_outpace_revenue", "매출채권이 매출보다 급증", "high",
            f"매출 {rev_g * 100:+.0f}% vs 매출채권 {recv_g * 100:+.0f}% — 매출 착시·회수 리스크 점검",
        ))

    # 2) 재고자산이 매출보다 빠르게 증가 — 15pp 이상.
    inv_g = _yoy(inventory, inventory_prior)
    if rev_g is not None and inv_g is not None and inv_g - rev_g >= 0.15:
        flags.append(RedFlag(
            "inventory_outpace_revenue", "재고자산이 매출보다 급증", "medium",
            f"매출 {rev_g * 100:+.0f}% vs 재고 {inv_g * 100:+.0f}% — 판매 부진·평가손 리스크",
        ))

    # 3) 이익 대비 현금흐름 괴리 — 순이익 흑자인데 OCF 음수, 또는 OCF 가 순이익의 50% 미만.
    if net_income is not None and net_income > 0 and ocf is not None:
        if ocf < 0:
            flags.append(RedFlag(
                "profit_no_cash", "흑자인데 영업현금흐름 적자", "high",
                f"순이익 {net_income:,.0f} vs OCF {ocf:,.0f} — 이익의 질 의심(현금 미창출)",
            ))
        elif ocf < net_income * 0.5:
            flags.append(RedFlag(
                "ocf_below_profit", "영업현금흐름이 순이익 대비 저조", "medium",
                f"OCF {ocf:,.0f} < 순이익의 50%({net_income * 0.5:,.0f}) — 현금 전환력 점검",
            ))

    # 4) 무형자산 비중 과다 — 총자산의 30% 이상이면 상각 리스크 플래그.
    if intangibles is not None and total_assets and total_assets > 0:
        ratio = intangibles / total_assets
        if ratio >= 0.30:
            flags.append(RedFlag(
                "high_intangibles", "무형자산 비중 과다", "medium",
                f"무형자산/총자산 {ratio * 100:.0f}% — 영업권·개발비 상각 리스크(주석 확인 필요)",
            ))
    return flags


def summarize_severity(flags: list[RedFlag]) -> str:
    """플래그 목록 → 종합 등급(high 있으면 위험, medium 만이면 주의, 없으면 양호)."""
    if any(f.severity == "high" for f in flags):
        return "위험"
    if any(f.severity == "medium" for f in flags):
        return "주의"
    return "양호"
