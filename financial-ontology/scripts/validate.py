#!/usr/bin/env python3
"""financial-ontology 전체 무결성 검증.

검사 항목:
  1. 모든 ontology/*.yaml 이 ontology_schema.json / account_schema.json 에 적합
  2. 계정 ID 패턴 ^[A-Z]+_[A-Z]+_[A-Z0-9_]+$ 준수
  3. parent/children/depends_on 참조가 병합된 계정 집합에 존재 (cross-file 포함)
  4. ratios/*.yaml 의 required_accounts 가 실제 계정 ID 참조
  5. examples/*.yaml 의 mapping/child_mapping/key_metrics 가 실제 계정 ID 참조
  6. mappings/*.yaml 의 역방향 인덱스가 실제 계정 ID 참조 (build_mappings.py 산출물)

사용: python3 financial-ontology/scripts/validate.py
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
IDPAT = re.compile(r"^[A-Z]+_[A-Z]+_[A-Z0-9_]+$")


def load_accounts() -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for yml in sorted((ROOT / "ontology").glob("*.yaml")):
        for aid, acc in yaml.safe_load(yml.read_text())["ontology"]["accounts"].items():
            merged[aid] = acc
    return merged


def main() -> int:
    errors: list[str] = []
    accounts = load_accounts()
    ids = set(accounts)

    # 1. JSON Schema
    oschema = json.loads((ROOT / "schema" / "ontology_schema.json").read_text())
    aschema = json.loads((ROOT / "schema" / "account_schema.json").read_text())
    for yml in sorted((ROOT / "ontology").glob("*.yaml")):
        doc = yaml.safe_load(yml.read_text())
        for e in Draft7Validator(oschema).iter_errors(doc):
            errors.append(f"{yml.name}: ontology_schema @ {list(e.path)} — {e.message[:120]}")
        for aid, acc in doc["ontology"]["accounts"].items():
            for e in Draft7Validator(aschema).iter_errors(acc):
                errors.append(f"{yml.name}:{aid}: account_schema — {e.message[:120]}")

    # 2. ID pattern
    errors += [f"bad id pattern: {k}" for k in accounts if not IDPAT.match(k)]

    # 3. references
    for aid, acc in accounts.items():
        p = acc.get("parent")
        if p and p not in ids:
            errors.append(f"{aid}: parent {p} not found")
        for c in acc.get("children", []):
            if c not in ids:
                errors.append(f"{aid}: child {c} not found")
        for d in acc.get("depends_on", []):
            if d not in ids:
                errors.append(f"{aid}: depends_on {d} not found")

    # 4. ratios
    for rf in sorted((ROOT / "ratios").glob("*.yaml")):
        for rid, r in yaml.safe_load(rf.read_text())["ratios"].items():
            for a in r.get("required_accounts", []):
                if a not in ids:
                    errors.append(f"{rf.name}:{rid}: required_accounts {a} not found")

    # 5. examples
    def collect(mapping):
        out = []
        for row in mapping:
            if "ontology_id" in row:
                out.append(row["ontology_id"])
            for c in row.get("child_mapping", []) or []:
                if "ontology_id" in c:
                    out.append(c["ontology_id"])
        return out

    for ef in sorted((ROOT / "examples").glob("*.yaml")):
        doc = yaml.safe_load(ef.read_text())
        for oid in collect(doc["mapping"]):
            if oid not in ids:
                errors.append(f"{ef.name}: mapping {oid} not found")
        for m in doc.get("key_metrics", []):
            for x in m.get("inputs", []):
                if x not in ids:
                    errors.append(f"{ef.name}: key_metrics {m['ratio']} input {x} not found")

    # 6. mappings reverse index
    for mf in sorted((ROOT / "mappings").glob("*.yaml")):
        doc = yaml.safe_load(mf.read_text())
        for t, oid in doc.get("by_taxonomy", {}).items():
            if oid not in ids:
                errors.append(f"{mf.name}: by_taxonomy {t} -> {oid} not found")
        for n, oid in doc.get("by_korean_name", {}).items():
            if oid not in ids:
                errors.append(f"{mf.name}: by_korean_name {n} -> {oid} not found")

    print(f"accounts={len(ids)} ratios={sum(len(yaml.safe_load(open(f))['ratios']) for f in glob.glob(str(ROOT/'ratios'/'*.yaml')))}")
    if errors:
        print(f"FAIL ({len(errors)} errors):")
        for e in errors[:50]:
            print("  -", e)
        return 1
    print("OK — 모든 검증 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())