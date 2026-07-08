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

- `POST /api/admin/ingest?date=YY.MM.DD` — 당일(또는 지정일) 종목·산업 리포트 크롤→PDF(MinIO)→GLM 요약·센티먼트→Postgres 적재 + 시황 브리핑 생성. 수동 백필용(정기 수집은 워커가 담당).
- `GET /api/today/market` — 당일 시황 요약
- `GET /api/today/reports?category=company|industry` — 리포트 카드 목록
- `GET /api/reports/{id}/pdf` — PDF 원본 스트림

## 수집 스케줄러 (worker)

정기 수집은 별도 워커 프로세스가 담당한다(`docker compose up -d reporter-worker`).
평일(월~금) **09:00–19:00, 매 30분** ingest + 시황 갱신. 멱등 수집이라 매 실행마다
신규 리포트만 저장·분석한다(중복 저장·GLM 재호출 없음).

로컬 실행: `cd api && uv run reporter-worker`

> 기존 `launchd/`·`crontab.example` 은 **CLI 텔레그램 발송용**으로 별개다. 이 워커는
> 웹서비스 DB 적재만 하므로 둘은 목적이 다르고 함께 두어도 무방하다.

## 프론트엔드 (web)

```bash
cd web
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010
pnpm install
pnpm dev                            # http://localhost:3000
```

세 페이지 모두 구현 완료:

- **Today's Brew** (`/`): 상단 당일 시황, 좌측 산업분석·우측 종목분석 카드
  (기업/산업명·제목·1줄 요약·BUY/SELL 뱃지·근거·작성일), 카드 클릭 시 PDF 뷰어.
- **산업 흐름** (`/industries`): 업종별 발간일별 센티먼트 시계열(점 클릭 → 원문),
  관세청 수출입 무역통계 차트(HS 품목별 수출·수입·무역수지).
- **기업 분석** (`/companies/[code]`): 리포트+DART 공시 병합 타임라인(항목별 주가
  긍/부정+근거), 주가 봉차트(2주 30분 / 3M·1Y 일봉 / 3Y 월봉), 분기 재무 차트
  (매출·영업이익·당기순이익·EPS·PER·PBR), 동일업종비교 테이블.

## 외부 데이터 소스 키

`api/.env` 에 설정. 없으면 해당 기능만 비활성(나머지는 정상 동작).

| 키 | 발급처 | 용도 |
|---|---|---|
| `OLLAMA_API_KEY` | ollama.com | 요약·센티먼트 GLM (필수) |
| `DART_API_KEY` | opendart.fss.or.kr | 기업 분석 DART 공시 타임라인 |
| `CUSTOMS_API_KEY` | data.go.kr 관세청 품목별국가별 수출입실적(15100475) | 산업 흐름 무역통계 |
