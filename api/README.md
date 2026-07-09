# reporter web

증권 리포트 크롤·AI 분석 결과를 웹으로 제공하는 서비스. 기존 `reporter` CLI 패키지
(`src/reporter`)의 크롤·PDF·GLM 로직을 재사용한다.

- `api/` — FastAPI 백엔드 (uv)
- `web/` — Next.js 14 프론트엔드 (pnpm)
- `infra/` — 격리된 docker-compose (postgres/redis/minio)

## 인프라 (격리)

기존에 로컬에서 도는 컨테이너(analytics-agent의 weaviate/redis/minio, standalone
postgres)와 **섞이지 않도록** 전용 compose project `reporter` + 전용 포트·볼륨을 쓴다.

| 서비스 | 호스트 포트 | 비고 |
|---|---|---|
| reporter-postgres | 5433 | 5432(standalone) 회피 |
| reporter-redis | 6380 | 6379(analytics) 회피 |
| reporter-minio | 9010 (API) / 9011 (Console) | 9000-9001(analytics) 회피 |

```bash
cd infra
cp .env.example .env      # 값 채우기 (로컬 dev 기본값 사용 가능)
docker compose up -d
docker compose ps         # 3개 healthy 확인
```

## 백엔드 (api)

```bash
cd api
cp .env.example .env      # 인프라 포트 + OLLAMA_API_KEY 등 채우기
uv sync
uv run uvicorn app.main:app --port 8010 --reload
```

- `POST /api/admin/ingest?date=YY.MM.DD` — 당일(또는 지정일) 종목·산업 리포트 크롤→PDF(MinIO)→GLM 요약·센티먼트→Postgres 적재 + 시황 브리핑 생성 + 브로드캐스트 스풀 흡수. 수동 백필용(정기 수집은 워커가 담당).
- `POST /api/admin/broadcasts/ingest` — CLI 텔레그램 발송 스풀(`logs/broadcasts.jsonl`)만 DB 로 적재.
- `GET /api/today/market` — 당일 시황 요약
- `GET /api/today/reports?category=company|industry` — 리포트 카드 목록
- `GET /api/reports/{id}/pdf` — PDF 원본 스트림
- `GET /api/broadcasts?industry=&stock=&kind=&from=&to=` — 텔레그램 브리핑 아카이브 목록(필터)
- `GET /api/broadcasts/{id}` — 브리핑 원문 + 근거 리포트/기사 링크

## 텔레그램 브리핑 아카이브

CLI(`src/reporter`)가 텔레그램으로만 발송하던 콘텐츠(투자·경제·채권 종합, 장중 뉴스,
미국증시, 오후 리서치 등)를 웹에서 열람하기 위한 브릿지다.

- **CLI**: 발송 직후 `logs/broadcasts.jsonl` 에 한 줄 append(stdlib 만, 오프라인 안전).
- **API**: 수집 사이클/관리자 트리거에서 스풀을 읽어 `broadcast` 테이블에 멱등 적재
  (`dedup_key` UNIQUE). Postgres 는 API 가 단일 writer 라는 불변식을 유지한다.
- **웹**: `/archive` 페이지(종류 필터·페이지네이션), 산업 흐름의 "관련 브리핑" 레일,
  기업 타임라인의 브리핑 이벤트. 종목코드/산업 태그(`stock_codes`/`industries`)로 조인한다.

> 아카이브는 배포 시점 이후 발송분부터 축적된다(과거 발송분은 소급 불가).

## 수집 스케줄러 (worker)

정기 수집은 별도 워커 프로세스가 담당한다(`docker compose up -d reporter-worker`).
평일(월~금) **09:00–19:00, 매 30분** ingest + 시황 갱신. 멱등 수집이라 매 실행마다
신규 리포트만 저장·분석한다(중복 저장·GLM 재호출 없음).

로컬 실행: `cd api && uv run reporter-worker`

> 기존 `launchd/`·`crontab.example` 은 **CLI 텔레그램 발송용**으로 별개다. 이 워커는
> 웹서비스 DB 적재만 하므로 둘은 목적이 다르고 함께 두어도 무방하다.

## Admin TUI

관리자용 터미널 대시보드(Textual). 서비스 계층을 직접 호출한다(HTTP 미경유).

```bash
cd api && uv run reporter-tui
```

- **상태 패널**: 테이블 행수(reports/universe/growth/…)·최신 스냅샷 날짜 (`r` 새로고침).
- **서버 제어**: API(:8010)·WEB(:3000) 시작/종료 버튼. TUI 가 subprocess 로 직접
  띄우고 그 PID 만 종료하므로 다른 프로젝트·외부 서버는 건드리지 않는다. TUI 종료 시
  자신이 띄운 서버는 자동 정리. (web 은 `pnpm build` 산출물 필요.)
- **수집 트리거 버튼**: 리포트 수집 / 유니버스 스냅샷 / 성장 배치 — 워커 스레드로 실행,
  진행 로그를 패널에 스트리밍. (텔레그램 미발송, DB 적재만.)
- **스몰캡 성장주 미리보기**: 정렬(`s`, 매출YoY/모멘텀/시총/등락률)·페이지 이동(`p`/`n`).

> 트리거는 실제 크롤·GLM·네이버 호출을 수행한다(라이브 자원 사용).

## 프론트엔드 (web)

```bash
cd web
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010
pnpm install
pnpm dev                            # http://localhost:3000
```

페이지:

- **Today's Brew** (`/`): 상단 당일 시황, 좌측 산업분석·우측 종목분석 카드
  (기업/산업명·제목·1줄 요약·BUY/SELL 뱃지·근거·작성일), 카드 클릭 시 PDF 뷰어.
- **산업 흐름** (`/industries`): 업종별 발간일별 센티먼트 시계열(점 클릭 → 원문),
  선택 산업 관련 텔레그램 브리핑 레일, 관세청 수출입 무역통계 차트(HS 품목별).
- **기업 분석** (`/companies/[code]`): 리포트+DART 공시+텔레그램 브리핑 병합 타임라인
  (항목별 주가 긍/부정+근거), 주가 봉차트(2주 30분 / 3M·1Y 일봉 / 3Y 월봉), 분기 재무 차트
  (매출·영업이익·당기순이익·EPS·PER·PBR), 동일업종비교 테이블.
- **브리핑 아카이브** (`/archive`): 텔레그램 발송 콘텐츠 전체 이력(종류 필터·페이지네이션),
  카드 클릭 시 원문 + 근거 링크 모달.

## 외부 데이터 소스 키

`api/.env` 에 설정. 없으면 해당 기능만 비활성(나머지는 정상 동작).

| 키 | 발급처 | 용도 |
|---|---|---|
| `OLLAMA_API_KEY` | ollama.com | 요약·센티먼트 GLM (필수) |
| `DART_API_KEY` | opendart.fss.or.kr | 기업 분석 DART 공시 타임라인 |
| `CUSTOMS_API_KEY` | data.go.kr 관세청 품목별국가별 수출입실적(15100475) | 산업 흐름 무역통계 |
