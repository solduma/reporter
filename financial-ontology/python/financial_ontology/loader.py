"""온톨로지 YAML 로드·병합·JSON 스키마 검증·정규화 인덱스 구축.

온톨로지 파일(ontology/*.yaml)이 단일 진실원(SOT)이다. 매핑 파일(mappings/*.yaml)은 파생물이므로
로더는 온톨로지 계정의 mappings/aliases/korean_name/english_name 으부터 정·역방향 인덱스를 직접 구축한다.
이렇게 하면 매핑 재생성(build_mappings.py) 없이도 항상 온톨로지와 인덱스가 일치한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

from .models import Account, Ontology, Ratio

# 패키지: financial-ontology/python/financial_ontology/loader.py
# 데이터 루트: financial-ontology/  (ontology/ mappings/ ratios/ schema/)
DATA_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_DIR = DATA_ROOT / "ontology"
RATIOS_DIR = DATA_ROOT / "ratios"
SCHEMA_DIR = DATA_ROOT / "schema"

_STANDARDS = ("dart", "ifrs", "usgaap")


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _validate_ontology_doc(doc: dict, source: str) -> None:
    schema_path = SCHEMA_DIR / "ontology_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        msg = first.message[:160]
        raise ValueError(f"온톨로지 스키마 검증 실패({source} @ {list(first.path)}): {msg}")


def _load_ontology_files(validate: bool) -> tuple[dict[str, Account], dict[str, dict], list[dict]]:
    accounts: dict[str, Account] = {}
    statements: dict[str, dict] = {}
    docs: list[dict] = []
    for yml in sorted(ONTOLOGY_DIR.glob("*.yaml")):
        doc = _load_yaml(yml)
        docs.append(doc)
        if validate:
            _validate_ontology_doc(doc, yml.name)
        statements.update(doc["ontology"].get("statements", {}))
        for aid, raw in doc["ontology"]["accounts"].items():
            if aid in accounts:
                raise ValueError(f"중복 계정 ID: {aid} (파일: {yml.name})")
            accounts[aid] = Account.from_dict(raw)
    return accounts, statements, docs


def _load_ratio_files() -> dict[str, Ratio]:
    ratios: dict[str, Ratio] = {}
    for yml in sorted(RATIOS_DIR.glob("*.yaml")):
        doc = _load_yaml(yml)
        for rid, raw in doc["ratios"].items():
            if rid in ratios:
                raise ValueError(f"중복 비율 ID: {rid} (파일: {yml.name})")
            raw["id"] = rid
            raw["category"] = raw.get("category") or doc.get("metadata", {}).get("category", "")
            ratios[rid] = Ratio.from_dict(raw)
    return ratios


def _build_indexes(accounts: dict[str, Account]) -> tuple[dict, dict, dict, dict]:
    """온톨로지 계정으로부터 정·역방향 정규화 인덱스 구축.

    - by_taxonomy: standard -> {taxonomy_element -> account_id}
    - by_korean_name: korean_name -> account_id (정준명; 충돌 시 첫 계정 유지)
    - by_english_name: english_name -> account_id
    - by_alias: alias -> account_id (한국·영문 별칭 모두; 충돌 시 첫 계정)
    """
    by_taxonomy: dict[str, dict[str, str]] = {s: {} for s in _STANDARDS}
    by_korean: dict[str, str] = {}
    by_english: dict[str, str] = {}
    by_alias: dict[str, str] = {}
    for acc in accounts.values():
        for std, elems in acc.mappings.items():
            if std not in by_taxonomy:
                by_taxonomy[std] = {}
            for elem in elems:
                by_taxonomy[std].setdefault(elem, acc.id)
        if acc.korean_name:
            by_korean.setdefault(acc.korean_name, acc.id)
        if acc.english_name:
            by_english.setdefault(acc.english_name, acc.id)
        for alias in acc.aliases:
            by_alias.setdefault(alias, acc.id)
    return by_taxonomy, by_korean, by_english, by_alias


def load_ontology(*, validate: bool = True) -> Ontology:
    """온톨로지 전체를 로드한다.

    validate=True(기본)면 JSON 스키마로 각 온톨로지 파일을 검증한다. 계정·비율·인덱스를
    구축한 Ontology 를 반환한다. 온톨로지 파일은 수정되지 않으므로 결과를 캐시한다.
    """
    accounts, statements, _docs = _load_ontology_files(validate)
    ratios = _load_ratio_files()
    by_taxonomy, by_korean, by_english, by_alias = _build_indexes(accounts)
    return Ontology(
        accounts=accounts,
        ratios=ratios,
        statements=statements,
        metadata={"standards": list(_STANDARDS)},
        by_taxonomy=by_taxonomy,
        by_korean_name=by_korean,
        by_english_name=by_english,
        by_alias=by_alias,
    )


@lru_cache(maxsize=4)
def _cached_load(validate: bool) -> Ontology:
    return load_ontology(validate=validate)


def get_ontology(*, validate: bool = True) -> Ontology:
    """캐시된 온톨로지 인스턴스 반환(프로세스 내 재사용)."""
    return _cached_load(validate)
