"""pending_review 승격 워크플로 서비스 테스트 — HITL 검수.

정규화 실패 노드(canonical_id=NULL, status=pending_review)를 사람이 canonical 로 승격.
SQLite 인메모리 + create_all. pending 행은 직접 insert 해서 재현(정규화 무매치 상태).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    BusinessOntologyEdge,
    BusinessOntologyNode,
    BusinessOverviewCache,
    CorpCodeMap,
    SegmentSales,
)
from app.domain.business_research import OntologyMention
from app.services import business_ingest as bi
from app.services import business_ontology as bo_svc


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BusinessOntologyNode.__table__,
            BusinessOntologyEdge.__table__,
            BusinessOverviewCache.__table__,
            CorpCodeMap.__table__,
            SegmentSales.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add_pending(db, stock_code, node_type, name):
    n = BusinessOntologyNode(
        stock_code=stock_code,
        node_type=node_type,
        korean_name=name,
        status="pending_review",
        canonical_id=None,
        confidence=0.0,
    )
    db.add(n)
    db.commit()
    return n


# --- (a) 목록 + fuzzy 후보 ---
def test_list_pending_with_candidates(db):
    db.add(CorpCodeMap(stock_code="005930", corp_code="00126380", corp_name="삼성전자"))
    db.commit()
    bi.persist_ontology(db, "005930", [OntologyMention("product", "DRAM", "manufactures")], "R1", "삼성전자")
    pending = _add_pending(db, "005930", "company", "삼성전자주식회사")  # 회사 pending

    r = bo_svc.list_pending(db)
    assert r["total"] == 1
    item = r["pending"][0]
    assert item["id"] == pending.id
    # CorpCodeMap fuzzy 후보에 삼성전자(CMP_KRX_005930) 가 점수와 함께 나타남.
    cids = [c["canonical_id"] for c in item["candidates"]]
    assert "CMP_KRX_005930" in cids
    cand = next(c for c in item["candidates"] if c["canonical_id"] == "CMP_KRX_005930")
    assert cand["score"] > 0


# --- (b) merge 승격 → explore 가 이웃 노출(엣지는 node PK 기준) ---
def test_promote_merge_visible_in_explore(db):
    # 005930 이 DRAM 생산(canonical PRD_SEMI_DRAM + 회사 노드 + 엣지).
    bi.persist_ontology(
        db, "005930", [OntologyMention("product", "DRAM", "manufactures", 0.42, "2024.12", "q", 0.9)],
        "R1", "삼성전자",
    )
    # pending 제품 "DRAM모듈" + 회사→pending 엣지(정규화 실패해 그대로 보존된 관계).
    pprod = _add_pending(db, "005930", "product", "DRAM모듈")
    company = db.scalar(
        select(BusinessOntologyNode).where(
            BusinessOntologyNode.node_type == "company", BusinessOntologyNode.stock_code == "005930"
        )
    )
    db.add(
        BusinessOntologyEdge(
            stock_code="005930", src_node_id=company.id, dst_node_id=pprod.id,
            edge_type="manufactures", period="", confidence=0.9,
        )
    )
    db.commit()

    # 승격 전: PRD_SEMI_DRAM 탐색에 canonical DRAM 의 manufactures 이웃(회사) 1건만.
    # pending "DRAM모듈" 엣지는 pprod 가 정준 집합에 없어 보이지 않음.
    g0 = bo_svc.explore_node(db, "PRD_SEMI_DRAM")
    assert g0 is not None
    mfr0 = [n for n in g0["neighbors"] if n["edge_type"] == "manufactures"]
    assert len(mfr0) == 1

    # merge 승격 — DRAM모듈 행을 기존 PRD_SEMI_DRAM 정준에 합류.
    r = bo_svc.promote_pending(db, pprod.id, "PRD_SEMI_DRAM", "merge")
    assert r["status"] == "canonical"
    assert r["canonical_id"] == "PRD_SEMI_DRAM"

    # 승격 후: 같은 정준 PK 집합에 pprod.id 가 포함돼 회사→pprod 엣지가 이웃으로 추가 노출.
    g1 = bo_svc.explore_node(db, "PRD_SEMI_DRAM")
    assert g1 is not None
    mfr1 = [n for n in g1["neighbors"] if n["edge_type"] == "manufactures"]
    assert len(mfr1) == 2
    assert {c["id"] for c in mfr1} == {"CMP_KRX_005930"}


# --- (c) new 승격 — 신규 정준 발급 ---
def test_promote_new_canonical(db):
    p = _add_pending(db, "005930", "product", "HBM3E")
    r = bo_svc.promote_pending(db, p.id, "PRD_SEMI_HBM3E", "new")
    assert r["status"] == "canonical"
    assert r["canonical_id"] == "PRD_SEMI_HBM3E"
    # explore 가 새 정준을 focal 로 인식(인스턴스 행 존재).
    g = bo_svc.explore_node(db, "PRD_SEMI_HBM3E")
    assert g is not None
    assert g["focal"]["node_type"] == "product"


# --- (d) 잘못된 접두어 / new 중복 → ValueError ---
def test_promote_invalid_prefix(db):
    p = _add_pending(db, "005930", "product", "HBM")
    with pytest.raises(ValueError):
        bo_svc.promote_pending(db, p.id, "MAT_SEMI_HBM", "new")  # product 에 MAT_ 접두어


def test_promote_new_conflict_with_existing(db):
    bi.persist_ontology(db, "005930", [OntologyMention("product", "DRAM", "manufactures")], "R1", "삼성전자")
    p = _add_pending(db, "005930", "product", "DRAM호환")
    with pytest.raises(ValueError):
        bo_svc.promote_pending(db, p.id, "PRD_SEMI_DRAM", "new")  # 이미 존재 → merge 써야 함


# --- (e) reject → 숨김, 목록에서 제외 ---
def test_reject_excludes_from_list(db):
    p = _add_pending(db, "005930", "product", "폐기대상")
    r = bo_svc.reject_pending(db, p.id)
    assert r["status"] == "rejected"
    # pending 목록에서 사라짐.
    lst = bo_svc.list_pending(db)
    assert lst["total"] == 0
    assert all(item["id"] != p.id for item in lst["pending"])


# --- (f) reprocess — 개선된 normalizer로 pending 일괄 재해석 ---
def test_reprocess_promotes_auto_new_product(db):
    """정적 사전에 없는 제품명 → 자동 new 발급(PRD_AUTO_<slug>)으로 승격."""
    p = _add_pending(db, "005930", "product", "Galaxy S24")
    r = bo_svc.reprocess_pending(db)
    assert r["promoted"] == 1
    assert r["still_pending"] == 0
    assert r["total"] == 1
    db.refresh(p)
    assert p.status == "canonical"
    assert p.canonical_id.startswith("PRD_AUTO_")
    # pending 목록에서 사라짐.
    assert bo_svc.list_pending(db)["total"] == 0


def test_reprocess_rejects_company_blacklist(db):
    """회사 NER 오분류(병ㆍ의원) → rejected."""
    p = _add_pending(db, "005930", "company", "병ㆍ의원")
    r = bo_svc.reprocess_pending(db)
    assert r["rejected"] == 1
    db.refresh(p)
    assert p.status == "rejected"
    # reject 는 pending 목록에서 제외.
    assert bo_svc.list_pending(db)["total"] == 0


def test_reprocess_industry_keyword_mapping(db):
    """industry 자유표현(반도체 시장) → GICS 키워드 매핑으로 canonical."""
    p = _add_pending(db, "005930", "industry", "메모리 반도체 시장")
    r = bo_svc.reprocess_pending(db)
    assert r["promoted"] == 1
    db.refresh(p)
    assert p.status == "canonical"
    assert p.canonical_id.startswith("IND_GICS_")


def test_reprocess_industry_nonindustry_rejected(db):
    """비산업(공공/서비스/제조) industry 자유표현 → reject. LLM 폴백 없이 포트 reject 판정 유지."""
    p = _add_pending(db, "005930", "industry", "공공")
    r = bo_svc.reprocess_pending(db)
    assert r["rejected"] == 1
    assert r["promoted"] == 0
    db.refresh(p)
    assert p.status == "rejected"
    assert p.canonical_id is None


def test_reprocess_node_type_filter(db):
    """node_type 필터 — company만 재처리 시 product pending 유지."""
    _add_pending(db, "005930", "company", "병ㆍ의원")
    pp = _add_pending(db, "005930", "product", "Galaxy S24")
    r = bo_svc.reprocess_pending(db, node_type="company")
    assert r["total"] == 1
    assert r["rejected"] == 1
    # product pending 은 그대로.
    assert bo_svc.list_pending(db)["total"] == 1
    db.refresh(pp)
    assert pp.status == "pending_review"


def test_reprocess_idempotent(db):
    """이미 승격된 노드는 재처리 대상 아님 — 두 번째 실행 시 total=0."""
    _add_pending(db, "005930", "product", "Galaxy S24")
    r1 = bo_svc.reprocess_pending(db)
    assert r1["promoted"] == 1
    r2 = bo_svc.reprocess_pending(db)
    assert r2["total"] == 0
    assert r2["promoted"] == 0


# --- (g) resolve_industry 임베딩 top-k + LLM 판정 폴백 ---
class _FakeIndustryLLM:
    """임베딩+판정 목킹 — GICS 배치는 첫 후보만 [1.0], 쿼리는 [1.0] → top-1=첫 GICS.

    chat 은 배치 판정 프롬프트([표현 N] 마커 포함)면 각 표현에 '<N>: <pick>' 한 줄씩 반환하고,
    per-call(resolve_industry 직접) 프롬프트면 pick 문자열 그대로 반환. embed 실패/미설정 분기는 별도 테스트.
    """

    def __init__(self, pick: str = "1", *, embed_fail: bool = False) -> None:
        self._pick = pick
        self._embed_fail = embed_fail

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if self._embed_fail:
            from app.ports.llm import LLMError

            raise LLMError("embed 실패(목킹)")
        # 다수 텍스트(GICS 배치) → 첫 항목 [1.0], 나머지 [0.0]; 단일(쿼리) → [1.0].
        if len(texts) > 1:
            return [[1.0] if i == 0 else [0.0] for i in range(len(texts))]
        return [[1.0]]

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.3,
        *,
        timeout: int | None = None,
        max_attempts: int = 3,
    ) -> str:
        import re

        nums = re.findall(r"\[표현 (\d+)\]", user)
        if nums:  # 배치 판정 — 표현마다 한 줄.
            return "\n".join(f"{n}: {self._pick}" for n in nums)
        return self._pick  # per-call(resolve_industry 직접)


def _clear_gics_cache() -> None:
    bo_svc._GICS_EMBED_CACHE.clear()


def test_resolve_industry_llm_pick_promotes(db):
    """포트 무매치 industry 자유표현 → LLM 판정(후보 1 선택) → canonical 승격."""
    _clear_gics_cache()
    first_id = bo_svc.industries()[0].id
    llm = _FakeIndustryLLM(pick="1")
    r = bo_svc.resolve_industry(db, "완전없는산업표현XYZ", llm=llm, embed_model="m", judge_model="m")
    assert r.status == "canonical"
    assert r.canonical_id == first_id
    assert r.matched_via == "llm_classify"


def test_resolve_industry_llm_none_keeps_pending(db):
    """LLM 판정 NONE(산업 분류 아님) → pending 유지(HITL 대상)."""
    _clear_gics_cache()
    llm = _FakeIndustryLLM(pick="NONE")
    r = bo_svc.resolve_industry(db, "글로벌 전자 기업", llm=llm, embed_model="m", judge_model="m")
    assert r.status == "pending_review"
    assert r.canonical_id is None


def test_resolve_industry_no_llm_keeps_pending(db):
    """LLM/임베딩 미설정 → 포트 pending 결과 그대로(우아한 강등)."""
    r = bo_svc.resolve_industry(db, "글로벌 전자 기업")
    assert r.status == "pending_review"
    assert r.canonical_id is None


def test_resolve_industry_embed_fail_keeps_pending(db):
    """임베딩 실패 → 포트 pending 결과로 강등(예외 전파 X)."""
    _clear_gics_cache()
    llm = _FakeIndustryLLM(pick="1", embed_fail=True)
    r = bo_svc.resolve_industry(db, "완전없는산업표현XYZ", llm=llm, embed_model="m", judge_model="m")
    assert r.status == "pending_review"
    assert r.canonical_id is None


def test_resolve_industry_keyword_short_circuits_llm(db):
    """포트 키워드 매칭 hit → LLM 호출 없이 canonical(임베딩 캐시 미생성 확인)."""
    _clear_gics_cache()
    llm = _FakeIndustryLLM(pick="NONE")  # 키워드 hit 이면 chat 미호출이라 pick 무의미
    r = bo_svc.resolve_industry(db, "메모리 반도체 시장", llm=llm, embed_model="m", judge_model="m")
    assert r.status == "canonical"
    assert r.canonical_id.startswith("IND_GICS_")
    assert r.matched_via != "llm_classify"
    # 키워드 단락 시 임베딩 캐시 미생성.
    assert len(bo_svc._GICS_EMBED_CACHE) == 0


def test_reprocess_industry_batch_via_settings(db, monkeypatch):
    """settings 주입 시 industry resolve 를 1회 chat 배치 판정 — 다건을 한 호출로 일괄 승격.

    _get_llm 를 가짜 LLM 으로 monkeypatch 하여 reprocess_pending 의 _classify_industry_batch 경로 검증.
    캐시 키는 embed_model 이라 인스턴스 무관 — 동시 빌드는 _gics_embeddings lock 으로 직렬화.
    """
    _clear_gics_cache()
    llm = _FakeIndustryLLM(pick="1")
    monkeypatch.setattr(bo_svc, "_get_llm", lambda s: llm)

    class _SettingsStub:
        ollama_embedding_model = "m"
        insight_model = "m"

    _add_pending(db, "005930", "industry", "표현A")
    _add_pending(db, "005930", "industry", "표현B")
    r = bo_svc.reprocess_pending(db, node_type="industry", settings=_SettingsStub())
    assert r["promoted"] == 2
    assert r["still_pending"] == 0
    # 두 노드 모두 동일 top-1 GICS 로 승격(가짜 LLM pick=1).
    assert bo_svc.list_pending(db, node_type="industry")["total"] == 0
    # 임베딩 캐시는 embed_model 키로 1회 빌드(병렬 중복 빌드 없음).
    assert set(bo_svc._GICS_EMBED_CACHE.keys()) == {"m"}


def test_reprocess_industry_batch_chunks(db, monkeypatch):
    """배치 청크 경계 — 청크 크기보다 많은 표현이 여러 청크로 쪼개져 로컬 번호 1..K 로 판정된다.

    _INDUSTRY_BATCH_CHUNK=2 로 좁혀 5건 → 3청크(2+2+1). 각 청크의 [표현 N] 은 로컬 1..K 이고 파서도
    로컬 idx 를 chunk_items 에 매핑 — 청크 경계 너머 전체 5건이 누락/중복 없이 승격됨을 검증.
    """
    _clear_gics_cache()
    llm = _FakeIndustryLLM(pick="1")
    monkeypatch.setattr(bo_svc, "_get_llm", lambda s: llm)
    monkeypatch.setattr(bo_svc, "_INDUSTRY_BATCH_CHUNK", 2)

    class _SettingsStub:
        ollama_embedding_model = "m"
        insight_model = "m"

    for name in ("A", "B", "C", "D", "E"):
        _add_pending(db, "005930", "industry", f"표현{name}")
    r = bo_svc.reprocess_pending(db, node_type="industry", settings=_SettingsStub())
    assert r["promoted"] == 5
    assert r["still_pending"] == 0
    assert bo_svc.list_pending(db, node_type="industry")["total"] == 0


# --- (h) 비동기 reprocess 백그라운드 ---
def _reset_reprocess_state() -> None:
    bo_svc._REPROCESS_STATE["running"] = False
    bo_svc._REPROCESS_STATE["last"] = None
    bo_svc._REPROCESS_STATE["error"] = None
    if bo_svc._REPROCESS_LOCK.locked():
        bo_svc._REPROCESS_LOCK.release()


def test_start_reprocess_background_runs_and_records(monkeypatch):
    """백그라운드 스레드로 reprocess 실행 — 완료 시 last 에 결과 기록, running 해제.

    SessionLocal 을 가짜로 두어 실제 DB 연결 없이 스레드가 동작. reprocess_pending 자체는 이미
    다른 테스트에서 검증됐으므로 여기선 비동기 오케스트레이션(start/status/lock)만 검증.
    """
    import time
    from unittest.mock import MagicMock

    _reset_reprocess_state()
    captured: dict[str, object] = {}

    def _fake_reprocess(db_arg, *, node_type=None, settings=None):
        captured["node_type"] = node_type
        return {"promoted": 5, "rejected": 1, "still_pending": 2, "total": 8}

    monkeypatch.setattr(bo_svc, "reprocess_pending", _fake_reprocess)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: MagicMock())

    class _SettingsStub:
        pass

    r = bo_svc.start_reprocess_background(node_type="industry", settings=_SettingsStub())
    assert r["status"] == "started"
    for _ in range(200):
        if not bo_svc.reprocess_status()["running"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("백그라운드 reprocess 완료 대기 시간 초과")
    s = bo_svc.reprocess_status()
    assert s["running"] is False
    assert s["last"]["promoted"] == 5
    assert s["error"] is None
    assert captured["node_type"] == "industry"
    _reset_reprocess_state()


def test_start_reprocess_already_running_rejects_overlap():
    """락 보유 중 두 번째 start 는 already_running — 중복 실행 방지."""
    _reset_reprocess_state()
    bo_svc._REPROCESS_LOCK.acquire()
    try:
        r = bo_svc.start_reprocess_background(node_type="industry", settings=None)
        assert r["status"] == "already_running"
    finally:
        bo_svc._REPROCESS_LOCK.release()
    _reset_reprocess_state()
