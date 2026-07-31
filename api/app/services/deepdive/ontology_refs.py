"""딥다이브 보고서의 단계별 JSON 키 → 온톨로지 정준 ID 연계(E2).

키가 Financial 컬럼명(revenue, per ...)이나 온톨로지 계정/비율 ID(IS_REV_TOTAL, roe ...)면
metric_info 를 통해 정준 라벨/설명을 찾아낸다. 본문(narrative_md) 키워드 매칭은 정확도/위험
대비가 크지 않아 단계 JSON 키 위주로 시작.

비즈니스 온톨로지(기업/산업/제품/원재료)는 사업보고서 ingest 단계에서 business_ontology_node/edge
테이블에 영속화된 정준 그래프를 읽어, 각 정준 노드 ID 를 OntologyRef 로 방출한다 — 기존 InfoDot
채널이 그대로 노출(신규 UI 불필요). 정준화되지 않은(pending_review) 노드는 정준 ID 가 없으므로 제외.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.services import business_ontology as bo_service
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


def extract_ontology_refs(
    report: dict,
    db: Session | None = None,
    stock_code: str | None = None,
    narrative_md: str | None = None,
) -> list[dict[str, str | None]]:
    """DeepDiveReport *_json dict 에서 온톨로지와 매핑되는 키를 추출.

    반환: [{stage, key, ontology_id, label, description}, ...]
    stage 는 현재 키 출처를 구분하기 위한 것 — 동일 키가 여러 stage 에 있을 수 있다.

    db·stock_code 가 주어지면 비즈니스 온톨로지 정준 노드(기업/산업/제품/원재료)도 함께 방출한다.
    narrative_md 가 주어지면 [[...]] 마커에서 온톨로지 ID 를 파싱해 stage="narrative" 로附加한다.
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
    refs.extend(_business_ontology_refs(db, stock_code))
    refs.extend(_narrative_ontology_refs(narrative_md))
    return refs


def _narrative_ontology_refs(narrative_md: str | None) -> list[dict[str, str | None]]:
    """narrative_md 의 [[...]] 마커 → OntologyRef(stage=narrative).

    LLM 이 프롬프트 지시로 [[ontology_id]] 형태의 마커를 본문에 표기하면,
    이곳에서 파싱해 metric_info 로 라벨/설명을 찾아 OntologyRef 로 방출한다.
    마커가 없으면 빈 리스트.
    """
    if not narrative_md:
        return []
    markers = {m.strip() for m in re.findall(r"\[\[(.*?)\]\]", narrative_md)}
    if not markers:
        return []
    infos, _ = ontology_service.metric_info(list(markers))
    refs: list[dict[str, str | None]] = []
    for info in infos:
        ont_id = info.get("ontology_id")
        if not ont_id:
            continue
        refs.append(
            {
                "stage": "narrative",
                "key": str(info["key"]),
                "ontology_id": str(ont_id),
                "label": str(info["term"] or info["key"]),
                "description": str(info["description"] or ""),
            }
        )
    return refs


def _business_ontology_refs(
    db: Session | None,
    stock_code: str | None,
) -> list[dict[str, str | None]]:
    """영속화된 비즈니스 온톨로지 그래프의 정준 노드 → OntologyRef.

    정준(canonical) 노드만 방출 — pending_review 노드는 정준 ID 가 없다. 노드 식별자(id)가
    곧 정준 ID(CMP_KRX_·IND_GICS_·PRD_·MAT_ 접두)이므로 key·ontology_id 에 동일값을 쓴다.
    """
    if db is None or not stock_code:
        return []
    graph = bo_service.company_graph(db, stock_code)
    out: list[dict[str, str | None]] = []
    for n in graph.get("nodes", []):
        if n.get("status") != "canonical":
            continue
        nid = n.get("id")
        if not nid:
            continue
        out.append(
            {
                "stage": "business",
                "key": str(nid),
                "ontology_id": str(nid),
                "label": str(n.get("korean_name") or nid),
                "description": str(n.get("node_type") or ""),
            }
        )
    return out
