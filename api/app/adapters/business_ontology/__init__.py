"""비즈니스 온톨로지 어댑터 — BusinessOntologyPort 구현(business_ontology 패키지 래핑).

get_business_ontology_port() 로 싱글턴 어댑터를 얻는다. 온톨로지는 정적 데이터라 프로세스에
1회만 로드. business_ontology 패키지 직접 참조는 이 패키지 안에만 있다
(import-linter business-ontology-behind-port).
"""

from __future__ import annotations

from app.adapters.business_ontology.adapter import BusinessOntologyAdapter
from app.ports.business_ontology import BusinessOntologyPort

_ADAPTER: BusinessOntologyPort | None = None


def get_business_ontology_port() -> BusinessOntologyPort:
    """BusinessOntologyPort 싱글턴(온톨로지 정적 데이터 → 재사용). 키·설정 불필요(항상 활성)."""
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = BusinessOntologyAdapter()
    return _ADAPTER


__all__ = ["BusinessOntologyAdapter", "get_business_ontology_port"]
