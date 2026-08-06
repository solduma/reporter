"""배치 메타데이터 — TUI 와 scheduler 가 공유하는 문자열 메타데이터.

문자열만 포함하므로 import 비용이 거의 없습니다.
실행 함수는 여전히 app.scheduler 의 MANUAL_BATCHES 에 있습니다.
"""

from __future__ import annotations

# 수동/TUI 실행 가능한 배치 목록. (key, label, 실행함수)
# 실행 함수 없이 key/label 만 필요하면 이 리스트를 공유합니다.
# (실행 함수는 scheduler.py 에서 import — 순환 참조 방지)
MANUAL_BATCHES_KEYS: list[tuple[str, str]] = [
    ("ingest_cycle", "리포트·시황 수집"),
    ("candle_batch", "일봉 수집"),
    ("intraday_refresh", "30분봉 갱신"),
    ("nightly_batch", "성장·RS 야간배치"),
    ("news_events", "뉴스·종목이벤트"),
    ("disclosure_batch", "공시 수집"),
    ("financials_backfill", "재무 백필(10년)"),
    ("ofs_statements", "별도재무제표 백필"),
    ("related_company", "관계사 수집"),
    ("report_backfill", "리포트 백필(10년)"),
    ("business_overview_backfill", "사업개요 백필"),
    ("report_fulltext", "리포트 원문 소급적재"),
    ("backfill_progressive", "일봉 백필(10년)"),
    ("us_universe", "US 유니버스"),
    ("us_candle_backfill", "US 일봉 백필(10년)"),
    ("us_disclosure", "US 공시(8-K)"),
    ("us_financials", "US 재무 백필(SEC)"),
    ("calendar", "경제·실적 캘린더"),
    ("risk_free", "무위험금리(국고채)"),
    ("capex_backfill", "CAPEX 백필(FCFF)"),
    ("market_premium", "시장 ERP(Damodaran)"),
    ("sce_migrate", "SCE 마이그레이션"),
    ("business_overview_refresh", "사업개요 갱신(새보고서)"),
    ("business_research_queue", "사업 리서치 큐"),
]

# MANUAL_BATCHES key(수동/TUI) → ingest_log 에 남는 잡 이름.
BATCH_KEY_TO_LOG_JOB: dict[str, str] = {
    "disclosure_batch": "disclosures",
    "financials_backfill": "financials_10y",
    "report_backfill": "report_10y",
    "backfill_progressive": "backfill_10y",
}

# 배치 실행 메타데이터 — heartbeat timeout 등.
_DEFAULT_TIMEOUT_SECONDS = 600
BATCH_META: dict[str, dict[str, object]] = {
    key: {"label": label, "heartbeat_timeout_seconds": _DEFAULT_TIMEOUT_SECONDS}
    for key, label in MANUAL_BATCHES_KEYS
}
# 무거운 백필/배포는 heartbeat timeout을 늘린다.
BATCH_META["release_deploy"] = {"label": "release 배포", "heartbeat_timeout_seconds": 1800}
BATCH_META["ofs_statements"] = {"label": "별도재무제표 백필", "heartbeat_timeout_seconds": 1800}
BATCH_META["financials_backfill"] = {"label": "재무 백필(10년)", "heartbeat_timeout_seconds": 1800}
BATCH_META["report_backfill"] = {"label": "리포트 백필(10년)", "heartbeat_timeout_seconds": 1800}
BATCH_META["business_overview_backfill"] = {
    "label": "사업개요 백필",
    "heartbeat_timeout_seconds": 1800,
}
BATCH_META["business_overview_refresh"] = {
    "label": "사업개요 갱신(새보고서)",
    "heartbeat_timeout_seconds": 1800,
}
BATCH_META["report_fulltext"] = {"label": "리포트 원문 소급적재", "heartbeat_timeout_seconds": 1800}
BATCH_META["us_financials"] = {"label": "US 재무 백필(SEC)", "heartbeat_timeout_seconds": 1800}
