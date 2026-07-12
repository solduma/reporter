# 프로젝트 재구조화 설계 검토 (초안)

> 상태: **설계 검토용 초안** — 코드 변경 없음. 착수 여부·범위를 합의하기 위한 문서.
> 작성 근거: 2026-07-12 코드베이스 실측(의존 방향·결합도·배포 경로).

## 1. 논의 배경

"모노레포로 하고, 데이터 수집·웹·텔레그램 발송을 코어에서 분리하자"는 제안에 대한 검토.

## 2. 현재 상태 (실측)

이미 **단일 git 저장소 = 느슨한 모노레포**이며, 세 관심사가 배포단위로 대체로 분리돼 있다.

| 배포단위 | 위치 | 진입점 | 규모 | DB |
|---|---|---|---|---|
| 텔레그램 발송 (CLI) | `src/reporter` | `reporter.cli` (launchd 9종) | 2,534줄 / 23파일 | **무의존** |
| 웹 API + 수집 워커 + TUI | `api/app` | `app.main`·`app.scheduler`·`app.tui` | 11,682줄 / 99파일 | Postgres |
| 웹 프론트 | `web` | Next.js | 55파일 | — |

**핵심 사실:**
- **의존 방향 단방향**: `app → reporter`(12곳), `reporter → app` = 0. 순환 없음.
- **발송 CLI는 DB 무의존**: `src/reporter`에 SQLAlchemy/postgres 0건. 크롤→GLM→텔레그램 직행, 상태는 `logs/*.json` 스풀.
- **수집↔발송 직접결합 0**: `app/services`가 telegram/pipeline 호출 0건.
- **두 제품 데이터 공유 = 느슨**: 발송분을 `broadcasts.jsonl` 스풀로 흘리면 API가 DB로 흡수(단방향).
- **`app`이 빌려 쓰는 공유 커널 5모듈**(`reporter.ollama_client`·`fallback`·`pdf`·`crawler`·`models`)은 서로 의존이 없어(응집도 낮음) 추출이 쉽다.
- 내부 경계는 이미 강제됨: `api/.importlinter` 9계약(육각형), 루트 `.importlinter` 1계약(reporter 커널 리프).

## 3. 제안(3분리)이 현실과 어긋나는 지점

원 제안 "수집·웹·발송을 코어에서 분리"를 코드에 대보면:

1. **발송은 이미 분리됨.** `src/reporter`는 이미 독립 배포단위 + DB 무의존. 더 뺄 것이 없다.
2. **수집과 웹은 한 몸.** 둘 다 `api/app` 안에서 같은 services·DB·domain을 공유한다(웹이 조회하는 데이터를 워커가 채움; `company_service`가 조회+백필 겸함). 억지로 분리하면 services/models/domain을 양쪽이 **다시 공유**해야 해 "코어 공유하는 두 진입점"으로 회귀 — 지금 이미 그 구조(`app.main`/`app.scheduler`가 같은 `app` 코어 공유).
3. **진짜 개선 여지는 "3분리"가 아니라 "공유 커널 명시화".** `src/reporter` 커널과 `api/app/domain`이 별개인데 `app`이 reporter 5모듈을 빌려 쓴다. 재구조화의 실질 가치는 이 공유 커널을 하나로 뽑는 것.

## 4. 목표 구조 대안

### 대안 A — 현 구조 유지 (권장 기준선)
이미 모노레포·단방향·배포단위 분리·육각형 경계 확보. 재구조화 순이득이 불분명하면 유지가 정답.

### 대안 B — 공유 커널 명시화 (점진, 저위험) ← **채택**
```
reporter/
├─ src/reporter/          # 텔레그램 발송 CLI 제품 (현행 유지)
│   └─ (shared kernel: app 이 공유하는 12모듈 — 아래 §8 목록)
├─ api/app/
│   ├─ domain/ ports/ adapters/ services/ routers/   # 육각형(현행)
│   ├─ main.py       # 웹 진입점
│   └─ scheduler.py  # 수집 워커 진입점
└─ web/
```
- `src/reporter`의 공유 표면을 **문서·import-linter 계약**으로 "공유 커널"임을 명시(코드 이동 없음). app→reporter 의존은 이미 단방향이라 그대로 정당화.
- **비용 낮음**(계약·문서 중심, 코드 이동 0), **이득**: 공유 표면이 명시·고정돼 (1) 향후 변경 시 파급 예측, (2) app 이 CLI 전용 모듈(pipeline·telegram·forum 등)을 실수로 쓰면 CI 차단.
- 상세 단계는 §8.

### 대안 C — packages/ 모노레포 (전면, 고위험)
```
packages/
├─ core/         # 공유 커널(ollama·fallback·pdf·crawler·models·domain)
├─ collector/    # 수집 워커 (core 의존)
├─ web-api/      # 웹 API (core 의존)
└─ telegram/     # 발송 CLI (core 의존)
web/             # Next.js
```
- 가장 명확한 구조. 그러나 **수집·웹이 core를 재공유**해야 하고(2번 항목), `api/app` 11,682줄 + **배포 경로 의존 20곳**(Makefile·docker-compose·Dockerfile·launchd) 전면 수정. 되돌리기 매우 비쌈.
- 단일 개발자·단일 Postgres 맥락에서 "배포단위 물리 분리"의 실질 가치가 낮음(수집·웹이 같은 DB·도메인을 쓰므로 프로세스만 나뉘고 코어는 공유).

