-- 사업 개요 조립 비동기 큐 — GET 캐시 미스·수동 갱신을 즉시 응답하고 worker 가 폴링 실행한다.
CREATE TABLE IF NOT EXISTS business_assembly_job (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(6) NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'pending',
    progress INTEGER NOT NULL DEFAULT 0,
    model VARCHAR(120) NOT NULL DEFAULT '',
    error TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_business_assembly_stock_status
    ON business_assembly_job (stock_code, status);
