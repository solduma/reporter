-- US 재무 XBRL 원시 ontology 영속화 컬럼 추가 (F3b)
-- 실행: psql -d reporter -f infra/migrations/20260723_us_financial_raw_ontology.sql

ALTER TABLE us_financials ADD COLUMN IF NOT EXISTS raw_ontology JSON;
COMMENT ON COLUMN us_financials.raw_ontology IS 'SEC companyfacts XBRL 계정 ontology 정규화 원시 데이터';
