-- 딥다이브 완료 요약 — screener·comment 등 인사이트 소비처 참조용.
CREATE TABLE IF NOT EXISTS insight_feedback (
    stock_code VARCHAR(6) PRIMARY KEY,
    verdict VARCHAR(200),
    upside_pct DOUBLE PRECISION,
    risk_count INTEGER NOT NULL DEFAULT 0,
    summary_line TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
