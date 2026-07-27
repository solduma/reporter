#!/usr/bin/env python3
"""비즈니스 온톨로지 무결성 검증.

1. ontology/*.yaml 을 schema/business_ontology_schema.json 로 검증.
2. ID 패턴 확인(IND_GICS_8자리 / PRD_ / MAT_ / CMP_ / SEG_).
3. 교차 참조: ProductNode.is_also_material_id 가 실제 material id 인지.
4. mappings/*.yaml 의 GICS id 가 industries.yaml 에 존재하는지.

실행: python3 business-ontology/scripts/validate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

DATA_ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_DIR = DATA_ROOT / "ontology"
MAPPINGS_DIR = DATA_ROOT / "mappings"
SCHEMA_DIR = DATA_ROOT / "schema"


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main() -> int:
    errors: list[str] = []
    schema_path = SCHEMA_DIR / "business_ontology_schema.json"
    with schema_path.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    validator = Draft7Validator(schema)

    node_files = {
        "industries": ONTOLOGY_DIR / "industries.yaml",
        "products": ONTOLOGY_DIR / "products.yaml",
        "materials": ONTOLOGY_DIR / "materials.yaml",
        "companies": ONTOLOGY_DIR / "companies.yaml",
        "segments": ONTOLOGY_DIR / "segments.yaml",
        "edge_types": ONTOLOGY_DIR / "edge_types.yaml",
    }

    docs: dict[str, dict] = {}
    for key, path in node_files.items():
        if not path.exists():
            continue
        doc = _load(path)
        docs[key] = doc
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
            errors.append(f"{path.name}: {'/'.join(map(str, err.path)) or '<root>'}: {err.message}")

    # 교차 참조: is_also_material_id
    material_ids = set((docs.get("materials", {}).get("ontology", {}).get("materials") or {}).keys())
    product_ids = set((docs.get("products", {}).get("ontology", {}).get("products") or {}).keys())
    for pid, p in (docs.get("products", {}).get("ontology", {}).get("products") or {}).items():
        ref = p.get("is_also_material_id")
        if ref and ref not in material_ids:
            errors.append(f"products.yaml: {pid}.is_also_material_id → {ref} 가 materials 에 없음")
        # PRD_STEEL_PRODUCT.is_also_material_id = MAT_METAL_STEEL 같은 교차링크 검증

    # 중복 ID 검사(노드 타입 간)
    all_ids: dict[str, str] = {}
    for key in ("industries", "products", "materials", "companies", "segments"):
        for nid in (docs.get(key, {}).get("ontology", {}).get(key) or {}):
            if nid in all_ids:
                errors.append(f"중복 ID {nid} ({all_ids[nid]} vs {key})")
            all_ids[nid] = key

    # 매핑 → GICS id 존재
    industry_ids = set((docs.get("industries", {}).get("ontology", {}).get("industries") or {}).keys())
    for mf in ("dart_industry.yaml", "krx_to_gics.yaml", "ksic_to_gics.yaml"):
        path = MAPPINGS_DIR / mf
        if not path.exists():
            continue
        doc = _load(path)
        for code, val in (doc.get("mappings") or {}).items():
            if isinstance(val, dict):
                gics = val.get("gics_sub_industry") or val.get("gics_code")
            else:
                gics = val
            if gics and gics not in industry_ids:
                errors.append(f"{mf}: {code} → {gics} 가 industries 에 없음")

    if errors:
        print("검증 실패:")
        for e in errors:
            print(f"  - {e}")
        return 1
    company_ids = set((docs.get("companies", {}).get("ontology", {}).get("companies") or {}).keys())
    segment_ids = set((docs.get("segments", {}).get("ontology", {}).get("segments") or {}).keys())
    print(f"검증 통과: industries={len(industry_ids)} products={len(product_ids)} "
          f"materials={len(material_ids)} companies={len(company_ids)} segments={len(segment_ids)}")
    print(f"  nodes total={len(all_ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())