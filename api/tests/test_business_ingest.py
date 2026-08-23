"""사업 개요 파이프라인 단위 테스트 — 원문 추출·섹션 슬라이스·조립 매핑·백필·캐시.

DART 호출(fetch_report_zip·find_periodic_report)과 LLM 은 모킹. JSONB 캐시 테이블은
SQLite 방언에서 JSON 으로 렌더해 create_all 통과(test_trend_service 패턴).
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    BusinessOverviewCache,
    BusinessReportRaw,
    CorpCodeMap,
    SyncState,
    UniverseSnapshot,
)
from app.domain import business_overview as bo
from app.services import business_ingest as bi


# SQLite 는 JSONB 를 모른다 — 테스트 방언에서만 JSON 으로 렌더.
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            BusinessReportRaw.__table__,
            BusinessOverviewCache.__table__,
            CorpCodeMap.__table__,
            SyncState.__table__,
            UniverseSnapshot.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _settings():
    s = MagicMock()
    s.dart_api_key = "KEY"
    s.insight_model = "m"
    return s


# ── 원문 추출 ─────────────────────────────────────────────────────────────
def _zip_bytes(body: str) -> bytes:
    """document.xml zip(단일 XML 파일)을 메모리에서 생성 — _full_text 가 풀 수 있게."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("D001.xml", f"<DOC><BODY>{body}</BODY></DOC>")
    return buf.getvalue()


def test_extract_sections_annual_finds_business_content():
    """annual: '사업의 내용' 앵커 이후 ~종료 앵커까지 슬라이스. 목차 항목은 건너뛴다."""
    full = (
        "목차 .... 사업의 내용 10\n"  # TOC — 점줄이 뒤에 오므로 스킵
        "I. 회사의 개황 ...\n"
        "II. 사업의 내용\n"
        "1. 사업의 개요: 회사는 반도체를 제조한다.\n"
        "2. 주요 제품: 메모리·파운드리·시스템LSI\n"
        "III. 임원 등에 관한 사항\n"
        "이사 구성..."
    )
    with patch.object(bi.dart_report_parser, "fetch_report_zip", return_value=_zip_bytes(full)):
        out = bi.extract_sections(_settings(), "corp", "R1", bo.ANNUAL, MagicMock())
    assert bo.SECTION_BUSINESS_CONTENT in out
    text = out[bo.SECTION_BUSINESS_CONTENT]
    assert "반도체를 제조" in text
    assert "임원 등에 관한 사항" not in text  # 종료 앵커에서 잘림


def test_extract_sections_half_finds_company_overview():
    """half/quarter: '회사의 개황' 앵커에서 슬라이스."""
    full = (
        "목차 .... 회사의 개황 3\n"
        "I. 회사의 개황\n"
        "당기 매출 증가, 신규 수주 확보.\n"
        "II. 주식의 총수 등\n"
        "발행주식수 ..."
    )
    with patch.object(bi.dart_report_parser, "fetch_report_zip", return_value=_zip_bytes(full)):
        out = bi.extract_sections(_settings(), "corp", "R2", bo.QUARTER, MagicMock())
    assert bo.SECTION_COMPANY_OVERVIEW in out
    assert "신규 수주" in out[bo.SECTION_COMPANY_OVERVIEW]
    assert "주식의 총수" not in out[bo.SECTION_COMPANY_OVERVIEW]


def test_extract_sections_anchor_missing_falls_back_to_head():
    """앵커를 못 찾으면 전문 앞부분 폴백(LLM 이 판단)."""
    full = "이 보고서에는 해당 앵커가 없다. 그냥 본문 내용."
    with patch.object(bi.dart_report_parser, "fetch_report_zip", return_value=_zip_bytes(full)):
        out = bi.extract_sections(_settings(), "corp", "R3", bo.ANNUAL, MagicMock())
    assert out and out[bo.SECTION_BUSINESS_CONTENT]


def test_extract_sections_empty_zip_returns_empty():
    with patch.object(bi.dart_report_parser, "fetch_report_zip", return_value=None):
        assert bi.extract_sections(_settings(), "corp", "R", bo.ANNUAL, MagicMock()) == {}


