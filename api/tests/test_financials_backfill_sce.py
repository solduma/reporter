"""SCE 마이그레이션 루프 단위 테스트 — BS 기말자본 매칭·원문 파싱 저장.

네트워크 경계(DART list.json·document.xml)만 mock 하고, 파서·매칭은 실제 코드를
돌린다. CFS BS 값은 000890 2025.09(별도-as-CFS) 실측 사용.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.adapters.dart import report_parser as rp
from app.services import financials_backfill as fb
from tests.test_report_parser_sce import _CONS_XML, _SEP_XML, _zip


def _bs_000890_2025_09() -> list[dict]:
    """000890 2025.09 CFS BS — 별도 데이터(기타자본 음수·이익잉여금 10,037백만)."""
    return [
        {"name": "자본금", "amount": 69_572_819_500},
        {"name": "주식발행초과금", "amount": 11_173_345_994},
        {"name": "기타자본구성요소", "amount": -2_020_644_257},
        {"name": "이익잉여금(결손금)", "amount": 10_037_350_331},
    ]


def _bs_025320_connects() -> list[dict]:
    """025320 2024.12 CFS BS — 연결 데이터(비지배지분 존재)."""
    return [
        {"name": "자본금", "amount": 44_428_841_500},
        {"name": "자본잉여금", "amount": 103_300_873_962},
        {"name": "기타자본", "amount": 1_854_445_855},
        {"name": "이익잉여금(결손금)", "amount": 18_200_177_336},
        {"name": "비지배지분", "amount": -147_298_109},
    ]


def test_period_end_date():
    assert fb._period_end_date("2024.12") == (2024, 12, 31)
    assert fb._period_end_date("2025.09") == (2025, 9, 30)
    assert fb._period_end_date("2024.02") == (2024, 2, 29)  # 윤년


def test_norm_comp():
    # SCE leaf ↔ CFS BS name 어휘 차이 정규화.
    assert fb._norm_comp("이익잉여금(결손금)") == "이익잉여금"
    assert fb._norm_comp("이익잉여금") == "이익잉여금"
    assert (
        fb._norm_comp("지배기업의 소유주에게 귀속되는 지분") == "지배기업의소유주에게귀속되는자본"
    )
    assert fb._norm_comp("기타자본 구성요소") == "기타자본구성요소"


def _cons_candidates() -> list[tuple[str, list]]:
    blocks = rp.parse_sce_blocks(_CONS_XML, want_consolidated=True)
    return [("consolidated", blocks)]


def _sep_candidates() -> list[tuple[str, list]]:
    blocks = rp.parse_sce_blocks(_SEP_XML, want_consolidated=False)
    return [("separate", blocks)]


def test_match_sce_table_picks_connecting_consolidated():
    # 000890 2024.12 CFS BS(연결 값) → 연결 SCE 선택(비지배지분 '-' 이어도 자본금·이익잉여금 등 일치).
    bs = [
        {"name": "자본금", "amount": 69_572_819_500},
        {"name": "주식발행초과금", "amount": 11_173_345_994},
        {"name": "기타자본구성요소", "amount": 3_381_100_708},
        {"name": "이익잉여금(결손금)", "amount": 2_916_115_110},
    ]
    matched = fb._match_sce_table(_cons_candidates(), bs)
    assert [t for t, _b in matched] == ["consolidated"]


def test_match_sce_table_picks_separate_for_separate_cfs():
    # 000890 2025.09 CFS BS(별도 값) → 별도 SCE 선택.
    matched = fb._match_sce_table(_sep_candidates(), _bs_000890_2025_09())
    assert [t for t, _b in matched] == ["separate"]


def test_match_sce_table_connects_when_both_present_with_nci():
    # 연결·별도가 자본 구성요소 값을 공유(동점)하지만 CFS BS 에 비지배지분(연결 증거)이 있으면
    # 연결 테이블을 우선한다. 동점 자체도 '연결+별도'가 아니라 '연결'만 남는다.
    tables = rp.parse_sce_tables_from_zip(_zip(_CONS_AND_SEP_SHARED))
    matched = fb._match_sce_table(tables, _bs_025320_connects())
    assert [t for t, _b in matched] == ["consolidated"]


def test_match_sce_table_no_match_returns_empty():
    # BS 값이 어떤 테이블과도 일치하지 않으면 빈 리스트(판별 불가 — 저장 안 함).
    bs = [{"name": "자본금", "amount": 1}, {"name": "이익잉여금(결손금)", "amount": 2}]
    assert fb._match_sce_table(_sep_candidates(), bs) == []
    assert fb._match_sce_table([], _bs_000890_2025_09()) == []


def test_run_sce_migration_for_code_parses_and_upserts(monkeypatch):
    """종목당 list.json 1회 → zip 파싱 → BS 매칭 → SCE 저장 + 캐시 삭제."""
    reports = [
        {"rcept_no": "20251114003027", "report_nm": "분기보고서 (2025.09)"},
        {"rcept_no": "20250318001002", "report_nm": "사업보고서 (2024.12)"},
    ]

    def _fake_list(api_key, corp_code, bgn_de, session):
        assert bgn_de == "20250101"  # pending 기간 최소 연도
        return reports

    def _fake_zip(api_key, rcept_no, session):
        # 보고서별 원문 zip. 분기보고서 = 별도 SCE 단일파일.
        if rcept_no == "20251114003027":
            return _zip(_SEP_XML)
        return _zip(_CONS_XML)

    db = MagicMock()
    db.scalar.return_value = "00000000"  # corp_code
    row = SimpleNamespace(data={"BS": _bs_000890_2025_09(), "CIS": []})
    db.scalars.return_value.first.return_value = row
    settings = MagicMock(dart_api_key="key")

    monkeypatch.setattr(fb.dart, "find_all_periodic_reports", _fake_list)
    monkeypatch.setattr(fb, "fetch_report_zip", _fake_zip)

    updated = fb._run_sce_migration_for_code(db, settings, "000890", ["2025.09"], MagicMock())
    assert updated == 1
    # 저장된 SCE 는 별도 기말자본 88,762,871,568 을 담는다.
    sce = row.data["SCE"]
    end = next(i for i in sce if i["name"] == "기말자본" and i["detail"] == "별도재무제표 [member]")
    assert end["amount"] == 88_762_871_568
    # 캐시 삭제 + 커밋.
    assert db.execute.call_count >= 1
    db.commit.assert_called_once()


# 연결·별도가 같은 자본 구성요소 값을 공유하는 발행사(025320 류) — 비지배지분만 다르다.
# 연결 테이블이 비지배지분을 포함한 7열, 별도는 5열.
_CONS_AND_SEP_SHARED = """<DOCUMENT>
<TITLE>연결 자본변동표</TITLE>
<P>(단위 : 원)</P>
<TABLE>
<TR><TH>과목</TH><TH COLSPAN="5">지배기업의 소유주에게 귀속되는 지분</TH><TH>비지배지분</TH><TH>자본 합계</TH></TR>
<TR><TH>자본금</TH><TH>자본잉여금</TH><TH>기타자본</TH><TH>이익잉여금</TH><TH>지배기업의 소유주에게 귀속되는 지분 합계</TH></TR>
<TR><TD>2024.01.01 (기초자본)</TD><TD ALIGN="RIGHT">44,428,841,500</TD><TD ALIGN="RIGHT">103,300,873,962</TD><TD ALIGN="RIGHT">1,854,445,855</TD><TD ALIGN="RIGHT">18,200,177,336</TD><TD ALIGN="RIGHT">167,784,338,653</TD><TD ALIGN="RIGHT">-147,298,109</TD><TD ALIGN="RIGHT">167,637,040,544</TD></TR>
<TR><TD>2024.12.31 (기말자본)</TD><TD ALIGN="RIGHT">44,428,841,500</TD><TD ALIGN="RIGHT">103,300,873,962</TD><TD ALIGN="RIGHT">1,854,445,855</TD><TD ALIGN="RIGHT">18,200,177,336</TD><TD ALIGN="RIGHT">167,784,338,653</TD><TD ALIGN="RIGHT">-147,298,109</TD><TD ALIGN="RIGHT">167,637,040,544</TD></TR>
</TABLE>
<TITLE>자본변동표</TITLE>
<P>(단위 : 원)</P>
<TABLE>
<TR><TH>과목</TH><TH>자본</TH></TR>
<TR><TH>자본금</TH><TH>자본잉여금</TH><TH>기타자본</TH><TH>이익잉여금</TH><TH>자본 합계</TH></TR>
<TR><TD>2024.01.01 (기초자본)</TD><TD ALIGN="RIGHT">44,428,841,500</TD><TD ALIGN="RIGHT">103,300,873,962</TD><TD ALIGN="RIGHT">1,854,445,855</TD><TD ALIGN="RIGHT">18,200,177,336</TD><TD ALIGN="RIGHT">167,784,338,653</TD></TR>
<TR><TD>2024.12.31 (기말자본)</TD><TD ALIGN="RIGHT">44,428,841,500</TD><TD ALIGN="RIGHT">103,300,873,962</TD><TD ALIGN="RIGHT">1,854,445,855</TD><TD ALIGN="RIGHT">18,200,177,336</TD><TD ALIGN="RIGHT">167,784,338,653</TD></TR>
</TABLE>
</DOCUMENT>
"""


# 2026.03 분기보고서 단일파일: 전기 블록(2025.12.31) + 당기 블록(2026.03.31).
# 2025.12 사업보고서가 아직 미제출이라도 전기 블록이 2025.12 기말자본을 담는다.
_Q1_2026_XML = """<DOCUMENT>
<SECTION><TITLE>4-3. 자본변동표</TITLE></SECTION>
<P>(단위 : 원)</P>
<TABLE>
<TR><TH>과목</TH><TH>자본</TH></TR>
<TR><TH>자본금</TH><TH>주식발행초과금</TH><TH>기타자본구성요소</TH><TH>이익잉여금</TH><TH>자본 합계</TH></TR>
<TR><TD>2025.01.01 (기초자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">3,381,100,708</TD><TD ALIGN="RIGHT">2,916,115,110</TD><TD ALIGN="RIGHT">87,043,381,312</TD></TR>
<TR><TD>2025.12.31 (기말자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">-2,020,644,257</TD><TD ALIGN="RIGHT">10,037,350,331</TD><TD ALIGN="RIGHT">88,762,871,568</TD></TR>
<TR><TD>2026.01.01 (기초자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">-2,020,644,257</TD><TD ALIGN="RIGHT">10,037,350,331</TD><TD ALIGN="RIGHT">88,762,871,568</TD></TR>
<TR><TD>2026.03.31 (기말자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">-2,020,644,257</TD><TD ALIGN="RIGHT">12,000,000,000</TD><TD ALIGN="RIGHT">90,725,521,237</TD></TR>
</TABLE>
</DOCUMENT>
"""


def test_run_sce_migration_missing_period_block_covered_by_prior(monkeypatch):
    """보고서 미제출 기간(2025.12 사업보고서 없음)은 전기 블록으로 커버해야 한다.

    분기보고서(2026.03)의 전기 블록이 2025.12 기말자본을 담음 → BS 매칭으로 채운다.
    """

    def _fake_list(api_key, corp_code, bgn_de, session):
        return [{"rcept_no": "20260512003001", "report_nm": "분기보고서 (2026.03)"}]

    def _fake_zip(api_key, rcept_no, session):
        return _zip(_Q1_2026_XML)

    db = MagicMock()
    db.scalar.return_value = "00000000"
    row = SimpleNamespace(data={"BS": _bs_000890_2025_09()})
    db.scalars.return_value.first.return_value = row
    settings = MagicMock(dart_api_key="key")

    monkeypatch.setattr(fb.dart, "find_all_periodic_reports", _fake_list)
    monkeypatch.setattr(fb, "fetch_report_zip", _fake_zip)

    updated = fb._run_sce_migration_for_code(db, settings, "000890", ["2025.12"], MagicMock())
    assert updated == 1
    assert row.data["SCE"]


def test_run_sce_migration_dedupe_latest_filing_wins(monkeypatch):
    """정정 공시: (기말날짜, 연결구분) 중복 시 최신 접수(rcept_no 큰 값)의 값으로 통일."""
    # 둘 다 BS 구성요소는 일치(동점)하되 자본 합계 값만 다르다 — dedupe 가 최신(정정)을 골라야.
    older = _CONS_XML.replace("87,043,381,312", "87,000,000,000")
    newer = _CONS_XML

    def _fake_list(api_key, corp_code, bgn_de, session):
        # 실제 find_all_periodic_reports 의 정렬(접수 최신 우선)과 동일한 순서.
        return [
            {"rcept_no": "20250401000001", "report_nm": "사업보고서 정정 (2024.12)"},
            {"rcept_no": "20250318001002", "report_nm": "사업보고서 (2024.12)"},
        ]

    def _fake_zip(api_key, rcept_no, session):
        return _zip(older if rcept_no == "20250318001002" else newer)

    db = MagicMock()
    db.scalar.return_value = "00000000"
    row = SimpleNamespace(
        data={
            "BS": [
                {"name": "자본금", "amount": 69_572_819_500},
                {"name": "주식발행초과금", "amount": 11_173_345_994},
                {"name": "기타자본구성요소", "amount": 3_381_100_708},
                {"name": "이익잉여금(결손금)", "amount": 2_916_115_110},
            ]
        }
    )
    db.scalars.return_value.first.return_value = row
    settings = MagicMock(dart_api_key="key")

    monkeypatch.setattr(fb.dart, "find_all_periodic_reports", _fake_list)
    monkeypatch.setattr(fb, "fetch_report_zip", _fake_zip)

    updated = fb._run_sce_migration_for_code(db, settings, "000890", ["2024.12"], MagicMock())
    assert updated == 1
    end = next(
        i
        for i in row.data["SCE"]
        if i["name"] == "기말자본" and i["detail"] == "연결재무제표 [member]"
    )
    assert end["amount"] == 87_043_381_312  # 최신(정정) 원문 값


def test_run_sce_migration_no_corp_code_returns_zero(monkeypatch):
    db = MagicMock()
    db.scalar.return_value = None  # 종목 미매핑
    assert fb._run_sce_migration_for_code(db, MagicMock(), "999999", ["2024.12"], MagicMock()) == 0


def test_run_sce_migration_zip_fetch_failure_skips(monkeypatch):
    def _fake_list(api_key, corp_code, bgn_de, session):
        return [{"rcept_no": "20251114003027", "report_nm": "분기보고서 (2025.09)"}]

    def _fake_zip(api_key, rcept_no, session):
        return None  # 다운로드 실패

    db = MagicMock()
    db.scalar.return_value = "00000000"
    db.scalars.return_value.first.return_value = SimpleNamespace(data={"BS": _bs_000890_2025_09()})
    settings = MagicMock(dart_api_key="key")

    monkeypatch.setattr(fb.dart, "find_all_periodic_reports", _fake_list)
    monkeypatch.setattr(fb, "fetch_report_zip", _fake_zip)

    updated = fb._run_sce_migration_for_code(db, settings, "000890", ["2025.09"], MagicMock())
    assert updated == 0
    db.commit.assert_not_called()