## 5. 비용·리스크

| 항목 | 대안 B | 대안 C |
|---|---|---|
| 코드 이동 | 최소(계약·문서 중심) | 대량(11.7k줄 재배치) |
| 배포 경로 수정(20곳) | 거의 없음 | 전면(Makefile·docker·launchd·Dockerfile) |
| 되돌리기 | 쉬움 | 매우 어려움 |
| import 경로 | 변화 적음 | 전면 변경 |
| 순이득 | 커널 경계 명시 | 물리 분리(가치 맥락 의존) |

## 6. 권고

- **수집·웹·발송 3분리는 권장하지 않음** — 발송은 이미 분리됐고, 수집·웹은 같은 코어를 공유해 물리 분리해도 코어 재공유로 회귀한다.
- 실질 개선이 필요하다면 **대안 B(공유 커널 명시화)**가 비용 대비 이득이 가장 낫다.
- 전면 재구조화(대안 C)는 현 규모·운영 맥락에서 **과잉 가능성**이 높다. DB 이관, 배포단위 독립 확장, 팀 분업 같은 **구체적 트리거가 생기면** 그때 재검토.

## 7. 미결 질문 (합의 필요)
- 재구조화의 **구체적 동기**는? (배포 독립성 / 테스트 격리 / 신규 진입점 추가 / 단순 정리 중 무엇)
- 동기가 "정리·명확성"이라면 대안 B로 충분한가?
- 물리 분리가 꼭 필요한 트리거(예: 수집 워커를 별 서버로)가 예정돼 있는가?

---

## 8. 대안 B 실행 계획 (채택)

**본질: 코드를 옮기는 게 아니라, `app`↔`reporter` 공유 표면을 계약으로 명시·고정한다.** app→reporter 단방향은 이미 성립하므로 새 위반만 막으면 된다.

### 8.1 공유 커널 표면 (실측, 12모듈)

`api/app`이 실제 import 하는 `reporter` 모듈 = 이것이 "공유 커널"의 정의다. 이 외 모듈은 CLI 전용.

| 성격 | 모듈 | 비고 |
|---|---|---|
| 순수 도메인(이미 리프 계약) | `models` · `market` · `sector_etf` | `grouping`·`selector`·`fallback`도 리프이나 fallback만 app 공유 |
| 도메인 유틸 | `fallback` | 폴백 이벤트 로깅(포트/어댑터 패턴 원형) |
| IO·외부서비스 | `ollama_client` · `crawler` · `pdf` · `news` · `judal` · `us_market` · `analyzer` · `article` | app 이 재사용하는 크롤·GLM·파싱 |

**CLI 전용(app 이 쓰면 안 되는) 모듈**: `cli` · `pipeline` · `afternoon` · `telegram` · `forum` · `shortener` · `archive` · `config` · `grouping` · `selector`.

### 8.2 단계

- **B-1. 공유 커널 계약 신설** (api `.importlinter`) ✅ 구현됨
  - **핵심 발견**: import-linter 는 external 패키지의 서브모듈(`reporter.pipeline`)을 forbidden 대상으로 못 쓴다. 해결 = `root_package = app` → **`root_packages = app, reporter`** 로 전환해 reporter 를 internal 화 → 서브모듈 forbidden 가능.
  - 계약 `app-no-cli-only`: `source_modules = app`, `forbidden_modules = reporter.{cli,pipeline,afternoon,telegram,forum,shortener,archive,config,grouping,selector}`. app 이 발송 파이프라인을 직접 부르는 신규 결합 차단.
  - **부수 이득**: P2 에서 external 한계로 포기했던 `llm-behind-port` 계약(`app.services/routers/domain/ports` → `reporter.ollama_client` 금지)도 이제 강제 가능 → 함께 추가. LLM 은 adapters/llm 뒤 LLMPort 로만.
  - 검증: 두 계약 각각 위반 주입 → BROKEN 확인(실효성 증명). 11 kept 복귀. 기존 9계약·271 tests 무영향.

- **B-2. 공유 커널 문서화** ✅ 구현됨
  - `docs/shared-kernel.md` 신설: 공유 12모듈 표(성격·app 사용처) + CLI 전용 목록 + 강제 방법.
  - reporter 메모리에 공유 표면·계약 기록.

- **B-3. (선택) 공유 커널 물리 그룹화** — *지금은 안 함, 트리거 시*
  - 12모듈을 `src/reporter/kernel/` 하위로 모으는 것은 코드 이동 + import 경로 변경이라 대안 B의 "저위험" 전제를 깬다. B-1·B-2로 경계가 명시되면 물리 이동 없이도 목적(파급 예측·오결합 차단) 달성. 물리 분리는 대안 C 트리거와 함께 재검토.

### 8.3 비용·검증
- 코드 이동 **0**. 배포 경로 수정 **0**. 순수 계약·문서 추가.
- 검증: `make lint`(import-linter) 그린 + 위반 주입 테스트 + 기존 271 tests 무영향.
- 되돌리기: 계약 1개 제거 = 즉시 원복.

### 8.4 착수 시 산출물
- api `.importlinter` +1 계약(app→CLI전용모듈 금지)
- `docs/shared-kernel.md`(공유 표면 목록·규약)
- 위반 주입으로 계약 실효 검증한 근거

> **다음 결정**: 이 §8 계획으로 실제 착수(B-1·B-2 구현)할지, 아니면 문서 합의 단계에서 멈출지.
