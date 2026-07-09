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

    def _api(self, method: str, payload: dict) -> dict:
        """Bot API 호출. 모든 실패(전송 오류·HTTP 4xx/5xx·ok=false)를 TelegramError 로 통일한다.

        텔레그램은 포럼 아님/권한 없음 등을 HTTP 4xx 로 알린다. raise_for_status 로 두면
        requests.HTTPError 가 새어 폴백(except TelegramError)을 우회하므로, 여기서 흡수한다.
        """
        try:
            resp = self._session.post(f"{self._base}/{method}", json=payload, timeout=15)
        except requests.RequestException as e:
            raise TelegramError(f"{method} 요청 실패: {e}") from e
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not resp.ok or not data.get("ok"):
            raise TelegramError(
                data.get("description") or f"{method} 실패 (HTTP {resp.status_code})"
            )
        return data.get("result") or {}

    def _send_one(
        self, text: str, thread_id: int | None = None, disable_notification: bool = False
    ) -> int:
        """단일 청크 발송. 발송된 message_id 를 반환한다."""
        payload: dict = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if disable_notification:
            payload["disable_notification"] = True
        result = self._api("sendMessage", payload)
        return int(result.get("message_id", 0))

    def send(
        self, text: str, thread_id: int | None = None, disable_notification: bool = False
    ) -> int:
        """긴 메시지는 자동 분할해 순차 발송한다. 발송한 청크 수를 반환한다.

        thread_id 를 주면 포럼 토픽 안으로 발송하고, disable_notification 으로 무음 발송한다.
        """
        chunks = _split(text)
        for i, chunk in enumerate(chunks):
            self._send_one(chunk, thread_id=thread_id, disable_notification=disable_notification)
            if i < len(chunks) - 1:
                time.sleep(_SEND_INTERVAL)
        return len(chunks)

    def send_message(
        self, text: str, thread_id: int | None = None, disable_notification: bool = False
    ) -> int:
        """단일(분할 없는) 메시지를 발송하고 message_id 를 반환한다. 헤더 등 짧은 메시지용."""
        return self._send_one(text[:_MAX_LEN], thread_id=thread_id, disable_notification=disable_notification)

    def create_forum_topic(self, name: str) -> int:
        """포럼 토픽을 생성하고 message_thread_id 를 반환한다. 포럼 슈퍼그룹에서만 동작."""
        result = self._api("createForumTopic", {"chat_id": self._chat_id, "name": name[:128]})
        return int(result["message_thread_id"])

    def delete_message(self, message_id: int) -> None:
        """메시지를 삭제한다. 이미 없거나 권한 없으면 조용히 무시(best-effort)."""
        try:
            self._api("deleteMessage", {"chat_id": self._chat_id, "message_id": message_id})
        except (TelegramError, requests.RequestException) as e:
            logger.info("deleteMessage 무시 (id=%s): %s", message_id, e)
