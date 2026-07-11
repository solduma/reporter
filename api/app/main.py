"""FastAPI 진입점."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.session import init_db
from app.routers import (
    admin,
    broadcasts,
    companies,
    industries,
    market,
    realtime,
    screener,
    today,
)
from app.services import fallback_store
from app.services.realtime import manager as realtime_manager
from reporter import fallback

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 폴백 이벤트 DB 영속화 sink 등록(단일 writer=API). reporter.fallback 은 계층상 DB 를 몰라
    # 여기서 주입한다. 미등록 시엔 로그만 남는다.
    fallback.register_sink(fallback_store.db_sink)
    # KIS 실시간 시세 WebSocket 상시 연결(키 없으면 자동 비활성).
    realtime_manager.start()
    try:
        yield
    finally:
        await realtime_manager.stop()


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
app.include_router(realtime.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
