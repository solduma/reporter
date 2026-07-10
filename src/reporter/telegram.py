"""텔레그램 Bot API 발송 — 4096자 초과 시 개행 경계 기준 분할.

메시지 본문은 마크다운(`**굵게**`·`[텍스트](url)`)으로 조립되며, 발송 직전
`_to_telegram_html` 로 텔레그램 HTML 로 변환해 parse_mode=HTML 로 보낸다.
소스를 마크다운으로 유지해 웹(react-markdown)과 아카이브가 동일하게 렌더된다.
"""

from __future__ import annotations

import html
import logging
import re
import time

import requests

from .fallback import log_fallback

logger = logging.getLogger(__name__)

_MAX_LEN = 4096
# HTML 이스케이프·태그로 길이가 늘어나므로 분할은 4096보다 낮게 잡아 여유를 둔다.
_SPLIT_LEN = 3800
_SEND_INTERVAL = 1.1  # 단일 채팅 초당 1건 제한 회피

# [텍스트](https://url) 형태의 마크다운 링크. 이스케이프 후에도 대괄호·괄호는 보존된다.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
# **굵게** 또는 __굵게__. 비탐욕 매칭으로 가장 짧은 쌍만 잡는다.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)


class TelegramError(RuntimeError):
    pass


def _to_telegram_html(text: str) -> str:
    """마크다운 섞인 본문을 텔레그램 HTML 로 변환한다.

    먼저 `< > &` 만 이스케이프한 뒤(텔레그램 HTML 은 이 3개만 요구) 마크다운
    링크와 굵게를 태그로 바꾼다. 순수 URL 은 텔레그램이 자동 링크하므로 건드리지 않는다.
    """
    escaped = html.escape(text, quote=False)
    escaped = _MD_LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', escaped)
    escaped = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", escaped)
    return escaped


def _is_parse_error(err: TelegramError) -> bool:
    """HTML 파싱 실패인지 판별. 이 경우에만 서식 없이 재시도한다."""
    msg = str(err).lower()
    return "parse" in msg or "entit" in msg or "tag" in msg


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
        """단일 청크 발송. HTML 서식으로 보내되 파싱 실패 시 평문으로 폴백한다.

        발송된 message_id 를 반환한다.
        """
        payload: dict = {
            "chat_id": self._chat_id,
            "text": _to_telegram_html(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if disable_notification:
            payload["disable_notification"] = True
        try:
            result = self._api("sendMessage", payload)
        except TelegramError as e:
            if not _is_parse_error(e):
                raise
            # HTML 파싱 실패 시 브리핑 유실을 막기 위해 서식 없이 원문 그대로 재발송한다.
            log_fallback(
                "telegram.html_to_plain",
                reason=f"HTML parse_mode 발송 실패 → 평문 재발송 ({e})",
            )
            payload["text"] = text
            del payload["parse_mode"]
            result = self._api("sendMessage", payload)
        return int(result.get("message_id", 0))

    def send(
        self, text: str, thread_id: int | None = None, disable_notification: bool = False
    ) -> int:
        """긴 메시지는 자동 분할해 순차 발송한다. 발송한 청크 수를 반환한다.

        thread_id 를 주면 포럼 토픽 안으로 발송하고, disable_notification 으로 무음 발송한다.
        """
        chunks = _split(text, limit=_SPLIT_LEN)
        for i, chunk in enumerate(chunks):
            self._send_one(chunk, thread_id=thread_id, disable_notification=disable_notification)
            if i < len(chunks) - 1:
                time.sleep(_SEND_INTERVAL)
        return len(chunks)

    def send_message(
        self, text: str, thread_id: int | None = None, disable_notification: bool = False
    ) -> int:
        """단일(분할 없는) 메시지를 발송하고 message_id 를 반환한다. 헤더 등 짧은 메시지용."""
        return self._send_one(text[:_SPLIT_LEN], thread_id=thread_id, disable_notification=disable_notification)

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
