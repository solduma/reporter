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

    capex 가 None 이더라도 다른 필드(revenue 등)는 채워진 IncomeEquity 를 반환할 수 있다.
    호출측은 필요한 필드별로 None 여부를 판정해 폴백을 결정한다.
    """
    rows = _flatten(fs_data)
    if not rows:
        return None
    return _parse_income_equity(rows)


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
