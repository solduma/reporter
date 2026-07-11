"""SEC EDGAR 어댑터 — US 재무(XBRL companyfacts)·ticker/CIK 매핑(driven adapter).

호출측은 `from app.adapters import sec` 후 `sec.resolve_cik(...)`, `sec.fetch_company_facts(...)`.
"""

from app.adapters.sec import throttle
from app.adapters.sec.client import (
    Filing,
    company_name,
    describe_8k_items,
    fetch_company_facts,
    fetch_recent_filings,
    resolve_cik,
    ticker_map,
)

__all__ = [
    "Filing",
    "company_name",
    "describe_8k_items",
    "fetch_company_facts",
    "fetch_recent_filings",
    "resolve_cik",
    "throttle",
    "ticker_map",
]
