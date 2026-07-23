"""DART 수집 account_id 집합 ↔ 재무 온톨로지 매핑 커버리지 회귀 가드(A2).

dart/client.py 의 _AID_* 집합(수집 추출용 DART taxonomy)이 온톨로지 dart_mapping.yaml
(SOT) 으로 정규화되는 비율을 측정한다. 커버리지는 단조 증가만 허용 — 드리프트로 인한
하락을 잡는다. EPS(주당지표, 온톨로지 계정 부재)·INTEREST(은행/비은행 산업 이중매핑)·
INTEREST_PAID_CF(CF 이자지급 상세)는 의도적 예외(별도 계정/산업 분기 필요).
"""

from __future__ import annotations

from app.adapters.dart import client as dart_client
from app.adapters.financial_ontology import get_ontology_port

# 현재 커버리지 기준선(해결된 taxonomy 수). 커버리지 확장 시 올리고, 하락하면 테스트 실패.
_BASELINE_RESOLVED = 22
# 의도적 미매핑 예외(온톨로지 계정 부재 또는 산업 이중매핑 — 별도 작업 필요).
_EXPECTED_UNMAPPED = {
    "ifrs-full_BasicEarningsLossPerShare",
    "ifrs_BasicEarningsLossPerShare",
    "dart_InterestExpenseFinanceExpense",
    "ifrs_InterestExpense",
    "ifrs-full_InterestPaidClassifiedAsOperatingActivities",
    "ifrs-full_InterestPaidClassifiedAsFinancingActivities",
}


def _aid_sets() -> dict[str, set[str]]:
    return {
        "REVENUE": dart_client._AID_REVENUE,
        "OP": dart_client._AID_OP,
        "NI_OWNERS": dart_client._AID_NI_OWNERS,
        "NI": dart_client._AID_NI,
        "EPS": dart_client._AID_EPS,
        "EQ_OWNERS": dart_client._AID_EQ_OWNERS,
        "EQ": dart_client._AID_EQ,
        "CAPEX": dart_client._AID_CAPEX,
        "TAX": dart_client._AID_TAX,
        "PRETAX": dart_client._AID_PRETAX,
        "INTEREST": dart_client._AID_INTEREST,
        "INTEREST_PAID_CF": dart_client._AID_INTEREST_PAID_CF,
    }


def test_dart_aid_coverage_does_not_regress():
    """_AID_* taxonomy 중 온톨로지 정규화 비율이 기준선 이상이어야 한다(드리프트 가드)."""
    port = get_ontology_port()
    all_tax: list[str] = []
    for taxs in _aid_sets().values():
        all_tax.extend(taxs)
    res = port.resolve_many(all_tax, standard="dart")
    resolved = sum(1 for r in res if r.id is not None)
    unresolved = {t for t, r in zip(all_tax, res, strict=True) if r.id is None}
    assert resolved >= _BASELINE_RESOLVED, (
        f"DART ontology coverage regressed: {resolved}/{len(all_tax)} "
        f"(baseline {_BASELINE_RESOLVED}). unresolved={sorted(unresolved)}"
    )


def test_unmapped_are_known_exceptions():
    """미매칭 taxonomy 는 의도적 예외 집합에 속해야 한다(예상치 못한 매핑 누락 감지)."""
    port = get_ontology_port()
    all_tax: list[str] = []
    for taxs in _aid_sets().values():
        all_tax.extend(taxs)
    res = port.resolve_many(all_tax, standard="dart")
    unresolved = {t for t, r in zip(all_tax, res, strict=True) if r.id is None}
    unexpected = unresolved - _EXPECTED_UNMAPPED
    assert not unexpected, (
        f"새로운 미매칭 taxonomy 발견(온톨로지 매핑 누락 또는 드리프트): {sorted(unexpected)}. "
        "매핑 추가하거나 _EXPECTED_UNMAPPED 에 예외로 등록할 것."
    )