# ── 조립 매핑 ─────────────────────────────────────────────────────────────
def test_map_sections_fills_missing_investor_sections():
    """LLM 결과에 일부 섹션만 있어도 모든 INVESTOR_SECTIONS 가 빈 값으로 채워진다."""
    result = {
        "sections": [
            {
                "id": "company_profile",
                "title": "회사 개요",
                "narrative": "반도체 제조",
                "tables": [],
                "updated_by_kind": "annual",
            },
        ]
    }
    reports = [("R1", bo.ANNUAL, 2024)]
    sections = bi._map_sections(result, reports)
    ids = [s["id"] for s in sections]
    assert ids == list(bo.INVESTOR_SECTIONS)
    profile = next(s for s in sections if s["id"] == "company_profile")
    assert profile["narrative"] == "반도체 제조"
    assert profile["updated_by_rcept"] == "R1"  # kind→rcept 매핑
    # 누락 섹션은 빈 값
    empty = next(s for s in sections if s["id"] == "market_position")
    assert empty["narrative"] == "" and empty["tables"] == []


def test_inputs_hash_stable_and_distinct():
    h1 = bi._inputs_hash([("R1", bo.ANNUAL, 2024), ("R2", bo.QUARTER, 2025)])
    assert h1 == bi._inputs_hash([("R1", bo.ANNUAL, 2024), ("R2", bo.QUARTER, 2025)])
    assert h1 != bi._inputs_hash([("R1", bo.ANNUAL, 2024)])


# ── 정기보고서 발견(베이스 + 이후 오버레이) ───────────────────────────────
def test_gather_for_assembly_base_annual_plus_later_half_quarter():
    """베이스 = 최신 annual. 그 이후(동일 rcept 이후) half/quarter 만 오버레이."""

    # annual R2025(2024연도분), half R2026, quarter R2027, 과거 quarter R2024는 제외.
    def fake_find(api_key, corp_code, year, kind, session):
        return {
            (2024, "annual"): "R2025",
            (2025, "half"): "R2026",
            (2025, "quarter"): "R2027",
            (2024, "quarter"): "R2024",  # 베이스 이전 — 제외
        }.get((year, kind))

    with patch.object(bi.dart, "find_periodic_report", side_effect=fake_find):
        reports = bi._gather_for_assembly(_settings(), "corp", MagicMock())
    assert reports[0] == ("R2025", bo.ANNUAL, 2024)  # 베이스
    rcepts = [r for r, _k, _y in reports]
    assert "R2024" not in rcepts  # 베이스 이전 제외
    assert "R2026" in rcepts and "R2027" in rcepts


def test_gather_for_assembly_no_annual_returns_empty():
    with patch.object(bi.dart, "find_periodic_report", return_value=None):
        assert bi._gather_for_assembly(_settings(), "corp", MagicMock()) == []


# ── 캐시 왕복 ─────────────────────────────────────────────────────────────
def test_cache_store_and_get_roundtrip(db):
    payload = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "sections": [{"id": "business_summary"}],
    }
    bi._store_cache(
        db,
        "005930",
        "삼성전자",
        "R1",
        [{"rcept_no": "R1", "kind": "annual", "period": "2024.12", "is_base": True}],
        "hash1",
        payload,
    )
    got = bi.get_cached_overview(db, "005930")
    assert got is not None
    assert got["stock_name"] == "삼성전자"
    assert got["sections"][0]["id"] == "business_summary"


def test_cache_get_miss_returns_none(db):
    assert bi.get_cached_overview(db, "000000") is None


def test_cache_invalidate_removes_row(db):
    bi._store_cache(db, "005930", "삼", "R1", [], "h", {"stock_code": "005930"})
    bi.invalidate_cache(db, "005930")
    assert bi.get_cached_overview(db, "005930") is None


