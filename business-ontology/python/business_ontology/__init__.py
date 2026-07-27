"""비즈니스 온톨로지 — 기업/산업/제품/원재료 + 가치사슬.

financial_ontology 패키지의 공개 API(get_ontology/Normalizer/Graph)를 미러.
"""

from .graph import Graph
from .loader import get_ontology, load_ontology
from .models import (
    BusinessOntology,
    ChainStage,
    CompanyNode,
    Edge,
    EdgeType,
    EdgeTypeMeta,
    IndustryNode,
    NodeType,
    ProductNode,
    RawMaterialNode,
    SegmentNode,
    SegmentType,
)
from .normalizer import Normalizer, Resolution

__all__ = [
    "BusinessOntology",
    "ChainStage",
    "CompanyNode",
    "Edge",
    "EdgeType",
    "EdgeTypeMeta",
    "Graph",
    "IndustryNode",
    "NodeType",
    "Normalizer",
    "ProductNode",
    "RawMaterialNode",
    "Resolution",
    "SegmentNode",
    "SegmentType",
    "get_ontology",
    "load_ontology",
]
__version__ = "0.1.0"
