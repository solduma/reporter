"""DART 수집 account_id 집합 ↔ 재무 온톨로지 매핑 커버리지 회귀 가드(A2).

dart/client.py 의 _AID_* 집합(수집 추출용 DART taxonomy)이 모두 온톨로지 dart_mapping.yaml
(SOT) 으로 정규화되어야 한다. 커버리지는 단조 증가만 허용 — 드리프트로 인한 하락을 잡는다.
"""

from __future__ import annotations

from app.adapters.dart import client as dart_client
from app.adapters.financial_ontology import get_ontology_port


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


def test_dart_aid_coverage_is_complete():
    """수집에 사용하는 모든 DART taxonomy account_id 는 ontology 로 정규화되어야 한다."""
    port = get_ontology_port()
    all_tax: list[str] = []
    for taxs in _aid_sets().values():
        all_tax.extend(taxs)
    res = port.resolve_many(all_tax, standard="dart")
    unresolved = {t for t, r in zip(all_tax, res, strict=True) if r.id is None}
    assert not unresolved, (
        f"DART 수집 taxonomy 중 ontology 미매핑 발견: {sorted(unresolved)}. "
        "ontology/common.yaml dart 매핑을 추가하거나 _AID_* 가 참조하는 ontology ID 를 확인할 것."
    )
