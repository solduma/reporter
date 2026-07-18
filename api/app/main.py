"""FastAPI 진입점."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters import dart
from app.adapters.realtime import manager as realtime_manager
from app.config import get_settings
from app.db.session import init_db
from app.routers import (
    admin,
    broadcasts,
    calendar,
    companies,
    deepdive,
    industries,
    market,
    portfolio,
    realtime,
    screener,
    today,
    us,
)
from app.services import fallback_store
from reporter import fallback

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # DART 키 링(primary→backup) 설정 — 020 한도초과 시 딥다이브 등 온디맨드 조회가 자동 폴오버.
    dart.configure_from_settings(get_settings())
    # 폴백 이벤트 DB 영속화 sink 등록(단일 writer=API). reporter.fallback 은 계층상 DB 를 몰라
    # 여기서 주입한다. 미등록 시엔 로그만 남는다.
    fallback.register_sink(fallback_store.db_sink)
    # KIS 실시간 시세 WebSocket 상시 연결(키 없으면 자동 비활성).
    realtime_manager.start()
    # 섹터·지수 flow 캐시 워밍 — 종목 analysis·스크리너 첫 요청이 cold ETF 봉 읽기(수백ms~수초)를
    # 물지 않도록 기동 직후 백그라운드 스레드로 데운다(startup 블로킹 방지). 실패는 무시(다음
    # 온디맨드 호출이 채움).
    threading.Thread(target=_warm_flow_cache, name="flow-warm", daemon=True).start()
    try:
        yield
    finally:
        await realtime_manager.stop()


def _warm_flow_cache() -> None:
    from app.services import sector_flow

    try:
        sector_flow.warm_cache()
    except Exception as e:  # 워밍 실패는 치명적 아님(온디맨드가 폴백)
        logging.getLogger(__name__).warning("flow cache warm failed: %s", e)


app = FastAPI(title="reporter web API", lifespan=lifespan)

# 개발 중 Next.js(localhost:43000) 에서 호출 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:43000", "http://127.0.0.1:43000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(today.router)
app.include_router(industries.router)
app.include_router(industries.trade_router)
app.include_router(companies.router)
app.include_router(screener.router)
app.include_router(market.router)
app.include_router(admin.router)
app.include_router(broadcasts.router)
app.include_router(portfolio.router)
app.include_router(realtime.router)
app.include_router(us.router)
app.include_router(calendar.router)
app.include_router(us.screener_router)
app.include_router(deepdive.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
