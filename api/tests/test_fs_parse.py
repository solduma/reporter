"""fs_parse._name_fallback 확장 테스트 — DART 원문 이름 변형 커버.

fs_parse_gaps 조사(2026-08-15)에서 발견한 매핑 갭: 폴백이 정준 이름만 매칭해
'차입부채'(부채)·'유형자산 취득'(의 없음)·'분기순이익'·섹션 접두사('Ⅲ.영업이익') 등
실제 보고서 표기 변형을 놓쳤다. 각 케이스는 실측 종목(032830·105560·051910·011930 등)에서
확인한 이름을 사용한다.
"""

from __future__ import annotations

from app.services.fs_parse import parse_income_equity_from_fs


def _fs(bs=None, is_=None, cis=None, cf=None) -> dict:
    return {"BS": bs or [], "IS": is_ or [], "CIS": cis or [], "CF": cf or [], "SCE": []}


def _item(name: str, amount: float, sj_div: str, account_id: str = "-표준계정코드 미사용-") -> dict:
    return {"account_id": account_id, "name": name, "amount": amount, "sj_div": sj_div, "level": 0}


# ── borrowings: '차입부채'(부채) 표기 + 총계 우선 ──

def test_borrowings_chabubchae_total():
    """'차입부채' 총계 행 단독 사용(032830 실측: 23,113,898,000,000)."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("차입부채", 23_113_898_000_000, "BS"),
    ]))
    assert fin.borrowings == 23_113_898_000_000


def test_borrowings_total_preferred_over_components():
    """총계+구성요소 동시 제출 보고서는 총계만 사용(이중계상 방지)."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("차입부채", 100_000_000_000, "BS"),
        _item("단기차입부채", 40_000_000_000, "BS"),
        _item("장기차입부채", 60_000_000_000, "BS"),
    ]))
    assert fin.borrowings == 100_000_000_000  # 200B 가 아니라 100B


def test_borrowings_components_sum_when_no_total():
    """총계 행이 없으면 단기/장기/유동성장기 구성요소 합산."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("단기차입부채", 27_203_999_532, "BS"),
        _item("장기차입부채", 50_000_000_000, "BS"),
        _item("유동성장기차입부채", 10_000_000_000, "BS"),
    ]))
    assert fin.borrowings == 87_203_999_532


def test_borrowings_section_prefix_total():
    """'III.차입부채' 섹션 접두사 제거 후 총계 단독 사용(001270 실측)."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("III.차입부채", 50_000_000_000, "BS"),
    ]))
    assert fin.borrowings == 50_000_000_000


def test_borrowings_parenthesized_components():
    """'차입부채(유동)'+'차입부채(비유동)' 구성요소 합산(323350 실측)."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("차입부채(유동)", 30_000_000_000, "BS"),
        _item("차입부채(비유동)", 70_000_000_000, "BS"),
    ]))
    assert fin.borrowings == 100_000_000_000


def test_borrowings_short_forms():
    """'단기차입'+'장기차입' 접미사 없는 표기 합산(036670 실측)."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("단기차입", 20_000_000_000, "BS"),
        _item("장기차입", 40_000_000_000, "BS"),
    ]))
    assert fin.borrowings == 60_000_000_000


def test_borrowings_other_variants():
    """'유동차입부채'·'유동성차입부채'·'기타차입부채' 변형 합산(361570·214610·060240 실측)."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("유동차입부채", 10_000_000_000, "BS"),
        _item("유동성차입부채", 5_000_000_000, "BS"),
        _item("기타차입부채", 3_000_000_000, "BS"),
    ]))
    assert fin.borrowings == 18_000_000_000


# ── cash: '현금및예치금'·섹션 접두사 ──

def test_cash_deposits_bank():
    """은행 '현금 및 예치금'(105560 실측: 659,832,000,000)."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("현금 및 예치금", 659_832_000_000, "BS"),
    ]))
    assert fin.cash == 659_832_000_000


