"""포럼 토픽 관리 — 일자별 종목/산업 리포트·장중뉴스 토픽에 메시지를 누적한다.

목적: 개별 리포트/뉴스가 다른 메시지에 묻히지 않도록 일자별 토픽 하나로 모은다.
- 토픽 내 개별 메시지는 무음(disable_notification) → 알림 폭탄 방지.
- 토픽 생성 시 헤더 메시지(알림 ON)로 "리포트 생성"을 통지.
- 일 중 추가되면 헤더를 재전송(삭제 후 하단에 알림 ON 재게시)해 "마지막 업데이트 HH:MM · N건"
  을 갱신하며 알림을 낸다(editMessageText 는 무음이라 재전송으로 알림을 낸다).

CLI 는 상태가 없으므로 (kind, date) → {thread_id, header_id, count} 를 JSON 파일에 보존한다.
포럼이 아니거나 권한이 없으면 예외를 상위에서 잡아 plain 발송으로 폴백한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

from .config import Config
from .telegram import TelegramSender

logger = logging.getLogger(__name__)

_STATE_NAME = "forum_topics.json"

# 토픽 종류 → 헤더 라벨(이모지). 일자별로 "라벨 MM.DD" 토픽을 만든다.
TOPIC_LABEL = {
    "company": "📈 종목 리포트",
    "industry": "🏭 산업 리포트",
    "market_news": "📰 장중 뉴스",
}


@dataclass
class TopicState:
    thread_id: int
    header_id: int
    count: int


class ForumPublisher:
    """일자별 토픽에 메시지를 누적하고 헤더를 갱신한다. 상태를 파일로 보존한다."""

    def __init__(self, config: Config, sender: TelegramSender):
        self._sender = sender
        self._path = config.logs_dir / _STATE_NAME
        self._state = self._load()

    def _load(self) -> dict[str, TopicState]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):  # 배열 등 dict 가 아닌 JSON 방어
                raise TypeError("state root is not an object")
            return {k: TopicState(**v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
            logger.warning("forum 상태 파일 손상 — 새로 시작")
            return {}

    def _save(self) -> None:
        data = {k: asdict(v) for k, v in self._state.items()}
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _key(self, kind: str, date_key: str) -> str:
        # date_key 는 연도 포함(YYYY.MM.DD): 이듬해 같은 MM.DD 가 작년 토픽을 재사용하지 않도록.
        return f"{kind}|{date_key}"

    def _header_text(self, kind: str, day: str, count: int, updated: str) -> str:
        label = TOPIC_LABEL.get(kind, kind)
        return f"**{label} — {day}**\n(총 {count}건 · 마지막 업데이트 {updated})"

    def publish(self, kind: str, entries: list[str], day: str | None = None) -> int:
        """kind 토픽에 여러 메시지를 무음으로 누적하고 헤더를 갱신(알림)한다.

        entries: 토픽 안에 넣을 메시지 본문 리스트. 발송한 메시지 수를 반환한다.
        day: 표시용 날짜(기본 오늘 MM.DD). 상태 키는 연도 포함(YYYY.MM.DD)으로 별도 산출.
        """
        if not entries:
            return 0
        now = datetime.now().astimezone()
        day = day or now.strftime("%m.%d")
        date_key = now.strftime("%Y.") + day  # 연도 + 표시일 → 연 단위 충돌 방지
        updated = now.strftime("%H:%M")
        key = self._key(kind, date_key)
        state = self._state.get(key)

        if state is None:
            # 새 토픽 생성(헤더는 본문 누적 후 하단에 한 번만 게시).
            label = TOPIC_LABEL.get(kind, kind)
            thread_id = self._sender.create_forum_topic(f"{label} {day}")
            state = TopicState(thread_id=thread_id, header_id=0, count=0)
            # 생성 직후 즉시 보존: 이후 발송 중 죽어도 다음 실행이 같은 토픽을 재사용(중복 생성 방지).
            self._state[key] = state
            self._save()
        elif state.header_id:
            # 기존 헤더를 지워 하단에 재게시할 자리를 낸다(editMessageText 는 무음이라 재전송).
            self._sender.delete_message(state.header_id)

        # 개별 메시지는 무음으로 누적
        for body in entries:
            self._sender.send(body, thread_id=state.thread_id, disable_notification=True)
        state.count += len(entries)
        self._save()  # 본문 반영을 먼저 보존(헤더 발송 실패해도 count 유실 방지)

        # 헤더를 최신 상태로 하단에 게시(알림 ON) → 갱신을 알리고 최신 요약이 맨 아래에 뜬다.
        state.header_id = self._sender.send_message(
            self._header_text(kind, day, state.count, updated), thread_id=state.thread_id
        )
        self._save()
        return len(entries)