def test_cache_ttl_expired_returns_none(db):
    bi._store_cache(db, "005930", "삼", "R1", [], "h", {"stock_code": "005930"})
    # cached_at 를 13시간 전으로 돌려 TTL(12h) 만료 시뮬레이션.
    row = db.query(BusinessOverviewCache).filter_by(stock_code="005930").one()
    row.cached_at = datetime.now(UTC) - timedelta(hours=13)
    db.commit()
    assert bi.get_cached_overview(db, "005930") is None


# ── assemble_overview 엔드투엔드(모킹) ─────────────────────────────────────
def _seed_corp(db, code="005930"):
    db.add(CorpCodeMap(stock_code=code, corp_code="CORP", corp_name="X"))
    db.commit()


def _fake_llm_chat(model, system, user, temperature=0.3, **kw):
    """map-reduce 파이프라인의 프롬프트 종류별로 응답을 dispatch 하는 fake LLM.chat."""
    import re as _re

    if "발췌문에서 투자자 관점의" in system:  # map — 청크 사실 추출
        return '{"facts": ["회사는 반도체 제조", "주력 제품 메모리"]}'
    if "주제별 카탈로그로 통합" in system:  # reduce — 카탈로그
        import json as _json

        return _json.dumps({t: [f"{t} 사실"] for t in bi._REDUCE_TOPICS}, ensure_ascii=False)
    if "단 하나의 섹션만" in system:  # 섹션별 생성
        sid = _re.search(r"- id: (\w+)", system).group(1)
        import json as _json

        return _json.dumps(
            {
                "id": sid,
                "title": sid,
                "narrative": f"{sid} 서술",
                "tables": [],
                "updated_by_kind": "annual",
            },
            ensure_ascii=False,
        )
    if "절차 감사자다" in system:  # review — 통과 판정
        return '{"procedure_sound": true, "gaps": []}'
    if "온톨로지 엔티티를 추출" in system:  # ontology NER
        return '{"mentions": []}'
    raise AssertionError(f"unexpected prompt: {system[:60]}")


def test_assemble_overview_persists_cache(db):
    """원문 추출·적재 → map/reduce → 섹션별 생성 → 리뷰 → 캐시 저장 흐름(모킹)."""
    _seed_corp(db)
    reports_map = {
        (2024, "annual"): "R2025",
        (2025, "quarter"): "R2027",
    }

    def fake_find(api_key, corp_code, year, kind, session):
        return reports_map.get((year, kind))

    fake_xml = _zip_bytes("II. 사업의 내용\n회사는 반도체 제조. III. 임원 등에 관한 사항\n")

    def fake_fetch_zip(api_key, rcept, session):
        return fake_xml

    llm = MagicMock()
    llm.chat.side_effect = _fake_llm_chat
    progresses: list[int] = []

    with (
        patch.object(bi.dart, "find_periodic_report", side_effect=fake_find),
        patch.object(bi.dart_report_parser, "fetch_report_zip", side_effect=fake_fetch_zip),
        patch.object(bi, "get_llm", return_value=llm),
        patch.object(bi.company_service, "report_stock_name", return_value="삼성전자"),
        patch.object(bi.company_service, "resolve_stock_name", return_value="삼성전자"),
    ):
        payload = bi.assemble_overview(
            db, _settings(), "005930", progress=lambda p: progresses.append(p)
        )

    assert payload is not None
    assert payload["as_of_annual_rcept"] == "R2025"
    ids = [s["id"] for s in payload["sections"]]
    assert ids == list(bo.INVESTOR_SECTIONS)  # 매핑이 빈 섹션까지 채움
    by_id = {s["id"]: s for s in payload["sections"]}
    assert by_id["company_profile"]["narrative"] == "company_profile 서술"
    assert progresses[0] >= 5 and progresses[-1] == 90  # 진행률 콜백 동작
    # 원문 적재 확인
    raw = db.query(BusinessReportRaw).filter_by(stock_code="005930").all()
    assert any(r.rcept_no == "R2025" and r.section_id == bo.SECTION_BUSINESS_CONTENT for r in raw)
    # 캐시 적재 확인
    assert bi.get_cached_overview(db, "005930") is not None


