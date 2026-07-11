"""DART 원문 XML 현금흐름표 감가상각 파서 단위 테스트 — 셀·단위·라벨·오탐 규칙."""

from __future__ import annotations

import io
import zipfile

from app.adapters.dart import report_parser as p


def _zip(xml: str, name: str = "doc.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, xml.encode("utf-8"))
    return buf.getvalue()


# 현금흐름표 recon 블록(순이익+조정) + 감가상각비·무형자산상각비 셀. 단위 백만원.
_CF_XML = """
<DOCUMENT><TITLE>연 결 현 금 흐 름 표</TITLE>
<P>(단위 : 백만원)</P>
<TABLE>
<TR><TD>당기순이익</TD><TD ALIGN="RIGHT">1,000,000</TD></TR>
<TR><TD>조정</TD><TD ALIGN="RIGHT">500,000</TD></TR>
<TR><TD>감가상각비</TD><TD ALIGN="RIGHT">13,121,135</TD><TD ALIGN="RIGHT">12,000,000</TD></TR>
<TR><TD>무형자산상각비</TD><TD ALIGN="RIGHT">552,541</TD><TD ALIGN="RIGHT">500,000</TD></TR>
</TABLE></DOCUMENT>
"""


def test_parses_cf_depreciation_with_unit_conversion():
    # 감가 13,121,135 + 무형 552,541 (백만원) → 원. 당기값(첫 우측정렬)만.
    dep = p.parse_cf_depreciation(_zip(_CF_XML))
    assert dep == (13_121_135 + 552_541) * 1_000_000


def test_te_cells_are_parsed():
    # 일부 발행사는 <TE> 셀을 쓴다.
    xml = _CF_XML.replace("<TD>", "<TE>").replace("</TD>", "</TE>").replace("<TD ", "<TE ")
    dep = p.parse_cf_depreciation(_zip(xml))
    assert dep == (13_121_135 + 552_541) * 1_000_000


def test_none_when_no_recon_block():
    # 순이익+조정 recon 블록이 없으면(성격별 note only 등) None.
    xml = """<DOCUMENT><TABLE>
    <TR><TD>감가상각비</TD><TD ALIGN="RIGHT">999,999</TD></TR>
    </TABLE></DOCUMENT>"""
    assert p.parse_cf_depreciation(_zip(xml)) is None


def test_excludes_accumulated_depreciation():
    # '감가상각누계액'(BS 잔액)은 제외어라 잡지 않는다.
    xml = """<DOCUMENT><TITLE>현금흐름표</TITLE>
    <TABLE>
    <TR><TD>당기순이익</TD><TD ALIGN="RIGHT">100</TD></TR>
    <TR><TD>조정</TD><TD ALIGN="RIGHT">50</TD></TR>
    <TR><TD>감가상각누계액</TD><TD ALIGN="RIGHT">9,999,999</TD></TR>
    </TABLE></DOCUMENT>"""
    # 누계액만 있고 진짜 감가상각비 없음 → None.
    assert p.parse_cf_depreciation(_zip(xml)) is None


def test_negative_parenthesis_amount_absolute():
    xml = """<DOCUMENT><TITLE>현금흐름표</TITLE>
    <TABLE>
    <TR><TD>당기순이익</TD><TD ALIGN="RIGHT">100</TD></TR>
    <TR><TD>가감</TD><TD ALIGN="RIGHT">50</TD></TR>
    <TR><TD>감가상각비</TD><TD ALIGN="RIGHT">(1,234)</TD></TR>
    </TABLE></DOCUMENT>"""
    # 음수 표기라도 D&A 는 절댓값(원).
    assert p.parse_cf_depreciation(_zip(xml)) == 1234


def test_bad_zip_returns_none():
    assert p.parse_cf_depreciation(b"not a zip") is None
