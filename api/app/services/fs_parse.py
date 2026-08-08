"""FinancialStatement 원문(JSONB) → IncomeEquity 파싱 — DART 재호출 회피.

파이프라인 원칙: fnlttSinglAcntAll 응답이 이미 FinancialStatement.data 에 영속화되어
있으므로, fetch_income_and_equity 를 다시 부르지 않고 여기서 매출·영업이익·지배순이익·
EPS·지분·capex·법인세·세전이익·이자·차입금·현금을 파싱한다.

파싱 실패(매핑된 account_id 없음/amount None)는 fs_parse_gaps 테이블에 기록해
온톨로지 매핑(account_id) 보완 워크플로우를 제공한다. 호출측은 폴백(DART 직접 호출)을
결정한다.
"""

from __future__ import annotations

import logging
from dataclasses import fields as dc_fields

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.dart.client import IncomeEquity, _dart_account_ids, _parse_income_equity
from app.db.models import FsParseGap

logger = logging.getLogger(__name__)

# 파싱 대상 IncomeEquity 필드(원본 fnlttSinglAcntAll 에서 온 것들).
# borrowings/cash 는 BS 계정명 매칭이라 account_id 매핑이 없지만 파싱은 된다.
_PARSE_FIELDS: tuple[str, ...] = tuple(
    f.name for f in dc_fields(IncomeEquity) if f.name != "net_debt"
)

# 각 필드가 기대하는 온톨로지 account_id 집합(매핑 보완 단서용). dart.client 의 모듈
# 상수와 동일 세트 — 온톨로지 매핑이 SOT라 dart._dart_account_ids 로 매번 재산출.
_FIELD_AIDS: dict[str, set[str]] = {
    "revenue": _dart_account_ids("IS_REV_TOTAL"),
    "operating_income": _dart_account_ids("IS_OP_INCOME"),
    "net_income": _dart_account_ids("IS_NI_PARENT", "IS_NI_TOTAL"),
    "eps": _dart_account_ids("IS_EPS_BASIC"),
    "equity": _dart_account_ids("BS_EQ_PARENT", "BS_EQ_TOTAL"),
    "capex": _dart_account_ids("CF_INV_PPE", "CF_INV_INTANG"),
    "income_tax": _dart_account_ids("IS_TAX_TOTAL"),
    "pretax_income": _dart_account_ids("IS_PBT_TOTAL"),
    "interest_expense": _dart_account_ids("IS_NONOP_INT_EXP", "CF_OP_INTEREST_PAID", "CF_FIN_INTEREST_PAID"),
    # borrowings/cash 는 BS 계정명 매칭(account_id 없음) — expected_aids 빈값.
}


def _flatten(fs_data: dict) -> list[dict]:
    """FinancialStatement.data(JSONB 그룹) → _parse_income_equity 가 읽는 평평 rows.

    data 항목은 {account_id, name, amount, sj_div, level}. _parse_income_equity 는
    {account_id, account_nm, sj_div, thstrm_amount} 를 읽으므로 합성해 동일 파서 재사용.
    """
    rows: list[dict] = []
    for items in fs_data.values():
        for item in items or []:
            amt = item.get("amount")
            if amt is None:
                continue
            rows.append({
                "account_id": item.get("account_id", ""),
                "account_nm": item.get("name", ""),
                "sj_div": item.get("sj_div", ""),
                "thstrm_amount": str(amt),
            })
    return rows


def _found_aids(fs_data: dict, expected: set[str]) -> str:
    """FS 원문에 실제 존재하는 account_id 중 expected 와 매칭된 것. 매핑 보완 단서."""
    if not expected:
        return ""
    found = set()
    for items in fs_data.values():
        for item in items or []:
            aid = item.get("account_id", "")
            if aid and aid in expected:
                found.add(aid)
    return ",".join(sorted(found))


def parse_income_equity_from_fs(fs_data: dict) -> IncomeEquity | None:
    """FinancialStatement.data JSONB → IncomeEquity. FS 데이터 없으면 None.

    account_id 매칭(_parse_income_equity) 후 누락 필드는 한글 계정명 폴백으로 채운다.
    많은 한국 기업이 핵심 항목(유형자산취득·법인세비용·매출)을 '-표준계정코드 미사용-'
    (IFRS account_id 없음) 으로 보고해 account_id 매칭이 빗나가기 때문.
    """
    rows = _flatten(fs_data)
    if not rows:
        return None
    fin = _parse_income_equity(rows)
    _name_fallback(fin, fs_data)
    return fin


