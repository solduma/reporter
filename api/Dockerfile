# reporter-api / reporter-worker 공용 이미지.
# 빌드 컨텍스트는 저장소 루트여야 한다(api 가 루트 reporter 패키지를 editable 로 참조).
FROM python:3.11-slim

# uv 설치 (프로젝트 표준 패키지 매니저)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 루트 reporter 패키지(크롤·PDF·GLM 로직) + api 소스
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY api ./api

WORKDIR /app/api
# api/.env 는 이미지에 넣지 않는다(런타임 env 로 주입). 의존성만 설치.
RUN uv sync --no-dev

# 기본은 웹 API. worker 는 compose 에서 command 로 override.
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