def test_assemble_overview_majority_section_failure_raises(db):
    """섹션 생성 과반 실패 시 AssemblyError(캐시 저장 안 함)."""
    _seed_corp(db)

    def fake_find(api_key, corp_code, year, kind, session):
        return "R2025" if (year, kind) == (2024, "annual") else None

    def broken_chat(model, system, user, temperature=0.3, **kw):
        if "단 하나의 섹션만" in system:
            raise bi.LLMError("섹션 생성 실패")
        return _fake_llm_chat(model, system, user, temperature, **kw)

    llm = MagicMock()
    llm.chat.side_effect = broken_chat

    with (
        patch.object(bi.dart, "find_periodic_report", side_effect=fake_find),
        patch.object(
            bi.dart_report_parser,
            "fetch_report_zip",
            return_value=_zip_bytes("II. 사업의 내용\n본문. III. 임원 등에 관한 사항\n"),
        ),
        patch.object(bi, "get_llm", return_value=llm),
        patch.object(bi.company_service, "report_stock_name", return_value="삼성전자"),
        patch.object(bi.company_service, "resolve_stock_name", return_value="삼성전자"),
        pytest.raises(bi.AssemblyError),
    ):
        bi.assemble_overview(db, _settings(), "005930")
    assert bi.get_cached_overview(db, "005930") is None


def test_assemble_overview_no_corp_code_returns_none(db):
    assert bi.assemble_overview(db, _settings(), "000000") is None


def test_assemble_overview_llm_unset_returns_none_but_stores_raw(db):
    """LLM 미설정 시 원문만 적재하고 조립은 None(캐시 미생성)."""
    _seed_corp(db)

    def fake_find(api_key, corp_code, year, kind, session):
        return "R2025" if (year, kind) == (2024, "annual") else None

    with (
        patch.object(bi.dart, "find_periodic_report", side_effect=fake_find),
        patch.object(
            bi.dart_report_parser,
            "fetch_report_zip",
            return_value=_zip_bytes("II. 사업의 내용\n본문. III. 임원 등에 관한 사항"),
        ),
        patch.object(bi, "get_llm", return_value=None),
    ):
        payload = bi.assemble_overview(db, _settings(), "005930")
    assert payload is None
    assert db.query(BusinessReportRaw).filter_by(stock_code="005930").count() >= 1  # 원문은 적재
    assert bi.get_cached_overview(db, "005930") is None  # 캐시 미생성


# ── map-reduce 단위 ───────────────────────────────────────────────────────
def test_chunk_text_respects_size_and_paragraph_boundaries():
    paras = [f"문단{i} " + "가" * 40 for i in range(50)]
    text = "\n".join(paras)
    chunks = bi._chunk_text(text, size=300)
    assert all(len(c) <= 300 for c in chunks)
    assert chunks[0].startswith("문단0")
    assert "문단49" in chunks[-1]
    # 재조립 시 모든 문단 보존 — 정보 손실 없음(기존 16K 절단과의 차이).
    joined = "\n".join(chunks)
    for i in range(50):
        assert f"문단{i}" in joined


def test_chunk_text_hard_splits_oversized_paragraph():
    chunks = bi._chunk_text("나" * 1500, size=600)
    assert len(chunks) == 3
    assert all(len(c) <= 600 for c in chunks)


def test_map_facts_prefixes_kind_and_drops_empty():
    llm = MagicMock()
    llm.chat.return_value = '{"facts": ["A", " ", "", "B"]}'
    assert bi._map_facts(llm, "m", "half", "본문") == ["[half] A", "[half] B"]


