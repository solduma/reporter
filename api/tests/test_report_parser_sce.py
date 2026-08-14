"""DART 원문 XML 자본변동표(SCE) 파서 단위 테스트 — 헤더 다단 병합·블록 분리·아이템 변환.

fixture 는 실측 원문을 축약한 형태다: 000890 2024 사업보고서 연결 SCE(기말자본
87,043,381,312)와 2025.09 분기보고서 별도 SCE(기말자본 88,762,871,568) 값 사용.
"""

from __future__ import annotations

import io
import zipfile

from app.adapters.dart import report_parser as p


def _zip(xml: str, name: str = "doc.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, xml.encode("utf-8"))
    return buf.getvalue()


# 연결 자본변동표(000890 2024 사업보고서 연결 SCE 축약). TH 2단: 그룹 행(지배기업지분·비지배·합계)
# + 최하위 행(5개 leaf). 데이터 행 7값. 전기 블록(2023.12.31) + 당기 블록(2024.12.31).
_CONS_XML = """<DOCUMENT>
<TITLE>연결 자본변동표</TITLE>
<P>(단위 : 원)</P>
<TABLE>
<TR><TH>과목</TH><TH COLSPAN="5">지배기업의 소유주에게 귀속되는 지분</TH><TH>비지배지분</TH><TH>자본 합계</TH></TR>
<TR><TH>자본금</TH><TH>주식발행초과금</TH><TH>기타자본구성요소</TH><TH>이익잉여금</TH><TH>지배기업의 소유주에게 귀속되는 지분 합계</TH></TR>
<TR><TD>2023.01.01 (기초자본)</TD><TD ALIGN="RIGHT">60,000,000,000</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">60,000,000,000</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">60,000,000,000</TD></TR>
<TR><TD>당기순이익(손실)</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">5,000,000,000</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD></TR>
<TR><TD>2023.12.31 (기말자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">3,381,100,708</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">84,127,266,202</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">84,127,266,202</TD></TR>
<TR><TD>2024.01.01 (기초자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">3,381,100,708</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">84,127,266,202</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">84,127,266,202</TD></TR>
<TR><TD>당기순이익(손실)</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">6,685,688,838</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD></TR>
<TR><TD>2024.12.31 (기말자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">3,381,100,708</TD><TD ALIGN="RIGHT">2,916,115,110</TD><TD ALIGN="RIGHT">87,043,381,312</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">87,043,381,312</TD></TR>
</TABLE>
</DOCUMENT>
"""

# 별도 자본변동표(000890 2025.09 분기보고서 별도 SCE 축약). 단일파일, 섹션 제목으로 식별.
# 5열. 데이터 행 5값. 기타자본구성요소가 음수인 실측 값 사용.
_SEP_XML = """<DOCUMENT>
<SECTION><TITLE>4-3. 자본변동표</TITLE></SECTION>
<P>(단위 : 원)</P>
<TABLE>
<TR><TH>과목</TH><TH>자본</TH></TR>
<TR><TH>자본금</TH><TH>주식발행초과금</TH><TH>기타자본구성요소</TH><TH>이익잉여금</TH><TH>자본 합계</TH></TR>
<TR><TD>2025.01.01 (기초자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">3,381,100,708</TD><TD ALIGN="RIGHT">2,916,115,110</TD><TD ALIGN="RIGHT">87,043,381,312</TD></TR>
<TR><TD>당기순이익(손실)</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">2,503,964,604</TD><TD ALIGN="RIGHT">-</TD></TR>
<TR><TD>2025.09.30 (기말자본)</TD><TD ALIGN="RIGHT">69,572,819,500</TD><TD ALIGN="RIGHT">11,173,345,994</TD><TD ALIGN="RIGHT">-2,020,644,257</TD><TD ALIGN="RIGHT">10,037,350,331</TD><TD ALIGN="RIGHT">88,762,871,568</TD></TR>
</TABLE>
</DOCUMENT>
"""


def _item(items: list[dict], name: str, detail: str) -> dict:
    return next(i for i in items if i["name"] == name and i["detail"] == detail)


def test_parse_sce_header_consolidated_group_rows_right_aligned():
    # 그룹 TH 행(지배기업지분·비지배·합계)이 최하위 leaf 밖 열을 채운다 → 7열.
    tstart = _CONS_XML.find("<TABLE")
    tend = _CONS_XML.find("</TABLE>") + len("</TABLE>")
    leaves = p._parse_sce_header(_CONS_XML, tstart, tend, 7)
    assert leaves == [
        "자본금",
        "주식발행초과금",
        "기타자본구성요소",
        "이익잉여금",
        "지배기업의 소유주에게 귀속되는 지분 합계",
        "비지배지분",
        "자본 합계",
    ]


def test_parse_sce_header_separate_5col():
    tstart = _SEP_XML.find("<TABLE")
    tend = _SEP_XML.find("</TABLE>") + len("</TABLE>")
    leaves = p._parse_sce_header(_SEP_XML, tstart, tend, 5)
    assert leaves == ["자본금", "주식발행초과금", "기타자본구성요소", "이익잉여금", "자본 합계"]


def test_split_sce_blocks_prior_and_current():
    tstart = _CONS_XML.find("<TABLE")
    tend = _CONS_XML.find("</TABLE>") + len("</TABLE>")
    rows = p._sce_rows(_CONS_XML, tstart, tend)
    blocks = p._split_sce_blocks(rows)
    assert [(d[0], d[1]) for d, _ in blocks] == [(2023, 12), (2024, 12)]
    # 각 블록은 기초자본 행으로 시작해 기말자본 행으로 끝난다(전기 먼저).
    labels = [label for label, _values in blocks[1][1]]
    assert labels[0] == "기초자본"
    assert labels[-1] == "기말자본"


def test_to_dart_items_normalizes_total_and_omits_empty():
    # 합계 열 → '연결재무제표 [member]', 빈 셀('-') 은 아이템 생략, ' 합계' leaf 는 접미사 제거.
    tstart = _CONS_XML.find("<TABLE")
    tend = _CONS_XML.find("</TABLE>") + len("</TABLE>")
    rows = p._sce_rows(_CONS_XML, tstart, tend)
    blocks = p._split_sce_blocks(rows)
    items = p._to_dart_items(
        blocks[1][1], p._parse_sce_header(_CONS_XML, tstart, tend, 7), "consolidated", 1
    )
    end = _item(items, "기말자본", "연결재무제표 [member]")
    assert end["amount"] == 87_043_381_312
    # '지배기업의 소유주에게 귀속되는 지분 합계' leaf → ' 합계' 제거한 detail.
    assert (
        _item(items, "기말자본", "지배기업의 소유주에게 귀속되는 지분 [member]")["amount"]
        == 87_043_381_312
    )
    # '-' 셀(비지배지분)은 아이템으로 남지 않는다.
    assert all(i["detail"] != "비지배지분 [member]" for i in items)


def test_to_dart_items_unit_scaling():
    # '(단위 : 천원)' 선언 → amount × 1,000(원 단위로 정규화).
    xml = _SEP_XML.replace("<P>(단위 : 원)</P>", "<P>(단위 : 천원)</P>")
    tstart = xml.find("<TABLE")
    tend = xml.find("</TABLE>") + len("</TABLE>")
    rows = p._sce_rows(xml, tstart, tend)
    blocks = p._split_sce_blocks(rows)
    items = p._to_dart_items(
        blocks[0][1], p._parse_sce_header(xml, tstart, tend, 5), "separate", 1000
    )
    assert _item(items, "기말자본", "별도재무제표 [member]")["amount"] == 88_762_871_568 * 1000


def test_parse_sce_blocks_consolidated_integration():
    # 000890 2024 사업보고서 연결 SCE 실측 앵커: 기말자본 87,043,381,312.
    blocks = p.parse_sce_blocks(_CONS_XML, want_consolidated=True)
    assert blocks is not None and len(blocks) == 2
    assert blocks[1][0] == (2024, 12, 31)
    items = blocks[1][1]
    assert _item(items, "기말자본", "연결재무제표 [member]")["amount"] == 87_043_381_312
    assert _item(items, "기말자본", "이익잉여금 [member]")["amount"] == 2_916_115_110
    assert _item(items, "당기순이익(손실)", "이익잉여금 [member]")["amount"] == 6_685_688_838


def test_parse_sce_blocks_separate_quarterly():
    # 000890 2025.09 분기보고서 별도 SCE 실측 앵커: 기말자본 88,762,871,568, 음수 기타자본.
    blocks = p.parse_sce_blocks(_SEP_XML, want_consolidated=False)
    assert blocks is not None and len(blocks) == 1
    assert blocks[0][0] == (2025, 9, 30)
    items = blocks[0][1]
    assert _item(items, "기말자본", "별도재무제표 [member]")["amount"] == 88_762_871_568
    assert _item(items, "기말자본", "기타자본구성요소 [member]")["amount"] == -2_020_644_257


def test_parse_sce_blocks_wrong_table_kind_returns_none():
    # 분기 단일파일(별도 SCE만)에서 연결을 요청하면 None(연결 섹션 없음).
    assert p.parse_sce_blocks(_SEP_XML, want_consolidated=True) is None
    # 연간 연결 파일에서 별도를 요청해도 None.
    assert p.parse_sce_blocks(_CONS_XML, want_consolidated=False) is None


def test_parse_sce_blocks_skips_toc():
    # 목차 항목(.....)은 제외하고 실제 섹션의 테이블을 찾는다.
    xml = "<DOCUMENT>1. 연결 자본변동표 ..... 30" + _CONS_XML[_CONS_XML.find("<TITLE>") :]
    blocks = p.parse_sce_blocks(xml, want_consolidated=True)
    assert blocks is not None and blocks[-1][0] == (2024, 12, 31)


def test_parse_sce_tables_from_zip_prefers_00761():
    # 연간 다중파일: 연결은 _00761.xml, 별도는 _00760.xml 우선.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.xml", _SEP_XML.encode("utf-8"))  # 본문(폴백용)
        zf.writestr("doc_00760.xml", _SEP_XML.encode("utf-8"))
        zf.writestr("doc_00761.xml", _CONS_XML.encode("utf-8"))
    tables = p.parse_sce_tables_from_zip(buf.getvalue())
    kinds = [t for t, _b in tables]
    assert kinds == ["consolidated", "separate"]
    cons = dict(tables)["consolidated"][-1][1]  # 당기(마지막) 블록
    assert _item(cons, "기말자본", "연결재무제표 [member]")["amount"] == 87_043_381_312


def test_parse_sce_tables_from_zip_single_file_both_sections():
    # 분기 단일파일에 연결·별도 섹션이 함께 있으면 섹션 제목으로 구분해 둘 다 반환.
    both = _SEP_XML.replace("4-3. 자본변동표", "2-3. 연결 자본변동표") + _SEP_XML
    tables = p.parse_sce_tables_from_zip(_zip(both))
    assert [t for t, _b in tables] == ["consolidated", "separate"]


def test_parse_sce_tables_from_zip_no_table_returns_empty():
    # SCE 테이블이 없는 문서(예: 정정공시 부속서류)는 빈 리스트 — 호출측 skip.
    assert p.parse_sce_tables_from_zip(_zip("<DOCUMENT><P>재무제표 본문 없음</P></DOCUMENT>")) == []
    assert p.parse_sce_tables_from_zip(b"not a zip") == []
