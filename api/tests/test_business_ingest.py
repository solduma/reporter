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
                "id": "business_summary",
                "title": "사업 개요",
                "narrative": "요약",
                "tables": [],
                "updated_by_kind": "annual",
            },
        ]
    }
    reports = [("R1", bo.ANNUAL, 2024)]
    sections = bi._map_sections(result, reports)
    ids = [s["id"] for s in sections]
    assert ids == list(bo.INVESTOR_SECTIONS)
    summary = next(s for s in sections if s["id"] == "business_summary")
    assert summary["narrative"] == "요약"
    assert summary["updated_by_rcept"] == "R1"  # kind→rcept 매핑
    # 누락 섹션은 빈 값
    empty = next(s for s in sections if s["id"] == "market_risk")
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


def test_assemble_overview_persists_cache(db):
    """원문 추출·적재 → LLM 정리 → 캐시 저장 흐름(모킹)."""
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
    llm.chat.return_value = '{"sections": [{"id": "business_summary", "title": "사업 개요", "narrative": "반도체 제조", "tables": [], "updated_by_kind": "annual"}]}'

    with (
        patch.object(bi.dart, "find_periodic_report", side_effect=fake_find),
        patch.object(bi.dart_report_parser, "fetch_report_zip", side_effect=fake_fetch_zip),
        patch.object(bi, "get_llm", return_value=llm),
        patch.object(
            bi.review_loop,
            "run_with_review",
            side_effect=lambda llm_, m, prod, rev, **kw: prod(None),
        ),
        patch.object(bi.company_service, "report_stock_name", return_value="삼성전자"),
        patch.object(bi.company_service, "resolve_stock_name", return_value="삼성전자"),
    ):
        payload = bi.assemble_overview(db, _settings(), "005930")

    assert payload is not None
    assert payload["as_of_annual_rcept"] == "R2025"
    ids = [s["id"] for s in payload["sections"]]
    assert ids == list(bo.INVESTOR_SECTIONS)  # 매핑이 빈 섹션까지 채움
    # 원문 적재 확인
    raw = db.query(BusinessReportRaw).filter_by(stock_code="005930").all()
    assert any(r.rcept_no == "R2025" and r.section_id == bo.SECTION_BUSINESS_CONTENT for r in raw)
    # 캐시 적재 확인
    assert bi.get_cached_overview(db, "005930") is not None


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
    with (
        patch.object(bi, "_universe_codes", return_value=["005930", "000660"]),
        patch.object(bi, "backfill_stock", side_effect=fake_backfill_stock),
    ):
        result = bi.run_backfill_progressive(db, _settings(), per_run=1)
    assert result["done"] == 1
    assert bi._done_codes(db) == {"005930"}  # per_run=1 이라 한 건만
    # 두 번째 실행은 첫 종목이 완료 마커라 남은 한 건 처리.
    with (
        patch.object(bi, "_universe_codes", return_value=["005930", "000660"]),
        patch.object(bi, "backfill_stock", side_effect=fake_backfill_stock),
    ):
        result2 = bi.run_backfill_progressive(db, _settings(), per_run=1)
    assert result2["done"] == 1
    assert bi._done_codes(db) == {"005930", "000660"}
