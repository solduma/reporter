-- financials unique constraint 에 fs_div 추가 (버그 #2 수정)
-- 실행: psql -d reporter -f infra/migrations/20260813_financials_uq_add_fs_div.sql
--
-- 배경: 04b7345(07-21)가 모델의 uq_financial 을 (stock_code, period, fs_div) 로
-- 바꿨지만 라이브 DB constraint 는 (stock_code, period) 그대로였다. 그 결과 모든
-- 백필에서 OFS insert 가 CFS 행과 충돌해 OFS 값이 CFS 행을 덮어썼다(fs_div 는 'CFS'
-- 로 남음) — financials 의 "CFS" 행 ~95% 가 실제 OFS(별도) 값.
-- 안전성: (stock_code, period, fs_div) 중복 0, FK 참조 0 확인 후 실행.

BEGIN;
ALTER TABLE financials DROP CONSTRAINT uq_financial;
ALTER TABLE financials ADD CONSTRAINT uq_financial UNIQUE (stock_code, period, fs_div);
COMMIT;
