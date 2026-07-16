# 종목 딥다이브 — 아키텍처 설계

> 상태: **설계(구현 전 합의용)**. 승인 후 단계적 구현.
> 목표: 종목을 입력하면 5단계 딥다이브 보고서를 자동 작성·저장하는 전문 에이전트.
> 모델: glm-5.2:cloud (기존 `insight_model`). 1차 전체자동화 → 2차 HITL 하네싱.

## 0. 결정 요약 (사용자 확정)

| 항목 | 결정 |
|---|---|
| 실행 방식 | **worker 작업큐 + 상태폴링** (API가 job enqueue, worker가 수분간 실행) |
| 큐 구현 | **DB 폴링 큐** (DeepDiveJob 테이블을 worker가 주기 폴링 — Redis/Celery 미도입) |
| 저장 구조 | **단계별 구조화 JSON + LLM 서술 본문** (섹션별 렌더·재생성·필터 가능) |
| 1차 범위 | **국내주 풀 5단계** (DART·재무·주석·공시 데이터 확보됨) |
| 에이전트 성격 | **하이브리드**: 5단계 고정 파이프라인 골격 + 각 단계 내 자율 리서치(mini tool-loop) |

## 1. 핵심 아이디어: LLMPort 확장 없는 "단계 내 자율 리서치"

현 `LLMPort.chat(model, system, user, temperature)` 은 단발 텍스트 생성만 한다. 자율 리서치를
위해 tool-calling 루프가 필요하지만, **포트를 바꾸지 않고 서비스 계층에서 오케스트레이션**한다:

1. 각 단계는 도구 카탈로그(JSON 스키마)를 system 프롬프트에 넣고, LLM 에게
   "더 필요한 데이터가 있으면 `{"tool": "...", "args": {...}}` 로 요청하라"고 지시.
2. 서비스(`deepdive_agent`)가 응답을 파싱해 도구를 **코드로** 실행(DART·재무·피어 등 기존 어댑터).
3. 도구 결과를 다시 user 프롬프트에 주입해 `chat()` 재호출. 최대 N회(단계별 상한) 반복.
4. LLM 이 `{"done": true, "result": {...}}` 를 내면 그 단계 구조화 결과 확정.

이 "orchestrated tool-loop" 는 결정론적 골격(단계 순서·상한·폴백) 안에서 LLM 의 자율성을 허용해,
재현성·비용통제·디버깅을 유지하면서 리서치 유연성을 얻는다. glm-5.2 tool-calling 네이티브 지원
여부와 무관(프롬프트+JSON 파싱 기반)하게 동작.

## 2. 계층 배치 (import-linter 계약 준수)

```
routers/deepdive.py         (driving) — enqueue, 상태·결과 조회 (ORM 직접 접근 금지 → service 경유)
  └ services/deepdive/
      orchestrator.py        — 5단계 파이프라인 실행·상태 전이·저장
      agent.py               — mini tool-loop(chat + 도구 실행 반복), LLMPort 주입
      tools.py               — 도구 카탈로그: 기존 어댑터/서비스를 감싼 순수 호출부
      stages/                — 단계별 프롬프트·구조화 스키마·검증
        s1_overview.py  s2_redflags.py  s3_business.py  s4_thesis.py  s5_valuation.py
  └ domain/deepdive_rules.py — 순수 계산(레드플래그 룰·업사이드 판정 등, IO 없음)
  └ ports/llm.py             — 변경 없음(chat 재사용)
  └ adapters/                — dart/sec/market/persistence 재사용, 신규 없음(1차)
  └ db/models.py             — DeepDiveReport, DeepDiveJob (신규 테이블)
```

worker(`scheduler.py` 또는 별도 큐 루프)가 `services/deepdive/orchestrator` 를 호출한다.
domain 은 IO 를 모르고(레드플래그 임계 룰만), 서비스가 어댑터·LLM 을 조율한다.

## 3. 데이터 모델

### DeepDiveJob (작업큐·상태)
```
id, stock_code, status(pending|running|paused|done|failed),
current_stage(1~5), progress(0~100), model, requested_at, started_at,
finished_at, error, hitl_pending(bool, 2차), hitl_prompt(text, 2차)
```
- 상태폴링: 프론트가 `GET /api/deepdive/{code}/status` 로 progress·current_stage 조회.
- 재시도: failed job 을 pending 으로 되돌려 재개(단계 체크포인트부터).

### DeepDiveReport (결과)
```
id, stock_code, job_id, as_of(생성 시각), model,
overview_json, redflags_json, business_json, thesis_json, valuation_json,  # 단계별 구조화
narrative_md,        # 5단계 통합 서술 본문(사람이 읽는 최종 보고서)
verdict,             # 결론 요약(예: '성장주 · 업사이드 62%')
upside_pct,          # 목표가 업사이드(스크리너 정렬·필터용)
inputs_hash          # 재생성 판단(analysis_comment 패턴 재사용)
```
- 단계별 JSON 은 프론트가 섹션별 카드로 렌더, 개별 단계 재생성·비교에 사용.
- 최신 1건만 유지(재실행 시 갱신) — 이력 필요 시 as_of 로 다건 보관(2차).

## 4. 5단계 파이프라인 (각 단계 = 데이터수집 → mini tool-loop → 구조화 저장)

