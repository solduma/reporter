"""DART 서비스 단위 테스트 — corpCode 매핑·공시 목록 파싱을 목킹으로 검증한다."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date
from unittest.mock import MagicMock

import pytest

from app.adapters import dart

_CORPCODE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<result>
<list><corp_code>00126380</corp_code><corp_name>\xec\x82\xbc\xec\x84\xb1\xec\xa0\x84\xec\x9e\x90</corp_name><stock_code>005930</stock_code></list>
<list><corp_code>00164779</corp_code><corp_name>\xed\x95\x9c\xea\xb5\xad</corp_name><stock_code></stock_code></list>
</result>"""


def _zip_session() -> MagicMock:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", _CORPCODE_XML)
    resp = MagicMock()
    resp.content = buf.getvalue()
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_fetch_corp_mappings_keeps_only_listed():
    # stock_code 있는 상장사만 반환(빈 stock_code 는 제외)
    mappings = dart.fetch_corp_mappings("key", _zip_session())
    assert len(mappings) == 1
    assert mappings[0].stock_code == "005930"
    assert mappings[0].corp_code == "00126380"
    assert mappings[0].corp_name == "삼성전자"


def _list_session(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_fetch_disclosures_parses_and_builds_url():
    payload = {
        "status": "000",
        "total_page": 1,
        "list": [
            {
                "rcept_no": "20260707000403",
                "report_nm": "주요사항보고서(자기주식처분결정)",
                "flr_nm": "삼성전자",
                "rcept_dt": "20260707",
            }
        ],
    }
    discs = dart.fetch_disclosures(
        "key", "00126380", "005930", date(2026, 6, 1), date(2026, 7, 8), _list_session(payload)
    )
    assert len(discs) == 1
    d = discs[0]
    assert d.rcept_no == "20260707000403"
    assert d.rcept_dt == date(2026, 7, 7)
    assert d.stock_code == "005930"
    assert "20260707000403" in d.dart_url


def test_fetch_disclosures_passes_pblntf_ty_when_given():
    # pblntf_ty='B'(주요사항보고 DS005)를 주면 서버 파라미터로 전달, 없으면 미포함.
    sess = _list_session({"status": "013"})
    dart.fetch_disclosures(
        "key", "00126380", "005930", date(2026, 6, 1), date(2026, 7, 8), sess, pblntf_ty="B"
    )
    _, kwargs = sess.get.call_args
    assert kwargs["params"]["pblntf_ty"] == "B"

    sess2 = _list_session({"status": "013"})
    dart.fetch_disclosures(
        "key", "00126380", "005930", date(2026, 6, 1), date(2026, 7, 8), sess2
    )
    _, kwargs2 = sess2.get.call_args
    assert "pblntf_ty" not in kwargs2["params"]


def _doc_zip_session(xml_by_name: dict[str, bytes]) -> MagicMock:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in xml_by_name.items():
            zf.writestr(name, content)
    resp = MagicMock()
    resp.content = buf.getvalue()
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_fetch_document_text_strips_tags_and_truncates():
    xml = "<doc><TITLE>자기주식처분</TITLE><P>총 1,083,434주 처분</P></doc>".encode()
    sess = _doc_zip_session({"20260710000585.xml": xml})
    text = dart.fetch_document_text("key", "20260710000585", sess, max_chars=6000)
    assert "<" not in text and ">" not in text  # 태그 제거
    assert "자기주식처분" in text
    assert "총 1,083,434주 처분" in text
    # max_chars 절삭
    short = dart.fetch_document_text("key", "20260710000585", sess, max_chars=5)
    assert len(short) == 5


def test_fetch_document_text_bad_zip_returns_empty():
    resp = MagicMock()
    resp.content = b"not a zip"
    resp.raise_for_status = MagicMock()
    sess = MagicMock()
    sess.get.return_value = resp
    assert dart.fetch_document_text("key", "x", sess) == ""


def test_fetch_disclosures_empty_status_returns_empty():
    # status != 000 (예: 013 데이터없음) → 빈 리스트
    discs = dart.fetch_disclosures(
        "key", "00126380", "005930", date(2026, 6, 1), date(2026, 7, 8),
        _list_session({"status": "013", "message": "조회된 데이타가 없습니다."}),
    )
    assert discs == []


def _sync_state_db(synced_at, synced_from):
    """sync_disclosures 의 db.execute(select(synced_at, synced_from)).first() 를 흉내낸다."""
    db = MagicMock()
    row = MagicMock()
    row.synced_at = synced_at
    row.synced_from = synced_from
    db.execute.return_value.first.return_value = row
    return db


def test_sync_disclosures_skips_within_ttl_when_depth_covered(monkeypatch):
    # TTL(6h) 이내 + 요청 begin 이 이미 동기화된 깊이(synced_from) 안이면 DART 재조회 억제.
    from datetime import datetime, timedelta

    from app.services import dart_ingest

    recent = datetime.now(UTC) - timedelta(hours=1)
    db = _sync_state_db(recent, date(2024, 1, 1))  # 2024년까지 이미 동기화됨

    called = {"fetch": False}

    def _should_not_fetch(*a, **k):
        called["fetch"] = True
        return []

    monkeypatch.setattr(dart_ingest.dart, "fetch_disclosures", _should_not_fetch)

    settings = MagicMock()
    # 요청 begin(2026-04-01) >= synced_from(2024-01-01) → 커버됨 → 스킵.
    result = dart_ingest.sync_disclosures(db, settings, "005930", date(2026, 4, 1), date(2026, 7, 8))

    assert result == 0
    assert called["fetch"] is False


def test_sync_disclosures_refetches_when_request_deeper_than_synced(monkeypatch):
    # TTL 이 유효해도 요청이 동기화된 깊이보다 더 과거(begin < synced_from)면 재조회해야 한다.
    from datetime import datetime, timedelta

    from app.services import dart_ingest

    recent = datetime.now(UTC) - timedelta(hours=1)  # TTL 이내
    db = _sync_state_db(recent, date(2026, 6, 30))  # 최근 14일만 얕게 동기화됨

    called = {"fetch": False}

    def _fetch(*a, **k):
        called["fetch"] = True
        return []  # 목록 비어도 fetch 시도 자체를 검증

    disc = MagicMock()
    disc.fetch_disclosures.side_effect = _fetch
    monkeypatch.setattr(dart_ingest, "_disclosures", lambda s: disc)
    monkeypatch.setattr(dart_ingest, "ensure_corp_mappings", lambda *a, **k: None)
    monkeypatch.setattr(dart_ingest, "get_llm", lambda s: MagicMock())
    monkeypatch.setattr(dart_ingest, "_mark_synced", lambda *a, **k: None)
    # corp_code 조회(db.scalar) 는 값 반환.
    db.scalar.return_value = "00126380"

    settings = MagicMock()
    # 요청 begin(2024-07-14, ~2년) < synced_from(2026-06-30) → 재조회.
    dart_ingest.sync_disclosures(db, settings, "005930", date(2024, 7, 14), date(2026, 7, 14))

    assert called["fetch"] is True  # 더 깊은 과거 요청이라 DART 재조회함


def test_fetch_ownership_changes_parses_signed_delta():
    # elestock: 부호있는 증감·콤마 숫자·개행 직위를 rcept_no 로 매핑한다.
    payload = {
        "status": "000",
        "list": [
            {
                "rcept_no": "20260701000528",
                "repror": "최준기",
                "isu_exctv_rgist_at": "비등기임원",
                "isu_exctv_ofcps": "담당",
                "sp_stock_lmp_cnt": "2,705",
                "sp_stock_lmp_irds_cnt": "-1,500",  # 처분
            },
            {
                "rcept_no": "20260702000039",
                "repror": "윤원일",
                "isu_exctv_rgist_at": "비등기임원",
                "isu_exctv_ofcps": "사장\n(호주 법인장)",
                "sp_stock_lmp_cnt": "9,214",
                "sp_stock_lmp_irds_cnt": "3,000",  # 취득
            },
        ],
    }
    changes = dart.fetch_ownership_changes("key", "00164779", _list_session(payload))
    assert set(changes) == {"20260701000528", "20260702000039"}
    sell = changes["20260701000528"]
    assert sell.shares_delta == -1500 and sell.shares_after == 2705
    assert sell.reporter == "최준기"
    buy = changes["20260702000039"]
    assert buy.shares_delta == 3000
    assert buy.position == "사장 (호주 법인장)"  # 개행 정규화


def test_fetch_ownership_changes_empty_status_returns_empty():
    changes = dart.fetch_ownership_changes("key", "x", _list_session({"status": "013"}))
    assert changes == {}


# status 020(일일한도초과)은 013(데이터없음)과 달리 '없음'으로 뭉개면 안 되고 예외로 올려
# 호출측(백필·딥다이브)이 중단·구분하게 한다. 조회형 4개 함수 모두 동일하게 동작.
def _quota_session() -> MagicMock:
    return _list_session({"status": "020", "message": "사용한도를 초과하였습니다."})


def test_find_periodic_report_raises_on_quota():
    with pytest.raises(dart.DartQuotaExceeded):
        dart.find_periodic_report("key", "00126380", 2026, "annual", _quota_session())


def test_fetch_disclosures_raises_on_quota():
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_disclosures(
            "key", "00126380", "005930", date(2026, 6, 1), date(2026, 7, 8), _quota_session()
        )


def test_fetch_income_and_equity_raises_on_quota():
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_income_and_equity("key", "00126380", 2025, 4, _quota_session())


def test_fetch_ownership_changes_raises_on_quota():
    with pytest.raises(dart.DartQuotaExceeded):
        dart.fetch_ownership_changes("key", "00126380", _quota_session())


def test_extract_ownership_reason_skips_table_labels():
    # 표 헤더('취득/처분')는 건너뛰고 실제 변동 사유('장내매도(-)')를 뽑는다.
    body = "소 유 주 식 수 (주) 취득/처분단가(원) 비 고 변동전 증감 변동후 장내매도(-) 2026.06.24 보통주"
    assert dart.extract_ownership_reason(body) == "장내매도"
    assert dart.extract_ownership_reason("사유 없는 본문") == ""


def test_extract_ownership_reason_skips_sign_legend_before_table():
    # 표 앞 부호 범례('증감수량의 (+)는 취득...')를 사유로 오인하지 않고, '변동후' 뒤 실제 사유를 잡는다.
    body = (
        "※ 증감수량의 (+)는 취득, (-)는 처분을 의미합니다. "
        "소유주식수 취득/처분단가 비고 변동전 증감 변동후 장내매수(+) 2026.07.01 보통주"
    )
    assert dart.extract_ownership_reason(body) == "장내매수"
