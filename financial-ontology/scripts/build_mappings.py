#!/usr/bin/env python3
"""온톨로지 YAML에서 매핑 테이블(dart/ifrs/usgaap)을 자동 생성.

각 온톨로지 파일의 accounts[].mappings.{dart,ifrs,usgaap} 와 korean_name/english_name/aliases 를
취합해 정규화용 매핑 테이블을 만든다. 온톨로지가 단일 진실원(SOT)이고 매핑은 파생물.

출력 구조(정방향 + 역방향):
  mappings:           # ontology_id 기준 정방향
    BS_CA_CASH:
      korean_name: ...
      english_name: ...
      statement: [...]
      dart: [...]        # 해당 표준 taxonomy 요소
      korean_aliases: [...]
  by_taxonomy:        # taxonomy 요소 → ontology_id (XBRL 정규화용 역방향)
    ifrs-full_CashAndCashEquivalents: BS_CA_CASH
  by_korean_name:     # 한국 계정명(정준+별칭) → ontology_id (텍스트 정규화용 역방향)
    현금및현금성자산: BS_CA_CASH
"""
from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict

import yaml

ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY = ROOT / "ontology"
MAPPINGS = ROOT / "mappings"
STANDARDS = ["dart", "ifrs", "usgaap"]
STANDARD_META = {
    "dart":  {"name": "DART XBRL 매핑 테이블", "standard": "DART_XBRL", "file": "dart_mapping.yaml"},
    "ifrs":  {"name": "IFRS 매핑 테이블",       "standard": "IFRS",       "file": "ifrs_mapping.yaml"},
    "usgaap":{"name": "US GAAP 매핑 테이블",     "standard": "US_GAAP",    "file": "usgaap_mapping.yaml"},
}
TODAY = "2026-07-22"


def load_accounts() -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for yml in sorted(ONTOLOGY.glob("*.yaml")):
        doc = yaml.safe_load(yml.read_text())
        for aid, acc in doc["ontology"]["accounts"].items():
            merged[aid] = acc
    return merged


def build(accounts: dict[str, dict], std: str) -> dict:
    forward: dict[str, dict] = {}
    by_taxonomy: dict[str, str] = {}
    by_korean: dict[str, str] = {}
    for aid, acc in sorted(accounts.items()):
        tax = (acc.get("mappings") or {}).get(std) or []
        entry = {
            "korean_name": acc.get("korean_name", acc.get("name", aid)),
            "english_name": acc.get("english_name", ""),
            "statement": acc.get("statement", []),
            "category": acc.get("category", []),
            std: tax,
            "korean_aliases": acc.get("aliases", []),
        }
        forward[aid] = entry
        for t in tax:
            # 첫 매핑이 정준. 충돌 시 가장 상위(정준) 계정 유지.
            by_taxonomy.setdefault(t, aid)
        names = [acc.get("korean_name", acc.get("name", aid))] + list(acc.get("aliases", []))
        for n in names:
            if n:
                by_korean.setdefault(str(n), aid)
    return forward, by_taxonomy, by_korean


def dump(std: str, forward, by_taxonomy, by_korean) -> str:
    meta = STANDARD_META[std]
    doc = {
        "version": "1.0.0",
        "metadata": {
            "name": meta["name"],
            "description": f"{meta['name']} — 온톨로지 계정 ID와 {meta['standard']} taxonomy/계정명 간 정규화 매핑. 온톨로지 YAML에서 자동 생성(SOT: ontology/*.yaml).",
            "standard": meta["standard"],
            "direction": "bidirectional (forward: ontology_id→taxonomy, reverse: taxonomy/korean_name→ontology_id)",
            "generated_from": "ontology/*.yaml",
            "generated": TODAY,
            "account_count": len(forward),
            "taxonomy_concept_count": len(by_taxonomy),
        },
        "mappings": forward,
        "by_taxonomy": dict(sorted(by_taxonomy.items())),
        "by_korean_name": dict(sorted(by_korean.items())),
    }
    return yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)


def main() -> None:
    accounts = load_accounts()
    MAPPINGS.mkdir(exist_ok=True)
    for std in STANDARDS:
        forward, by_taxonomy, by_korean = build(accounts, std)
        out = dump(std, forward, by_taxonomy, by_korean)
        path = MAPPINGS / STANDARD_META[std]["file"]
        path.write_text(out)
        print(f"{path.relative_to(ROOT)}: {len(forward)} accounts, {len(by_taxonomy)} taxonomy, {len(by_korean)} korean names")


if __name__ == "__main__":
    main()