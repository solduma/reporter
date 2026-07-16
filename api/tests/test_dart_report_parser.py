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


def test_none_when_no_da_anywhere():
    # recon 블록도 없고 상각비 라벨도 전혀 없으면 None(은행·성격별 note only 등).
    xml = """<DOCUMENT><TABLE>
    <TR><TD>이자수익</TD><TD ALIGN="RIGHT">999,999</TD></TR>
    </TABLE></DOCUMENT>"""
    assert p.parse_cf_depreciation(_zip(xml)) is None


# 대형사(삼성 등): CF 본표가 '조정'을 요약(주석번호만)하고 감가상각을 유형·무형자산 주석으로 뺀 형태.
# recon 블록이 D&A 를 못 담아 fallback(주석 당기 상각비 합산)이 작동해야 한다.
_NOTE_ONLY_XML = """
<DOCUMENT><TITLE>연 결 현 금 흐 름 표</TITLE>
<P>(단위 : 백만원)</P>
<TABLE>
<TR><TD>가. 당기순이익</TD><TD ALIGN="RIGHT">10,000,000</TD></TR>
<TR><TD>나. 조정</TD><TD ALIGN="RIGHT">27</TD></TR>
<TR><TD>다. 자산부채의 변동</TD><TD ALIGN="RIGHT">28</TD></TR>
</TABLE>
<TITLE>유형자산</TITLE>
<TABLE>
<TR><TD>감가상각비</TD><TD ALIGN="RIGHT">39,649,982</TD><TD ALIGN="RIGHT">37,000,000</TD></TR>
</TABLE>
<TITLE>무형자산</TITLE>
<TABLE>
<TR><TD>무형자산상각비</TD><TD ALIGN="RIGHT">2,980,840</TD><TD ALIGN="RIGHT">2,800,000</TD></TR>
</TABLE></DOCUMENT>
"""


def test_note_fallback_when_recon_summarizes_adjustment():
    # CF 조정이 주석번호(27)만 담아 recon 이 D&A 를 못 찾을 때, 유형·무형자산 주석 당기 상각비 합산.
    dep = p.parse_cf_depreciation(_zip(_NOTE_ONLY_XML))
    assert dep == (39_649_982 + 2_980_840) * 1_000_000  # 당기(첫 등장)만


def test_recon_takes_priority_over_note_fallback():
    # recon 블록에 D&A 가 있으면 fallback 을 타지 않는다(회귀 방지 — 정상 종목 값 불변).
    dep = p.parse_cf_depreciation(_zip(_CF_XML))
    assert dep == (13_121_135 + 552_541) * 1_000_000


def test_note_fallback_takes_current_year_only():
    # 주석 표가 당기→전기 순이라 각 라벨 첫 등장(당기)만. 전기값·중복합산 방지.
    xml = """<DOCUMENT><TITLE>연결현금흐름표</TITLE><P>(단위 : 백만원)</P>
    <TABLE><TR><TD>가. 당기순이익</TD><TD ALIGN="RIGHT">100</TD></TR>
    <TR><TD>나. 조정</TD><TD ALIGN="RIGHT">27</TD></TR></TABLE>
    <TABLE>
    <TR><TD>감가상각비</TD><TD ALIGN="RIGHT">5,000</TD></TR>
    <TR><TD>감가상각비</TD><TD ALIGN="RIGHT">4,000</TD></TR>
    </TABLE></DOCUMENT>"""
    dep = p.parse_cf_depreciation(_zip(xml))
    assert dep == 5_000 * 1_000_000  # 첫 등장(당기)만, 4000(전기) 미합산


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
