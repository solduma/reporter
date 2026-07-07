"""텔레그램 Bot API 발송 — 4096자 초과 시 개행 경계 기준 분할."""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

_MAX_LEN = 4096
_SEND_INTERVAL = 1.1  # 단일 채팅 초당 1건 제한 회피


class TelegramError(RuntimeError):
    pass


def resolve_chat_ids(bot_token: str) -> list[tuple[int, str]]:
    """getUpdates 로 봇에게 말을 건 채팅들의 (chat_id, 표시이름) 목록을 조회한다."""
    resp = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getUpdates", timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    found: dict[int, str] = {}
    for update in data.get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            found[chat["id"]] = (
                chat.get("title") or chat.get("username") or chat.get("first_name") or ""
            )
    return list(found.items())


def _split(text: str, limit: int = _MAX_LEN) -> list[str]:
    """서식 손상을 피하기 위해 개행 경계에서 우선 분할한다."""
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        # 한 줄 자체가 limit 을 넘으면 강제로 잘라 넣는다.
        while len(line) > limit:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{buf}\n{line}" if buf else line
        if len(candidate) > limit:
            chunks.append(buf)
            buf = line
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


class TelegramSender:
    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise TelegramError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 설정되지 않았습니다.")
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._chat_id = chat_id
        self._session = requests.Session()

    def _send_one(self, text: str) -> None:
        resp = self._session.post(
            f"{self._base}/sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):  # 텔레그램은 200 에도 ok=false 를 반환할 수 있다
            raise TelegramError(data.get("description", "sendMessage 실패"))

    def send(self, text: str) -> int:
        """긴 메시지는 자동 분할해 순차 발송한다. 발송한 청크 수를 반환한다."""
        chunks = _split(text)
        for i, chunk in enumerate(chunks):
            self._send_one(chunk)
            if i < len(chunks) - 1:
                time.sleep(_SEND_INTERVAL)
        return len(chunks)