# ── 한글 계정명 폴백(account_id 미매칭 필드 보완) ───────────────────────────
# sj_div 제약으로 오탐 방지(매출↔매출원가, 취득↔자기주식취득 등). 온톨로지 매핑이
# '-표준계정코드 미사용-' 행을 못 잡는 회사를 커버. 갭 기반 분석으로 도출한 규칙.
def _name_fallback(fin: IncomeEquity, fs_data: dict) -> None:
    items_by_div = fs_data  # {BS:[], IS:[], CIS:[], CF:[], SCE:[]}

    def _amount_of(item) -> float | None:
        a = item.get("amount")
        return a if isinstance(a, (int, float)) and a is not None else None

    def _is_bs(item) -> bool:
        return item.get("sj_div") == "BS"

    def _is_cf(item) -> bool:
        return item.get("sj_div") == "CF"

    def _is_income(item) -> bool:
        return item.get("sj_div") in ("IS", "CIS")

    # capex: CF 투자활동 유형/무형자산 취득(abs 합). 처분·자기주식·사업결합 제외.
    if fin.capex is None:
        total = 0.0
        got = False
        for item in items_by_div.get("CF", []) or []:
            nm = (item.get("name") or "").replace(" ", "")
            if not ("유형자산의취득" in nm or "무형자산의취득" in nm):
                continue
            if any(x in nm for x in ("처분", "자기주식", "사업결합", "처분등")):
                continue
            a = _amount_of(item)
            if a is not None:
                total += abs(a)
                got = True
        if got:
            fin.capex = total

    # revenue: 손익 '매출'/'영업수익' 정확히(매출원가·매출총이익·매출채권 제외).
    if fin.revenue is None:
        for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
            if not _is_income(item):
                continue
            nm = (item.get("name") or "").replace(" ", "")
            if nm in ("매출", "영업수익", "매출액", "수익"):
                a = _amount_of(item)
                if a is not None:
                    fin.revenue = a
                    break

    # operating_income: 손익 '영업이익' 정확히.
    if fin.operating_income is None:
        for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
            if not _is_income(item):
                continue
            nm = (item.get("name") or "").replace(" ", "")
            if nm in ("영업이익", "영업이익(손실)"):
                a = _amount_of(item)
                if a is not None:
                    fin.operating_income = a
                    break

    # net_income: 손익 '당기순이익' 정확히(지배주주 우선).
    if fin.net_income is None:
        for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
            if not _is_income(item):
                continue
            nm = (item.get("name") or "").replace(" ", "")
            if "지배" in nm and "순이익" in nm:
                a = _amount_of(item)
                if a is not None:
                    fin.net_income = a
                    break
        if fin.net_income is None:
            for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
                if not _is_income(item):
                    continue
                nm = (item.get("name") or "").replace(" ", "")
                if nm in ("당기순이익", "당기순이익(손실)", "순이익"):
                    a = _amount_of(item)
                    if a is not None:
                        fin.net_income = a
                        break

    # eps: account_id EarningsPerShare(희석 폴백) 또는 '기본주당'/'주당순이익' 이름.
    if fin.eps is None:
        for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
            if not _is_income(item):
                continue
            aid = item.get("account_id") or ""
            nm = (item.get("name") or "").replace(" ", "")
            if "EarningsPerShare" in aid and "Diluted" not in aid:
                a = _amount_of(item)
                if a is not None:
                    fin.eps = a
                    break
        if fin.eps is None:
            for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
                if not _is_income(item):
                    continue
                nm = (item.get("name") or "").replace(" ", "")
                if ("기본주당" in nm and "순이익" in nm) or nm in ("주당순이익", "주당순손실"):
                    a = _amount_of(item)
                    if a is not None:
                        fin.eps = a
                        break

    # equity: BS '자본총계'/'지배주주지분'(지배 우선).
    if fin.equity is None:
        for item in items_by_div.get("BS", []) or []:
            if not _is_bs(item):
                continue
            nm = (item.get("name") or "").replace(" ", "")
            if "지배" in nm and "지분" in nm:
                a = _amount_of(item)
                if a is not None:
                    fin.equity = a
                    break
        if fin.equity is None:
            for item in items_by_div.get("BS", []) or []:
                if not _is_bs(item):
                    continue
                nm = (item.get("name") or "").replace(" ", "")
                if nm in ("자본총계", "총자본", "자본총계(순자산)"):
                    a = _amount_of(item)
                    if a is not None:
                        fin.equity = a
                        break

    # income_tax: 손익 '법인세'+'비용'(차감전/환급/납부 제외). account_id ifrs*_CurrentTax 등.
    if fin.income_tax is None:
        for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
            if not _is_income(item):
                continue
            nm = (item.get("name") or "").replace(" ", "")
            if "법인세" not in nm and "소득세" not in nm:
                continue
            if any(x in nm for x in ("차감전", "환급", "납부", "미수", "선급", "부채")):
                continue
            a = _amount_of(item)
            if a is not None:
                fin.income_tax = abs(a)
                break

    # pretax_income: 손익 '법인세비용차감전순이익' 정확히.
    if fin.pretax_income is None:
        for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
            if not _is_income(item):
                continue
            nm = (item.get("name") or "").replace(" ", "")
            if nm in ("법인세비용차감전순이익", "법인세비용차감전순이익(손실)", "법인세차감전순이익"):
                a = _amount_of(item)
                if a is not None:
                    fin.pretax_income = a
                    break

    # interest_expense: 손익 '금융비용'/'이자비용'(account_id ifrs_FinanceCosts), CF '이자지급' 폴백.
    if fin.interest_expense is None:
        for item in (items_by_div.get("IS", []) or []) + (items_by_div.get("CIS", []) or []):
            if not _is_income(item):
                continue
            aid = item.get("account_id") or ""
            nm = (item.get("name") or "").replace(" ", "")
            if aid == "ifrs_FinanceCosts" or nm in ("금융비용", "이자비용", "금융원가", "지급이자"):
                a = _amount_of(item)
                if a is not None:
                    fin.interest_expense = abs(a)
                    break
        if fin.interest_expense is None:
            for item in items_by_div.get("CF", []) or []:
                if not _is_cf(item):
                    continue
                nm = (item.get("name") or "").replace(" ", "")
                if "이자지급" in nm or "이자의지급" in nm:
                    a = _amount_of(item)
                    if a is not None:
                        fin.interest_expense = abs(a)
                        break

    # borrowings: BS 차입금 계정(증가/상환/감소 CF 제외).
    if fin.borrowings is None:
        total = 0.0
        got = False
        for item in items_by_div.get("BS", []) or []:
            if not _is_bs(item):
                continue
            nm = (item.get("name") or "").replace(" ", "")
            if not any(k in nm for k in ("단기차입금", "장기차입금", "사채", "유동성장기부채", "차입금")):
                continue
            if any(x in nm for x in ("증가", "상환", "감소", "유입")):
                continue
            a = _amount_of(item)
            if a is not None:
                total += abs(a)
                got = True
        if got:
            fin.borrowings = total

    # cash: BS '현금및현금성자산' 정확히(이름 유사 변형 포함).
    if fin.cash is None:
        for item in items_by_div.get("BS", []) or []:
            if not _is_bs(item):
                continue
            nm = (item.get("name") or "").replace(" ", "")
            if nm in ("현금및현금성자산", "현금및현금성자산등", "현금및예금"):
                a = _amount_of(item)
                if a is not None:
                    fin.cash = a
                    break


