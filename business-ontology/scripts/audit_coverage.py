#!/usr/bin/env python3
"""DART 추출 → 정준 resolution 커버리지 측정.

LLM 추출 mention 목록(raw name + node type)을 받아 normalizer 가 얼마나 정준 id 로
해석하는지 측정한다. pending_review 비율이 높으면 사전(products/materials) 보강이 필요함.

실행 예: python3 business-ontology/scripts/audit_coverage.py mentions.yaml
mentions.yaml 형식:
  mentions:
    - {node_type: product, name: "DRAM"}
    - {node_type: company, name: "삼성전자(주)"}
    - {node_type: raw_material, name: "구리"}
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from business_ontology import Normalizer, NodeType, get_ontology  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: audit_coverage.py <mentions.yaml>")
        return 2
    path = Path(sys.argv[1])
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    mentions = doc.get("mentions") or []
    norm = Normalizer(get_ontology())
    by_status: dict[str, int] = {}
    pending: list[str] = []
    for m in mentions:
        nt = m.get("node_type")
        name = m.get("name", "")
        res = norm.resolve(name, nt)  # type: ignore[arg-type]
        by_status[res.status] = by_status.get(res.status, 0) + 1
        if res.status != "canonical":
            pending.append(f"{nt}:{name} → {res.status} (matched_via={res.matched_via})")
    total = len(mentions)
    canon = by_status.get("canonical", 0)
    print(f"total={total} canonical={canon} pending_review={by_status.get('pending_review', 0)} "
          f"unknown={by_status.get('unknown', 0)} coverage={canon / total:.2%}" if total else "no mentions")
    if pending:
        print("해석 실패:")
        for p in pending:
            print(f"  - {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())