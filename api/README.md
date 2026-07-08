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

- `POST /api/admin/ingest?date=YY.MM.DD` — 당일(또는 지정일) 종목·산업 리포트 크롤→PDF(MinIO)→GLM 요약·센티먼트→Postgres 적재 + 시황 브리핑 생성. (2단계에서 스케줄러가 대체)
- `GET /api/today/market` — 당일 시황 요약
- `GET /api/today/reports?category=company|industry` — 리포트 카드 목록
- `GET /api/reports/{id}/pdf` — PDF 원본 스트림

## 프론트엔드 (web)

```bash
cd web
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010
pnpm install
pnpm dev                            # http://localhost:3000
```

**Today's Brew** (`/`): 상단 당일 시황, 좌측 산업분석·우측 종목분석 카드
(기업/산업명·제목·1줄 요약·BUY/SELL 뱃지·근거·작성일), 카드 클릭 시 PDF 뷰어.

산업 흐름(`/industries`)·기업 분석(`/companies`)은 후속 단계에서 구현.
