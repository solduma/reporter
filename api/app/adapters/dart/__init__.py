"""OpenDART 어댑터 — 공시·재무제표 조회(driven adapter).

기존 `services.dart` / `dart_report_parser` / `dart_throttle` 공개 API 를 이 패키지에서
그대로 재노출해, 호출측은 `from app.adapters import dart` 후 `dart.fetch_*` 를 쓴다.
"""

from app.adapters.dart import report_parser, throttle
from app.adapters.dart.client import (
    CorpMapping,
    DartQuotaExceeded,
    Disclosure,
    Dividend,
    IncomeEquity,
    LargestShareholders,
    StockTotal,
    configure_from_settings,
    extract_ownership_reason,
    fetch_corp_mappings,
    fetch_disclosures,
    fetch_dividend,
    fetch_document_text,
    fetch_income_and_equity,
    fetch_largest_shareholders,
    fetch_ownership_changes,
    fetch_roe,
    fetch_stock_total,
    find_periodic_report,
)
from app.adapters.dart.report_parser import fetch_report_zip, parse_cf_depreciation

__all__ = [
    "CorpMapping",
    "DartQuotaExceeded",
    "Disclosure",
    "Dividend",
    "IncomeEquity",
    "LargestShareholders",
    "StockTotal",
    "configure_from_settings",
    "extract_ownership_reason",
    "fetch_corp_mappings",
    "fetch_disclosures",
    "fetch_dividend",
    "fetch_document_text",
    "fetch_income_and_equity",
    "fetch_largest_shareholders",
    "fetch_ownership_changes",
    "fetch_report_zip",
    "fetch_roe",
    "fetch_stock_total",
    "find_periodic_report",
    "parse_cf_depreciation",
    "report_parser",
    "throttle",
]
