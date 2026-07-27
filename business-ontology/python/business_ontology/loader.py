"""YAML 로더 — ontology/*.yaml + mappings/*.yaml 을 BusinessOntology 로 구성.

financial_ontology.loader 의 캐시 패턴(lru_cache + get_ontology)을 미러.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

from .models import (
    BusinessOntology,
    CompanyNode,
    EdgeTypeMeta,
    IndustryNode,
    ProductNode,
    RawMaterialNode,
    SegmentNode,
)

DATA_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_DIR = DATA_ROOT / "ontology"
MAPPINGS_DIR = DATA_ROOT / "mappings"
SCHEMA_DIR = DATA_ROOT / "schema"

_STANDARDS = ("GICS", "KRX", "KSIC")


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _schema_validator(name: str) -> Draft7Validator | None:
    path = SCHEMA_DIR / name
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return Draft7Validator(json.load(fh))


def _validate_doc(doc: dict, validator: Draft7Validator | None, source: str) -> None:
    if validator is None:
        return
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        msgs = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]
        raise ValueError(f"schema 검증 실패 {source}:\n" + "\n".join(msgs))


def _load_node_file(
    path: Path, key: str, cls, validator: Draft7Validator | None
) -> dict[str, object]:
    """ontology/*.yaml 의 {key: {id: {...}}} 블록을 노드 사전으로 로드."""
    if not path.exists():
        return {}
    doc = _load_yaml(path)
    _validate_doc(doc, validator, str(path))
    block = doc.get("ontology", {}).get(key, {}) or {}
    return {str(node_id): cls.from_dict(raw) for node_id, raw in block.items()}


def _load_edge_types(path: Path, validator: Draft7Validator | None) -> dict[str, EdgeTypeMeta]:
    if not path.exists():
        return {}
    doc = _load_yaml(path)
    _validate_doc(doc, validator, str(path))
    block = doc.get("ontology", {}).get("edge_types", {}) or {}
    return {str(eid): EdgeTypeMeta.from_dict(raw) for eid, raw in block.items()}


def _load_industry_mappings() -> dict[tuple[str, str], str]:
    """mappings/{dart_industry,krx_to_gics,ksic_to_gics}.yaml → (standard, code) → GICS sub-industry id.

    값이 GICS 코드(8자리)면 industries.yaml 의 by_gics_code 로 정준 id 를 다시 찾는다.
    """
    out: dict[tuple[str, str], str] = {}
    files = {
        "dart": MAPPINGS_DIR / "dart_industry.yaml",
        "krx": MAPPINGS_DIR / "krx_to_gics.yaml",
        "ksic": MAPPINGS_DIR / "ksic_to_gics.yaml",
    }
    for standard, path in files.items():
        if not path.exists():
            continue
        doc = _load_yaml(path)
        mappings = doc.get("mappings", {}) or {}
        for code, val in mappings.items():
            if isinstance(val, dict):
                gics = val.get("gics_sub_industry") or val.get("gics_code")
            else:
                gics = val
            if gics:
                out[(standard, str(code))] = str(gics)
    return out


def _build_indexes(
    industries: dict[str, IndustryNode],
    products: dict[str, ProductNode],
    materials: dict[str, RawMaterialNode],
    companies: dict[str, CompanyNode],
    segments: dict[str, SegmentNode],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    by_korean: dict[str, str] = {}
    by_english: dict[str, str] = {}
    by_alias: dict[str, str] = {}
    by_gics: dict[str, str] = {}

    def add(node_id: str, korean: str, english: str, aliases) -> None:
        if korean:
            by_korean.setdefault(korean, node_id)
        if english:
            by_english.setdefault(english, node_id)
        for a in aliases:
            if a:
                by_alias.setdefault(a, node_id)

    for nid, n in industries.items():
        add(nid, n.korean_name, n.english_name, n.aliases)
        if n.gics_sub_industry:
            by_gics.setdefault(n.gics_sub_industry, nid)
        if n.gics_code and n.gics_code != n.gics_sub_industry:
            by_gics.setdefault(n.gics_code, nid)
    for nid, n in products.items():
        add(nid, n.korean_name, n.english_name, n.aliases)
    for nid, n in materials.items():
        add(nid, n.korean_name, n.english_name, n.aliases)
    for nid, n in companies.items():
        add(nid, n.korean_name, n.english_name, n.aliases)
    for nid, n in segments.items():
        add(nid, n.korean_name, n.english_name, n.aliases)

    return by_korean, by_english, by_alias, by_gics


def load_ontology(*, validate: bool = True) -> BusinessOntology:
    validator = _schema_validator("business_ontology_schema.json") if validate else None

    industries = _load_node_file(
        ONTOLOGY_DIR / "industries.yaml", "industries", IndustryNode, validator
    )
    products = _load_node_file(ONTOLOGY_DIR / "products.yaml", "products", ProductNode, validator)
    materials = _load_node_file(
        ONTOLOGY_DIR / "materials.yaml", "materials", RawMaterialNode, validator
    )
    companies = _load_node_file(
        ONTOLOGY_DIR / "companies.yaml", "companies", CompanyNode, validator
    )
    segments = _load_node_file(ONTOLOGY_DIR / "segments.yaml", "segments", SegmentNode, validator)
    edge_types = _load_edge_types(ONTOLOGY_DIR / "edge_types.yaml", validator)

    by_korean, by_english, by_alias, by_gics = _build_indexes(
        industries, products, materials, companies, segments
    )
    by_industry_code = _load_industry_mappings()
    # mappings 의 값이 GICS 코드면 정준 id 로 치환.
    by_industry_code_resolved = {
        key: by_gics.get(val, val) for key, val in by_industry_code.items()
    }

    return BusinessOntology(
        industries=industries,
        products=products,
        materials=materials,
        companies=companies,
        segments=segments,
        edge_types=edge_types,
        metadata={"standards": list(_STANDARDS)},
        by_korean_name=by_korean,
        by_english_name=by_english,
        by_alias=by_alias,
        by_gics_code=by_gics,
        by_industry_code=by_industry_code_resolved,
    )


@lru_cache(maxsize=4)
def _cached_load(validate: bool) -> BusinessOntology:
    return load_ontology(validate=validate)


def get_ontology(*, validate: bool = True) -> BusinessOntology:
    return _cached_load(validate)
