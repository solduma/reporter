# Financial Ontology → reporter 앱 통합 설계

온톨로지(187 계정·57 비율·의존성 그래프·다표준 매핑·산업 extension)를 api/web/infra 기존 서비스에 통합하는 단계별 설계. 구현 전 설계 확정용.

## 현재 상태 요약

- 온톨로지 실사용 = `/api/companies/{code}/financial-statements` 응답의 `ontology_id` 1곳.
- 응답 시점 동적 정규화(`companies.py:641,712`), **DB 미영속화**.
- DART account_id 매칭(`dart/client.py:386-571` hardcoded set) ≠ `dart_mapping.yaml` — 별도 관리.
- 비율 계산 2체계: 온톨로지 `RatioEngine`(57) vs `analysis_scoring`/`domain/valuation`.
- LLM 재무 입력 = 한국어 공시명 key 그대로(`deepdive/tools.py:99`).
- web 라벨 ~10개 컴포넌트 하드코딩 + `lib/glossary.ts` 중복.

## 데이터 흐름 (통합 기준점)

```
DART ─ dart/client.py ─┬─ Financial (한국어 컬럼)
                       ├─ FinancialStatement (JSONB, account_id+account_nm)  ← A1 영속화 지점
                       └─ ReportFinancial (원단위)
Naver ────────────────── Financial (보강)
SEC EDGAR ────────────── UsFinancial (US-GAAP, 별도)

Financial ─┬─ /financials, /peers, /summary, /growth, /analysis (한국어)
           ├─ /screener (SQL 한국어 컬럼)
           └─ deepdive tool_financials() → LLM (한국어 key)

FinancialStatement ─ /financial-statements (현재 동적 ontology_id)
OntologyPort ─ /api/ontology/* (독립, 미연결)
```

수집 writer 2곳: `financials_backfill.py:143`(백필), `company_service.py:182`(온디맨드).

---

## 단계별 설계

### A. 수집/저장 기반

#### A1. FinancialStatement 항목에 ontology_id 영속화
- **대상**: `app/services/financials_backfill.py:143`, `app/services/company_service.py:182` — `fetch_full_statements()` 결과를 `FinancialStatement.data` JSONB에 저장하기 전에 항목별 `ontology_id` 주입.
- **구현**: 두 writer 공통 전처리 함수 `enrich_with_ontology_id(items)`를 `app/services/ontology.py`에 추가(`OntologyPort.normalize` 사용, 괄호 접미사 fallback 포함). 기존 `companies.py:641` 동적 부여는 영속화된 값이 있으면 그대로 사용, 없으면 fallback 정규화(마이그레이션 기간).
- **DB**: `FinancialStatement.data` 항목 dict에 `ontology_id` 키 추가(스키마 변경 없음, JSONB).
- **마이그레이션**: 기존 행은 백필 재실행 또는 일괄 정규화 배치(`scripts/backfill_ontology_id.py`)로 보강.
- **검증**: 기존 동적 부여 결과와 영속화 결과가 동일(정규화 결정성), coverage 측정, pytest(`test_financial_statements` regression).
- **PR**: 단일 PR. 의존성: A2와 동시 또는 선행.

#### A2. DART account_id 매핑 SOT 통일
- **대상**: `app/adapters/dart/client.py:386-571`의 hardcoded `_AID_*` set + account_nm 보조 매칭.
- **구현**: `dart_mapping.yaml`(`by_taxonomy`/`by_korean_name` 역인덱스)을 통해 `OntologyPort` 경유 정규화. hardcoded set을 온톨로지 매핑 기반 조회로 교체. 매핑 누락 항목은 기존 동작 유지(fallback) 후 커버리지 로깅.
- **위험**: 매핑 누락 → 수집 결측. 반드시 기존 account_id set이 온톨로지에 모두 존재하는지 사전 검증(`scripts/audit_dart_mapping.py`).
- **검증**: 삼성전자/현대차/KB금융 예시 기업 전 품목 수집 회귀(값 동일), DART 일일한도 고려(백업키 폴오버 유지).
- **PR**: A1과 분리 또는 통합. 권장 분리(회귀 범위 다름).

#### A3. Financial 테이블 정준 ID 보강 (점진)
- **대상**: `Financial` ORM(`models.py:120`). 컬럼명 변경 위험도 상 → **별도 매핑 메타**(`financial_column_ontology.yaml` 또는 `app/services/ontology.py` 정적 맵)로 `revenue`→`IS_REV_TOTAL` 등을 노출만. 컬럼 자체는 유지.
- **용도**: C/E 단계에서 값을 온톨로지 ID 키로 변환할 때 참조.
- **PR**: A1/A2 이후, 독립.

---

### B. 표시 단계 라벨 통일 (web, 낮은 위험)