def test_review_fix_regenerates_only_gap_sections():
    """gap 이 지적한 섹션만 재생성하고 나머지는 건드리지 않는다(전체 재생성 아님)."""
    sections = {
        sid: {
            "id": sid,
            "title": sid,
            "narrative": f"{sid} 서술",
            "tables": [],
            "updated_by_kind": "annual",
        }
        for sid in bo.INVESTOR_SECTIONS
    }
    catalog = {t: [] for t in bi._REDUCE_TOPICS}
    llm = MagicMock()
    calls = {"review": 0, "synth": []}

    def fake_chat(model, system, user, temperature=0.3, **kw):
        import json as _json
        import re as _re

        if "절차 감사자다" in system:
            calls["review"] += 1
            if calls["review"] == 1:
                return (
                    '{"procedure_sound": false, "gaps": [{"target": "revenue_model", '
                    '"missing_step": "매출 구성 근거 부족", '
                    '"fix_instruction": "카탈로그의 매출 사실로 표를 만들어라"}]}'
                )
            return '{"procedure_sound": true, "gaps": []}'
        if "단 하나의 섹션만" in system:
            sid = _re.search(r"- id: (\w+)", system).group(1)
            calls["synth"].append(sid)
            return _json.dumps(
                {
                    "id": sid,
                    "title": sid,
                    "narrative": "보완됨",
                    "tables": [],
                    "updated_by_kind": "annual",
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected prompt: {system[:60]}")

    llm.chat.side_effect = fake_chat
    fixed, sound = bi._review_and_fix_sections(llm, "m", sections, catalog, "삼성전자")
    assert sound is True
    assert calls["synth"] == ["revenue_model"]  # gap 대상 1개만
    assert fixed["revenue_model"]["narrative"] == "보완됨"
    assert fixed["company_profile"]["narrative"] == "company_profile 서술"  # 타 섹션 무변경


def test_reduce_catalog_converges_on_large_input():
    """cap 초과 입력은 그룹 분할·단계적 병합으로 수렴한다(8키 카탈로그 반환)."""
    facts = [f"[annual] 사실{i} " + "다" * 100 for i in range(200)]  # ~22K chars > cap
    llm = MagicMock()

    def fake_chat(model, system, user, temperature=0.1, **kw):
        import json as _json

        n = sum(1 for line in user.split("\n") if line.startswith("["))
        return _json.dumps({t: ([f"{t}:{n}"] if t == "company" else []) for t in bi._REDUCE_TOPICS})

    llm.chat.side_effect = fake_chat
    catalog = bi._reduce_catalog(llm, "m", facts)
    assert set(catalog.keys()) == set(bi._REDUCE_TOPICS)


# ── 백필 ─────────────────────────────────────────────────────────────────
def _snap(code, d):
    return UniverseSnapshot(
        snapshot_date=d,
        stock_code=code,
        market="KOSPI",
        stock_name="X",
        stock_type="stock",
        market_cap=1e10,
        close_price=1000.0,
    )


def test_backfill_progressive_marks_done_and_resumes(db):
    db.add_all(
        [_snap("005930", datetime(2024, 1, 1).date()), _snap("000660", datetime(2024, 1, 1).date())]
    )
    db.commit()

    calls = {"n": 0}

    def fake_backfill_stock(dbb, settings, code):
        calls["n"] += 1
        bi._mark_done(dbb, code)  # run_backfill_progressive 가 내부에서 마킹하지만
        return True

    # _universe_codes 의 postgres ~ 정규 연산자는 sqlite 미지원 — 코드 리스트를 직접 반환.
    # backfill_budget_exhausted 는 실제 DART 키 환경에 결합 — 테스트에선 항상 여유로 고정.
    with (
        patch.object(bi, "_universe_codes", return_value=["005930", "000660"]),
        patch.object(bi.dart_throttle, "backfill_budget_exhausted", return_value=False),
        patch.object(bi, "backfill_stock", side_effect=fake_backfill_stock),
    ):
        result = bi.run_backfill_progressive(db, _settings(), per_run=1)
    assert result["done"] == 1
    assert bi._done_codes(db) == {"005930"}  # per_run=1 이라 한 건만
    # 두 번째 실행은 첫 종목이 완료 마커라 남은 한 건 처리.
    with (
        patch.object(bi, "_universe_codes", return_value=["005930", "000660"]),
        patch.object(bi.dart_throttle, "backfill_budget_exhausted", return_value=False),
        patch.object(bi, "backfill_stock", side_effect=fake_backfill_stock),
    ):
        result2 = bi.run_backfill_progressive(db, _settings(), per_run=1)
    assert result2["done"] == 1
    assert bi._done_codes(db) == {"005930", "000660"}