| 단계 | 산출 구조(예) | 주 데이터원(도구) | 자율 리서치 |
|---|---|---|---|
| 1 Overview | per/pbr/시총/주주구성/사업개요 | universe·financials·DART 최근 사업보고서 | 낮음(대부분 바인딩) |
| 2 Red Flags | 매출채권·재고·OCF 괴리, 무형자산 비중, 현금 시계열 | financials 시계열·DART 재무제표 주석 | 중(주석 조회 반복) |
| 3 Business | 밸류체인·벤더/납품처·경쟁사·아이템 비중 변화 | DART 과거 사업보고서·리포트·웹 보조 | **높음**(과거 공시 반복 조회) |
| 4 Thesis & Risks | 실적기반 아이디어·업종별 논리·하방리스크 | 1~3단계 결과·재무추세·대주주 이력 | 중 |
| 5 Valuation | 예상실적·멀티플·목표가·업사이드·진입조건 | 재무추세·피어 멀티플·4단계 아이디어 | 중 |

- 각 단계는 이전 단계 구조화 결과를 컨텍스트로 받음(누적).
- 단계별 도구 호출 상한(예 3단계 6회, 나머지 2~3회)으로 비용·시간 통제.
- 단계 실패 시 부분 결과 저장 + job.failed(재개 가능).

## 5. 도구 카탈로그 (tools.py — 기존 자산 래핑)

1차 도구(모두 기존 어댑터/서비스 재사용, 신규 수집 파이프라인 없음):
- `get_financials(code)` — 분기·연간 재무 시계열 (company_service.financials_rows)
- `get_financial_notes(code, rcept)` — DART 재무제표 주석 원문 (dart.fetch_document_text)
- `list_disclosures(code, kind, years)` — 과거 공시 목록 (dart.fetch_disclosures)
- `get_disclosure_text(rcept)` — 공시 원문 (dart.fetch_document_text)
- `get_ownership(code)` — 주주구성·대주주 변동 (dart.fetch_ownership_changes)
- `get_peers(code)` — 동종 밸류에이션 (company_service.peer_valuations)
- `get_price_context(code)` — 현재가·모멘텀·52주 (company_service)
- `web_search(query)` — 보조(경쟁사·산업 맥락). 2차 확장, 1차는 선택적.

각 도구는 순수 함수(입력 dict → 출력 dict)로 감싸 LLM 응답의 tool 요청을 안전히 디스패치.

## 6. HITL 하네싱 (2차 목표, 1차에서 자리만 확보)

- job.status 에 `paused` + `hitl_pending`/`hitl_prompt` 필드를 1차부터 스키마에 둔다.
- 2차: 특정 단계 후 `paused` 로 멈추고, 프론트가 사용자 입력(방향 조율·추가 인풋)을 받아
  `POST /api/deepdive/{code}/resume {stage, feedback}` 로 재개. feedback 은 다음 단계 프롬프트에 주입.
- 1차는 전 단계 자동 진행(paused 미사용). 스키마·상태기계만 HITL 을 수용하도록 설계.

## 6.5 DB 폴링 큐 (확정)

Redis/Celery 미도입 — `DeepDiveJob` 테이블 자체를 큐로 쓴다(딥다이브 빈도가 낮아 충분).

- **enqueue**: API 가 `POST /api/deepdive/{code}` → 같은 code 의 진행 중 job 없으면 `pending` row 삽입.
- **worker 루프**: docker worker 에 폴링 태스크 추가(APScheduler interval, 예 10초).
  가장 오래된 `pending` 1건을 `running` 으로 원자적 전이(`UPDATE ... WHERE status='pending'
  ... RETURNING`, 동시성 안전) 후 orchestrator 실행. 단일 worker 라 동시 1건(직렬) — 비용·부하 통제.
- **크래시 복구**: `running` 인데 `started_at` 이 임계(예 30분) 초과면 stale 로 보고 `failed` 처리
  후 재큐 가능. 단계 체크포인트(완료된 단계 JSON 저장)부터 재개.
- **상태폴링**: 프론트가 `GET /api/deepdive/{code}/status` 로 progress·current_stage 조회(2~3초 간격).

## 7. 구현 순서(1차)

1. DB 모델(DeepDiveJob·DeepDiveReport) + 마이그레이션.
2. `tools.py` 도구 카탈로그(기존 어댑터 래핑) + 단위테스트.
3. `agent.py` mini tool-loop(fake LLM 으로 루프·상한·파싱 테스트).
4. `stages/` 5단계 프롬프트·구조화 스키마 + `domain/deepdive_rules.py` 순수 룰.
5. `orchestrator.py` 파이프라인·상태전이·저장.
6. worker 큐 루프(enqueue→실행) + `routers/deepdive.py`(enqueue·status·result).
7. 프론트: 딥다이브 탭(입력·진행률·단계별 카드·통합 보고서).
8. 실종목 E2E 검수(국내주 3~5종목) → 배포.

## 8. 리스크·주의

- **비용**: 단계별 tool-loop 반복 × glm-5.2. 단계별 상한·토큰 예산 필수. 초기엔 상한 보수적으로.
- **DART 레이트리밋**: 기존 throttle 재사용. 3단계 과거공시 반복 조회가 최대 부하 → 캐시.
- **재현성**: LLM 자율 루프라 완전 결정론은 아님. inputs_hash 로 재생성 판단, 온도 낮게(0.2).
- **환각**: 밸류에이션·목표가는 수치 근거를 구조화 JSON 에 강제(숫자 필드 필수)해 서술과 분리.
