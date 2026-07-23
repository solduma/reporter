"""딥다이브 보고서의 단계별 JSON 키 → 온톨로지 정준 ID 연계(E2).

키가 Financial 컬럼명(revenue, per ...)이나 온톨로지 계정/비율 ID(IS_REV_TOTAL, roe ...)면
metric_info 를 통해 정준 라벨/설명을 찾아낸다. 본문(narrative_md) 키워드 매칭은 정확도/위험
대비가 크지 않아 단계 JSON 키 위주로 시작.
"""

from __future__ import annotations

from app.services import ontology as ontology_service

_STAGE_KEYS = ("overview", "redflags", "business", "thesis", "valuation")


def _collect_keys(obj: object) -> set[str]:
    """dict/list 중첩 구조에서 모든 문자열 키를 수집."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                keys.add(k)
            keys.update(_collect_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            keys.update(_collect_keys(item))
    return keys


def extract_ontology_refs(report: dict) -> list[dict[str, str | None]]:
    """DeepDiveReport *_json dict 에서 온톨로지와 매핑되는 키를 추출.

    반환: [{stage, key, ontology_id, label, description}, ...]
    stage 는 현재 키 출처를 구분하기 위한 것 — 동일 키가 여러 stage 에 있을 수 있다.
    """
    refs: list[dict[str, str | None]] = []
    for stage in _STAGE_KEYS:
        data = report.get(stage)
        if not isinstance(data, dict):
            continue
        keys = sorted(_collect_keys(data))
        if not keys:
            continue
        infos, _ = ontology_service.metric_info(keys)
        for info in infos:
            ont_id = info.get("ontology_id")
            if not ont_id:
                continue
            refs.append(
                {
                    "stage": stage,
                    "key": str(info["key"]),
                    "ontology_id": str(ont_id),
                    "label": str(info["term"] or info["key"]),
                    "description": str(info["description"] or ""),
                }
            )
    return refs
