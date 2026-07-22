"""재무 온톨로지 어댑터 — OntologyPort 구현(financial_ontology 패키지 래핑).

get_ontology_port() 로 싱글턴 어댑터를 얻는다. 온톨로지는 정적 데이터라 프로세스에 1회만 로드.
financial_ontology 패키지 직접 참조는 이 패키지 안에만 있다(import-linter ontology-behind-port).
"""

from __future__ import annotations

from app.adapters.financial_ontology.adapter import OntologyAdapter
from app.ports.financial_ontology import OntologyPort

_ADAPTER: OntologyPort | None = None


def get_ontology_port() -> OntologyPort:
    """OntologyPort 싱글턴(온톨로지 정적 데이터 → 재사용). 키·설정 불필요(LLM 과 달리 항상 활성)."""
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = OntologyAdapter()
    return _ADAPTER


__all__ = ["OntologyAdapter", "get_ontology_port"]
