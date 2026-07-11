"""실시간 시세 SSE — 조회 중인 종목의 KIS 체결가를 브라우저로 push.

프론트가 ``EventSource('/api/realtime/quote?code=005930')`` 로 연결하면, 그 종목을 KIS
WebSocket 에 구독(refcount)하고 들어오는 틱을 SSE 이벤트로 흘려보낸다. 연결이 끊기면
구독을 줄인다(마지막 리스너면 KIS 구독 해지). 구독 한도 초과·비활성 시 즉시 종료해
프론트가 폴링으로 폴백하게 한다.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.services.realtime import manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/realtime", tags=["realtime"])

# 틱이 뜸한 동안 프록시·브라우저가 유휴 연결을 끊지 않도록 보내는 주석 하트비트 주기(초).
_HEARTBEAT_S = 15.0


async def _event_stream(request: Request, code: str):
    # acquire 는 동기 — 이후 어떤 await 에서 요청이 취소돼도 finally 가 refcount 를 되돌린다.
    if not manager.acquire(code):
        # 구독 불가(한도·비활성) — 프론트가 폴링으로 폴백하도록 신호 이벤트 후 종료.
        yield "event: unavailable\ndata: {}\n\n"
        return
    q = manager.add_listener(code)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_S)
            except TimeoutError:
                # 하트비트 겸 연결 확인 — 틱이 뜸해도 15s 내 끊긴 연결을 정리한다.
                if await request.is_disconnected():
                    break
                yield ": keepalive\n\n"  # SSE 주석 라인 — 이벤트 아님, 연결만 유지
                continue
            yield f"event: tick\ndata: {json.dumps(payload)}\n\n"
    finally:
        manager.remove_listener(code, q)
        manager.release(code)


@router.get("/quote")
async def realtime_quote(request: Request, code: str = Query(..., pattern=r"^\d{6}$")):
    """종목 실시간 체결가 SSE 스트림. code=국내 6자리."""
    return StreamingResponse(
        _event_stream(request, code),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
