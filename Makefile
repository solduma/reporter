# reporter — 루트에서 자주 쓰는 작업 모음.
# CLI(src/reporter, uv)·api(FastAPI, uv)·web(Next.js, pnpm) 모노레포.

.DEFAULT_GOAL := help

.PHONY: help tui api web worker install test test-cli test-api lint fmt hooks

help: ## 사용 가능한 타깃 목록
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

tui: ## Admin TUI 실행 (api 서비스 계층 직접 호출)
	cd api && uv run reporter-tui

api: ## API 서버 실행 (:8010, reload)
	cd api && uv run uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload

worker: ## 수집 스케줄러(워커) 실행
	cd api && uv run reporter-worker

web: ## 웹 개발 서버 실행 (:43000)
	cd web && pnpm dev -p 43000

install: ## 의존성 설치 (cli/api uv + web pnpm)
	uv sync
	cd api && uv sync
	cd web && pnpm install

test: test-cli test-api ## 전체 파이썬 테스트

test-cli: ## CLI(src/reporter) 테스트
	uv run pytest

test-api: ## API 테스트
	cd api && uv run pytest

lint: ## 린트 (ruff: cli+api, import-linter: api 계층, eslint+tsc: web)
	uv run ruff check src tests
	cd api && uv run ruff check app tests && uv run lint-imports
	cd web && pnpm lint && pnpm exec tsc --noEmit

fmt: ## 포매팅 (ruff format: cli+api)
	uv run ruff format src tests
	cd api && uv run ruff format app tests

hooks: ## pre-commit 훅 활성화 (.githooks)
	git config core.hooksPath .githooks
	@echo "core.hooksPath = .githooks 설정 완료"
