"""financial-ontology — 재무 온톨로지 로더·정규화·비율엔진.

사용:
    from financial_ontology import get_ontology, Normalizer, RatioEngine, Graph

    ont = get_ontology()                          # 온톨로지 로드(스키마 검증+캐시)
    norm = Normalizer(ont)
    norm.resolve("매출채권")                       # -> "BS_CA_AR"
    norm.resolve("ifrs-full_CashAndCashEquivalents", standard="dart")  # -> "BS_CA_CASH"

    engine = RatioEngine(ont)
    engine.calculate("current_ratio", {"BS_CA_TOTAL": 100, "BS_CL_TOTAL": 60})  # -> 1.666...

    graph = Graph(ont)
    graph.ratio_inputs("roe")                     # -> ["IS_NI_PARENT", "BS_EQ_PARENT"]
"""

from __future__ import annotations

from .graph import Graph
from .loader import get_ontology, load_ontology
from .models import Account, Ontology, Ratio
from .normalizer import Normalizer, Resolution
from .ratios import RatioEngine, RatioResult

__all__ = [
    "Account",
    "Graph",
    "Normalizer",
    "Ontology",
    "Ratio",
    "RatioEngine",
    "RatioResult",
    "Resolution",
    "get_ontology",
    "load_ontology",
]

__version__ = "1.0.0"
