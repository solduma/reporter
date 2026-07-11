"""KIS WebSocket 실시간 체결가(H0STCNT0) — API 프로세스 내 단일 연결 + 종목별 팬아웃.

API 가 KIS 키를 쥐고 uvicorn 단일 워커로 돌기에, 상시 WebSocket 연결을 이 프로세스 안의
asyncio 태스크로 소유한다(별도 워커·Redis 브리지 불필요). 조회 중인 종목만 구독하고
(동시구독 ~41 한도), 들어온 틱을 SSE 리스너 큐로 뿌린다.

메시지 종류
- 제어(JSON): 구독 성공/실패, PINGPONG(그대로 되돌려줘야 연결 유지)
- 데이터(파이프): ``0|H0STCNT0|<건수>|<^구분 필드들>`` — H0STCNT0 는 encrypt=N(평문)이라 복호화 없음.
  한 프레임에 여러 체결이 묶여 올 수 있어 마지막(최신) 레코드만 쓴다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass

import requests
import websockets

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_APPROVAL_URL = "https://openapi.koreainvestment.com:9443/oauth2/Approval"
# 실전 도메인. 모든 체결가 구독(종목만 다름)이 이 한 연결·경로를 공유한다.
_WS_URL = "ws://ops.koreainvestment.com:21000/tryitout/H0STCNT0"
_TR_ID = "H0STCNT0"
# KIS 개인계좌 실시간 등록 한도(안전 여유로 40). 초과분은 구독 거부 → 프론트가 폴링으로 폴백.
_MAX_SUBSCRIPTIONS = 40
_RECONNECT_DELAY_S = 5.0
# H0STCNT0 체결 레코드 필드 인덱스(^로 분할했을 때).
_F_CODE = 0
_F_TIME = 1
_F_PRICE = 2
_F_SIGN = 3
_F_CHANGE = 4
_F_RATIO = 5
_F_VOLUME = 13
_MIN_FIELDS = 14  # 위 인덱스를 모두 쓰려면 최소 이만큼 필요


@dataclass
class Tick:
    code: str
    price: int
    rising: bool | None  # 상승 True / 하락 False / 보합·불명 None
    change: float
    change_ratio: float
    volume: int
    ts: str  # 체결 시각 HHMMSS


def _sign_to_rising(sign: str) -> bool | None:
    """KIS 전일대비부호(1상한·2상승·3보합·4하한·5하락) → 방향. 네이버 코드와 다르니 주의."""
    if sign in ("1", "2"):
        return True
    if sign in ("4", "5"):
        return False
    return None


def is_data_frame(raw: str) -> bool:
    """실시간 데이터 프레임(파이프 구분, 암호화 플래그로 시작)인지."""
    return bool(raw) and raw[0] in ("0", "1") and "|" in raw


def parse_ticks(raw: str) -> list[Tick]:
    """H0STCNT0 데이터 프레임 → 체결 틱(최신 1건). 형식 불일치·다른 TR 이면 빈 리스트.

    프레임: ``<암호화>|<tr_id>|<건수>|<필드들>``. 건수>1 이면 레코드가 연달아 붙으므로
    레코드당 필드 수를 (전체/건수)로 유도해 마지막 레코드만 취한다.
    """
    parts = raw.split("|")
    if len(parts) < 4 or parts[1] != _TR_ID:
        return []
    try:
        count = int(parts[2])
    except ValueError:
        return []
    fields = parts[3].split("^")
    if count < 1 or len(fields) < _MIN_FIELDS:
        return []
    per = len(fields) // count
    if per < _MIN_FIELDS:
        return []
    rec = fields[(count - 1) * per : count * per]  # 최신 레코드
    try:
        return [
            Tick(
                code=rec[_F_CODE],
                price=int(rec[_F_PRICE]),
                rising=_sign_to_rising(rec[_F_SIGN]),
                change=float(rec[_F_CHANGE]),
                change_ratio=float(rec[_F_RATIO]),
                volume=int(rec[_F_VOLUME]),
                ts=rec[_F_TIME],
            )
        ]
    except (ValueError, IndexError):
        return []


class RealtimeManager:
    """단일 KIS WebSocket 연결을 소유하며 종목 구독·틱 팬아웃을 관리한다.

    구독은 refcount — 같은 종목을 여러 SSE 클라이언트가 봐도 KIS 등록은 1건이다.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._approval_key: str | None = None
        self._ws: websockets.ClientConnection | None = None
        self._send_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._reconcile_task: asyncio.Task | None = None
        self._reconcile_event = asyncio.Event()
        self._stopping = False
        # 구독 희망 종목 refcount(SSE 연결 수) + 현재 연결에 실제로 보낸 종목.
        self._desired: dict[str, int] = {}
        self._subscribed: set[str] = set()
        # 종목별 리스너 큐(SSE 스트림마다 하나).
        self._listeners: dict[str, set[asyncio.Queue]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._settings.kis_app_key and self._settings.kis_app_secret)

    # ---- 생명주기 ----
    def start(self) -> None:
        if not self.enabled:
            logger.info("realtime disabled — KIS 키 없음")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="kis-realtime")
        self._reconcile_task = asyncio.create_task(self._reconcile_loop(), name="kis-reconcile")

    async def stop(self) -> None:
        self._stopping = True
        self._reconcile_event.set()  # 리컨사일 루프 깨워 종료
        if self._ws is not None:
            await self._ws.close()
        for task in (self._task, self._reconcile_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # ---- 구독 API(SSE 핸들러가 호출) ----
    # acquire/release 는 **동기**다. SSE 요청이 await 지점에서 취소돼도 refcount 가 새지 않도록
    # 상태 변경과 네트워크 전송을 분리한다 — 실제 KIS 구독/해지는 _reconcile 이 멱등하게 맞춘다.
    def acquire(self, code: str) -> bool:
        """종목 구독 refcount 를 늘린다. 한도 초과·비활성이면 False(프론트는 폴링 폴백)."""
        if not self.enabled:
            return False
        if code in self._desired:
            self._desired[code] += 1
            return True
        if len(self._desired) >= _MAX_SUBSCRIPTIONS:
            logger.warning("realtime 구독 한도(%d) 초과 — %s 거부", _MAX_SUBSCRIPTIONS, code)
            return False
        self._desired[code] = 1
        self._reconcile_event.set()
        return True

    def release(self, code: str) -> None:
        """종목 구독 refcount 를 줄인다. 0 이 되면 리컨사일이 KIS 구독을 해지한다."""
        n = self._desired.get(code)
        if n is None:
            return
        if n > 1:
            self._desired[code] = n - 1
            return
        del self._desired[code]
        self._reconcile_event.set()

    def add_listener(self, code: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._listeners.setdefault(code, set()).add(q)
        return q

    def remove_listener(self, code: str, q: asyncio.Queue) -> None:
        qs = self._listeners.get(code)
        if qs is not None:
            qs.discard(q)
            if not qs:
                del self._listeners[code]

    # ---- 내부 ----
    async def _run(self) -> None:
        while not self._stopping:
            if not await self._ensure_approval():
                await asyncio.sleep(_RECONNECT_DELAY_S)
                continue
            try:
                async with websockets.connect(_WS_URL, ping_interval=None) as ws:
                    self._ws = ws
                    self._subscribed.clear()
                    await self._reconcile()  # 재연결 시 희망 종목 재구독
                    async for raw in ws:
                        await self._handle(raw if isinstance(raw, str) else raw.decode())
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 연결 끊김·프로토콜 오류 — 재연결
                logger.warning("realtime WS 오류, 재연결: %s", e)
                # 인증키 만료가 원인일 수 있으니 재발급하도록 폐기(24h 후 조용히 죽는 것 방지).
                self._approval_key = None
            finally:
                self._ws = None
            if not self._stopping:
                await asyncio.sleep(_RECONNECT_DELAY_S)

    async def _reconcile_loop(self) -> None:
        """희망 구독(_desired)과 실제 구독(_subscribed)의 차이를 멱등하게 맞춘다.

        acquire/release 는 동기라 event 만 세팅하고, 실제 KIS 전송은 여기서 한다 — 취소된
        SSE 요청이 refcount 를 새게 두지 않으면서 구독/해지 프레임은 확실히 나가게 한다.
        """
        while not self._stopping:
            await self._reconcile_event.wait()
            self._reconcile_event.clear()
            if self._stopping:
                return
            await self._reconcile()

    async def _reconcile(self) -> None:
        if self._ws is None:  # 미연결 — 다음 연결 시 _run 이 재구독
            return
        desired = set(self._desired)
        for code in desired - self._subscribed:
            await self._send(code, subscribe=True)
        for code in self._subscribed - desired:
            await self._send(code, subscribe=False)

    async def _ensure_approval(self) -> bool:
        """approval_key 를 발급/재사용한다(24h 유효). 실패 시 False."""
        if self._approval_key:
            return True
        s = self._settings

        def _issue() -> str | None:
            try:
                resp = requests.post(
                    _APPROVAL_URL,
                    json={
                        "grant_type": "client_credentials",
                        "appkey": s.kis_app_key,
                        "secretkey": s.kis_app_secret,
                    },
                    headers={"content-type": "application/json"},
                    timeout=15,
                )
                resp.raise_for_status()
                return resp.json().get("approval_key")
            except (requests.RequestException, ValueError) as e:
                logger.warning("approval_key 발급 실패: %s", e)
                return None

        self._approval_key = await asyncio.to_thread(_issue)
        return self._approval_key is not None

    def _frame(self, code: str, subscribe: bool) -> str:
        return json.dumps(
            {
                "header": {
                    "approval_key": self._approval_key,
                    "custtype": "P",
                    "tr_type": "1" if subscribe else "2",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": _TR_ID, "tr_key": code}},
            }
        )

    async def _send(self, code: str, subscribe: bool) -> None:
        """KIS 로 구독/해지 프레임을 보낸다. _subscribed 는 의도대로 갱신(전송 실패해도 다음
        리컨사일/재연결이 재시도) — 실제 구독 상태와 어긋난 채 굳지 않게 한다."""
        ws = self._ws
        if ws is None:
            return
        if subscribe:
            self._subscribed.add(code)
        else:
            self._subscribed.discard(code)
        try:
            await self._send_raw(ws, self._frame(code, subscribe))
        except Exception as e:
            logger.warning("realtime %s 전송 실패 %s: %s", "구독" if subscribe else "해지", code, e)
            # 다음 리컨사일에서 다시 시도하도록 어긋난 상태 되돌림.
            if subscribe:
                self._subscribed.discard(code)
            else:
                self._subscribed.add(code)

    async def _send_raw(self, ws: websockets.ClientConnection, frame: str) -> None:
        """단일 연결에 대한 모든 쓰기를 직렬화한다(구독·PINGPONG 응답 공용)."""
        async with self._send_lock:
            await ws.send(frame)

    async def _handle(self, raw: str) -> None:
        if is_data_frame(raw):
            for tick in parse_ticks(raw):
                self._fanout(tick)
            return
        # 제어 메시지(JSON): PINGPONG 은 그대로 돌려줘야 연결이 유지된다.
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if msg.get("header", {}).get("tr_id") == "PINGPONG":
            ws = self._ws
            if ws is not None:
                with contextlib.suppress(Exception):
                    await self._send_raw(ws, raw)
            return
        body = msg.get("body") or {}
        if body.get("rt_cd") not in (None, "0"):
            logger.warning("realtime 구독 응답 오류: %s", body.get("msg1"))

    def _fanout(self, tick: Tick) -> None:
        payload = {
            "code": tick.code,
            "price": tick.price,
            "rising": tick.rising,
            "change": tick.change,
            "change_ratio": tick.change_ratio,
            "volume": tick.volume,
            "ts": tick.ts,
        }
        for q in list(self._listeners.get(tick.code, ())):
            # 느린 클라이언트 — 큐가 차면 최신 틱만 중요하므로 그냥 드롭.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(payload)


# 프로세스 싱글턴(lifespan 에서 start/stop, SSE 라우터에서 구독).
manager = RealtimeManager()