def test_cash_section_prefix():
    """'1.현금및현금성자산' 섹션 번호 접두사 제거."""
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("1.현금및현금성자산", 12_345_678_000, "BS"),
    ]))
    assert fin.cash == 12_345_678_000


def test_cash_amortized_cost_deposits():
    fin = parse_income_equity_from_fs(_fs(bs=[
        _item("현금및상각후원가측정예치금", 5_000_000_000, "BS"),
    ]))
    assert fin.cash == 5_000_000_000


# ── capex: '유형자산 취득'(의 없음) ──

def test_capex_no_eui():
    """'유형자산 취득'(의 없음) 표기(011930 실측: 3,795,938,487). 처분·자기주식 제외."""
    fin = parse_income_equity_from_fs(_fs(cf=[
        _item("유형자산 취득", 3_795_938_487, "CF"),
        _item("무형자산 취득", -362_512_082, "CF"),
        _item("자기주식의 취득", 434_880, "CF"),
    ]))
    assert fin.capex == 3_795_938_487 + 362_512_082  # 자기주식 제외, abs 합


def test_capex_with_eui_still_matches():
    """기존 '유형자산의 취득' 표기도 계속 매칭."""
    fin = parse_income_equity_from_fs(_fs(cf=[
        _item("유형자산의 취득", 1_000_000_000, "CF"),
    ]))
    assert fin.capex == 1_000_000_000


# ── net_income: 분기/반기순이익 ──

def test_net_income_quarterly():
    """분기 보고서 '분기순이익'(051910 실측: 585,044,000,000)."""
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("분기순이익", 585_044_000_000, "IS"),
    ]))
    assert fin.net_income == 585_044_000_000


def test_net_income_consolidated_quarterly():
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("연결분기순이익", 345_031_540, "IS"),
    ]))
    assert fin.net_income == 345_031_540


# ── operating_income: 섹션 접두사·영업손실 ──

def test_operating_income_section_prefix():
    """'Ⅲ. 영업이익' 로마숫자 접두사 제거."""
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("Ⅲ. 영업이익", 1_234_000_000, "IS"),
    ]))
    assert fin.operating_income == 1_234_000_000


def test_operating_income_loss():
    """'영업손실' 표기(241560 실측: -3,808,749,000) — 음수 유지."""
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("영업손실", -3_808_749_000, "IS"),
    ]))
    assert fin.operating_income == -3_808_749_000


# ── pretax_income: 차감전이익/손실/손익 ──

def test_pretax_income_profit_variant():
    """'법인세비용차감전이익'(순이익 아님) 표기."""
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("법인세비용차감전이익", 2_000_000_000, "IS"),
    ]))
    assert fin.pretax_income == 2_000_000_000


def test_pretax_income_loss_variant():
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("법인세비용차감전순손실", -500_000_000, "IS"),
    ]))
    assert fin.pretax_income == -500_000_000


# ── eps: 주당이익 변형 ──

def test_eps_plain():
    """'주당이익' 표기(032640 실측: 372)."""
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("주당이익", 372, "IS"),
    ]))
    assert fin.eps == 372


def test_eps_continuing_ops():
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("계속사업주당순이익", 1_250, "IS"),
    ]))
    assert fin.eps == 1_250


# ── revenue: 영업수익(매출액)·섹션 접두사 ──

def test_revenue_parenthesized():
    """'영업수익(매출액)' 표기."""
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("영업수익(매출액)", 500_000_000_000, "IS"),
    ]))
    assert fin.revenue == 500_000_000_000


def test_revenue_section_prefix():
    """'Ⅰ. 영업수익' 접두사 제거."""  # noqa: RUF002
    fin = parse_income_equity_from_fs(_fs(is_=[
        _item("Ⅰ. 영업수익", 300_000_000_000, "IS"),  # noqa: RUF001
    ]))
    assert fin.revenue == 300_000_000_000