def record_gaps(
    db: Session,
    stock_code: str,
    period: str,
    fs_div: str,
    fin: IncomeEquity | None,
    fs_data: dict,
    *,
    fallback: str = "dart",
) -> None:
    """파싱 실패한 필드를 fs_parse_gaps 에 upsert — 온톨로지 매핑 보완 워크플로우용.

    fin 이 None(데이터 없음)이면 field='__all__' no_fs/no_rows 로 기록. 그 외엔
    _PARSE_FIELDS 중 None 인 필드를 기록. 이미 같은 갭이 있으면 updated_at 갱신.
    폴백(fallback)은 호출측이 지정(dart|skip) — 이 필드를 어떻게 메웠는지.
    """
    if fin is None:
        _upsert_gap(db, stock_code, period, fs_div, "__all__", "", "", "no_rows", fallback)
        return
    any_gap = False
    for field in _PARSE_FIELDS:
        val = getattr(fin, field, None)
        if val is not None:
            continue
        any_gap = True
        expected = _FIELD_AIDS.get(field, set())
        found = _found_aids(fs_data, expected)
        expected_str = ",".join(sorted(expected))
        # found 가 비어있으면 매핑된 account_id 자체가 FS 에 없음(no_match).
        # found 가 있는데 파싱 실패면 amount 가 None 이었을 가능(거의 안 됨).
        reason = "no_match" if not found else "amount_none"
        _upsert_gap(db, stock_code, period, fs_div, field, expected_str, found, reason, fallback)
    if any_gap:
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("fs_parse_gaps commit 실패 %s %s %s", stock_code, period, fs_div)


def _upsert_gap(
    db: Session,
    stock_code: str,
    period: str,
    fs_div: str,
    field: str,
    expected_aids: str,
    found_aids: str,
    reason: str,
    fallback: str,
) -> None:
    stmt = insert(FsParseGap).values(
        stock_code=stock_code,
        period=period,
        fs_div=fs_div,
        field=field,
        expected_aids=expected_aids,
        found_aids=found_aids,
        reason=reason,
        fallback=fallback,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_fs_parse_gap",
        set_={
            "expected_aids": expected_aids,
            "found_aids": found_aids,
            "reason": reason,
            "fallback": fallback,
        },
    )
    db.execute(stmt)