#### B1. 라벨 단일 출처
- **신규 엔드포인트**: `GET /api/ontology/labels?ids=...` 또는 `/accounts` 확장 — 정준 한국어명·영문명·단위·설명을 batch 반환. 캐싱(온톨로지는 정적 → 메모리 캐시).
- **web 소비**: `web/lib/ontology-labels.ts`(fetch + 캐시 훅). 아래 컴포넌트에서 하드코딩 라벨을 온톨로지 정준명으로 교체:
  - `FinancialsLineChart.tsx:37-44`(영업이익/매출/당기순이익/EV-EBITDA/ROE/EPS)
  - `MultipleBandChart.tsx:43-46`(PER/PBR/PSR)
  - `GrowthMetrics.tsx:120-136`(매출YoY/영업이익/순이익/EBITDA)
  - `PeersTable.tsx:18-27`(PER/PBR/PSR/ROE/EV-EBITDA)
  - `screener/page.tsx:176-220`(컬럼), `51-120`(필터 프리셋 라벨)
  - `us/[ticker]/page.tsx:87-97`, `us-screener/page.tsx:193-204`
- **검증**: **browser-verify 필수**(로컬 web 43100 + API 워밍 + 스크린샷). eslint+tsc. 라벨 미매칭 시 기존 하드코딩 fallback 유지(회귀 방지).
- **PR**: 라벨 출처 엔드포인트 + 훅(1), 각 컴포넌트 교체(그룹별 분할 가능).

#### B2. glossary 통일
- **대상**: `web/lib/glossary.ts`(per/pbr/roe/ev_ebitda/revenue_yoy 등 하드코딩 설명).
- **구현**: 온톨로지 ratio/account `description`으로 대체. B1 훅과 동일 출처. 미매칭 키는 기존 설명 유지.
- **검증**: 툴팁 렌더링 browser-verify.

#### B3. 딥다이브 Section 라벨
- **대상**: `DeepDiveReportView.tsx:37-46`(JSON 키를 라벨로 사용).
- **구현**: E1(LLM 정준화)과 연계 — LLM 출력 키를 정준명 매핑 테이블로 변환. E1 선행 시 자연스럽게 해결. 단독으로는 가독성 개선 정도.

---

### C. 비율 엔진 통합 (신규 가치)

#### C1. /companies/{code}/ratios 엔드포인트
- **신규**: `app/routers/companies.py`에 `GET /api/companies/{code}/ratios`. `OntologyPort.calculate_many(ratio_ids, values)` 호출.
- **값 키 매핑**: A3 매핑 메타로 `Financial`/`FinancialStatement` 값을 `{ontology_id: value}` dict로 변환. 기간 접미(`:opening`/`:closing`/`:prior`) 처리 — `RatioEngine`이 이미 지원.
- **응답**: 비율별 `{id, name, value, reason}` — `reason="composite_or_manual"`이면 값 null(잘못된 값 방어).
- **web**: `web/components/RatioPanel.tsx` 신규 — 57비율 중 노출 대상(profitability/liquidity/leverage/valuation) 탭 구성, 결측 사유 표시.
- **검증**: pytest(계정 ID 참조 무결성), browser-verify. 계산값 vs 기존 `Financial.per/roe`(독립 로직) 교차 검증 — 차이 시 사유 명시(정규화/기간 정의 차이).
- **PR**: 엔드포인트+서비스(1), web 패널(1).

#### C2. 기존 scoring과 정합
- **대상**: `app/domain/analysis_scoring.py`(value_score_abs, growth_score).
- **원칙**: 온톨로지를 "비율 정의·계산 정본"으로, scoring의 가중/정규화는 유지(하이브리드). scoring이 쓰는 비율값을 C1 결과에서 가져오도록 리팩터 — 단, 정규화 로직 차이로 점수 변동 방지(회귀 테스트로 값 동일 보장).
- **위험**: 점수 회귀. 일괄 교체 지양, 점진적.
- **PR**: C1 이후, scoring별 개별 PR.

#### C3. 그래프 근거 표시
- **대상**: `Graph.transitive_inputs(ratio_id)`로 "어느 계정에서 파생" 표시.
- **web**: RatioPanel 각 비율에 근거 펼침(입력 계정 목록). 온톨로지에 이미 존재하는 데이터.
- **PR**: C1에 포함 또는 후속.

---

### D. 스크리너 비율 기반화

#### D1. 조건 키 정준화
- **대상**: `screener/page.tsx` 프리셋 + `app/routers/screener.py`/`screener_service.py` SQL.
- **구현**: 필터 키를 ontology ratio ID로 정규화 매핑(`valuation.per` ↔ `Financial.per`). 라벨/설명은 B1. SQL은 여전히 `Financial` 컬럼 참조(성능) — 매핑 레이어만 추가.
- **검증**: 기존 5전략 결과 동일 회귀.
- **PR**: B1, A3 이후.

#### D2. 동적 비율 필터 (장기)
- 온톨로지 ratio 목록으로 사용자 정의 조건 생성 UI. 기존 5전략 프리셋 유지.
- **PR**: D1 이후, 별도.

---

### E. 딥다이브/LLM 정준화

#### E1. LLM 컨텍스트 강화
- **대상**: `deepdive/tools.py:99`(tool_financials), `stages.py` 각 단계 `_fin_series`.
- **구현**: 재무 입력에 `ontology_id`+정준명+정의를 추가. key는 기존 한국어명 유지(LLM 호환) + 메타 부가. `Graph.ratio_inputs`로 관련 비율 힌트 제공.
- **위험**: 프롬프트 길이 증가, LLM 동작 변화. A/B 평가(기존 vs 강화) — 딥다이브 HITL critique로 정성 검증.
- **검증**: 삼성전자 딥다이브 회귀, Ollama stream timeout 유지.
- **PR**: E1 단일. `reporter-deepdive-*` 메모리 참고(이벤트·HITL 함정).

#### E2. 출력 정준 ID 연계
- LLM이 정준 ID로 수치 인용 → web에서 항목 하이라이트/드릴다운. E1 후속, LLM 지시 필요.
- **PR**: E1 이후.

#### E3. 산업 extension 적용
- **대상**: 은행/보험/증권 — `bank/insurance/securities.yaml`의 NIM·NPL·종합비율·레버리지.
- **구현**: 종목 산업 감지 → 산업별 비율 세트 선택. C1 RatioPanel과 연계.
- **PR**: C1 이후.

---

### F. US 통합·교차표준 (장기, 고위험)

- `usgaap_mapping`으로 `UsFinancial`을 동일 ontology_id로 정규화 → KR/US 동일 비율/라벨.
- US 경로 전면 재설계(`domain/us_financials.py`, `routers/us.py`, `us/*` web). 위험도 높음, 후순위.
- 별도 상세 설계 필요.

---

## PR 순서 및 의존성

```
A1 ─┬─→ A3 ─→ C1 ─→ C2 ─→ C3
A2 ──┘            ↗
B1(엔드포인트+훅) ─→ B2 ─→ B3 ─(E1)─→
                   ↘ D1 ─→ D2
C1 ─→ E3
E1 ─→ E2
F (독립 장기)
```

권장 순서:
1. **A1** — 영속화(기반)
2. **A2** — DART 매핑 통일(수집 정확성)
3. **A3** — 정준 ID 매핑 메타
4. **B1**(엔드포인트+훅) → **B2** — 라벨/glossary(가시화, browser-verify)
5. **C1** — 비율 엔드포인트+패널(신규 가치)
6. **C2**(점진) — scoring 정합
7. **D1** — 스크리너 키 정준화
8. **E1** → **E2** → **E3** — 딥다이브
9. **D2**, **F** — 장기

## 공통 검증 기준 (각 PR)

- **api**: ruff + `lint-imports`(12계약 유지 — 새 포트/어댑터 시 `api/.importlinter` 업데이트) + pytest 회귀(기존 로직 값 동일).
- **web**: eslint + tsc + **browser-verify**(로컬 43100 스크린샷, web/ 변경 시 필수).
- **financial-ontology**: pytest + validate.py(참조 무결성).
- **배포**: worker(API·domain 변경 시) / API(라우터·스키마) / web(빌드 후) — diff 위치로 판단. 재무 수집 코드(A1/A2)는 worker+API 둘 다. 프로덕션 배포 전 사용자 확인.

## 롤백

- A1: `ontology_id` 키는 additive → 미존 시 동적 부여 fallback 유지로 즉시 롤백 가능.
- A2: hardcoded set을 기존 경로로 복귀(한 PR 범위).
- B: 라벨 fallback(하드코딩 유지) → 미매칭 시 기존 표시.
- C1: 신규 엔드포인트/패널 → 제거만으로 롤백.
- E1: 프롬프트 복귀 — 단, 회귀 비교 데이터 보존 필수.

## 미결정 사항 (설계 확정 시 결정)

1. **C2 정합 범위**: scoring을 온톨로지 비율값 기반으로 얼마나 교체할지(전부 vs 비율 정의만).
2. **E1 평가 방식**: LLM 출력 정성 평가 기준(HITL critique 외 정량 지표 필요?).
3. **A3 매핑 메타 형태**: YAML(`financial-ontology/`) vs `app/services/ontology.py` 정적 dict.
4. **US(F) 착수 시점**: KR 안정화 후 vs 병행.