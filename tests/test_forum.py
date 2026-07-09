"""포럼 토픽 발행 단위 테스트 — 토픽 생성·누적·헤더 재전송·무음·상태 보존."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from reporter.config import Config
from reporter.forum import ForumPublisher


def _config(tmp_path: Path) -> Config:
    return Config(
        ollama_host="x",
        ollama_api_key="",
        summary_model="s",
        insight_model="i",
        telegram_bot_token="token",
        telegram_chat_id="123",
        use_topics=True,
        root=tmp_path,
    )


class _FakeSender:
    """TelegramSender 인터페이스 흉내 — 호출을 기록하고 message_id 를 순차 발급한다."""

    def __init__(self):
        self.created: list[str] = []
        self.sends: list[dict] = []  # {text, thread_id, silent}
        self.deleted: list[int] = []
        self._next_thread = 100
        self._next_msg = 1000

    def create_forum_topic(self, name: str) -> int:
        self.created.append(name)
        self._next_thread += 1
        return self._next_thread

    def send_message(self, text, thread_id=None, disable_notification=False) -> int:
        self._next_msg += 1
        self.sends.append({"text": text, "thread_id": thread_id, "silent": disable_notification, "id": self._next_msg})
        return self._next_msg

    def send(self, text, thread_id=None, disable_notification=False) -> int:
        self.sends.append({"text": text, "thread_id": thread_id, "silent": disable_notification})
        return 1

    def delete_message(self, message_id: int) -> None:
        self.deleted.append(message_id)


def test_first_publish_creates_topic_and_header(tmp_path):
    sender = _FakeSender()
    pub = ForumPublisher(_config(tmp_path), sender)

    n = pub.publish("company", ["삼성전자 종합", "SK하이닉스 종합"], day="07.09")

    assert n == 2
    assert sender.created == ["📈 종목 리포트 07.09"]  # 토픽 1회 생성
    # 헤더는 알림 ON(무음 아님), 본문 2건은 무음
    headers = [s for s in sender.sends if not s["silent"]]
    bodies = [s for s in sender.sends if s["silent"]]
    assert len(headers) == 1 and "총 2건" in headers[0]["text"]
    assert len(bodies) == 2
    assert all(b["thread_id"] == 101 for b in bodies)


def test_second_publish_resends_header_and_accumulates(tmp_path):
    sender = _FakeSender()
    cfg = _config(tmp_path)
    pub = ForumPublisher(cfg, sender)
    pub.publish("company", ["첫 리포트"], day="07.09")
    # 헤더는 본문 누적 후 마지막에 게시되므로 첫 publish 의 마지막 non-silent send 가 헤더
    first_header_id = [s for s in sender.sends if not s["silent"]][-1]["id"]

    # 같은 날 추가 발송 — 상태 파일에서 복원되어야 함(새 인스턴스)
    pub2 = ForumPublisher(cfg, sender)
    pub2.publish("company", ["둘째 리포트"], day="07.09")

    # 토픽은 재생성되지 않음
    assert sender.created == ["📈 종목 리포트 07.09"]
    # 기존 헤더 삭제 후 재전송(알림)
    assert first_header_id in sender.deleted
    headers = [s for s in sender.sends if not s["silent"]]
    assert "총 2건" in headers[-1]["text"]  # 누적 count 갱신


def test_state_persisted_to_file(tmp_path):
    sender = _FakeSender()
    cfg = _config(tmp_path)
    ForumPublisher(cfg, sender).publish("industry", ["반도체 업황"], day="07.09")

    state = json.loads((tmp_path / "logs" / "forum_topics.json").read_text(encoding="utf-8"))
    # 키는 연도 포함(YYYY.07.09) — 이듬해 같은 MM.DD 충돌 방지
    keys = [k for k in state if k.startswith("industry|") and k.endswith(".07.09")]
    assert len(keys) == 1
    assert state[keys[0]]["count"] == 1
    assert state[keys[0]]["thread_id"] == 101


def test_different_days_get_separate_topics(tmp_path):
    sender = _FakeSender()
    cfg = _config(tmp_path)
    pub = ForumPublisher(cfg, sender)
    pub.publish("market_news", ["오전 뉴스"], day="07.09")
    pub.publish("market_news", ["다음날 뉴스"], day="07.10")

    assert sender.created == ["📰 장중 뉴스 07.09", "📰 장중 뉴스 07.10"]  # 일자별 별도 토픽


def test_empty_entries_noop(tmp_path):
    sender = _FakeSender()
    assert ForumPublisher(_config(tmp_path), sender).publish("company", [], day="07.09") == 0
    assert sender.created == []
    assert sender.sends == []


def test_topic_persisted_before_body_send(tmp_path):
    # 토픽 생성 직후 상태가 저장돼, 본문 발송 중 죽어도 다음 실행이 중복 토픽을 안 만든다.
    cfg = _config(tmp_path)
    state_path = tmp_path / "logs" / "forum_topics.json"

    class _CrashAfterCreate(_FakeSender):
        def send(self, text, thread_id=None, disable_notification=False):
            raise RuntimeError("crash mid-send")

    sender = _CrashAfterCreate()
    pub = ForumPublisher(cfg, sender)
    with contextlib.suppress(RuntimeError):
        pub.publish("company", ["리포트"], day="07.09")

    # 생성한 토픽이 파일에 남아 있어야 함
    assert sender.created == ["📈 종목 리포트 07.09"]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    key = next(k for k in saved if k.startswith("company|") and k.endswith(".07.09"))
    assert saved[key]["thread_id"] == 101

    # 다음 실행은 토픽을 재생성하지 않고 기존 것을 재사용
    sender2 = _FakeSender()
    ForumPublisher(cfg, sender2).publish("company", ["리포트 재시도"], day="07.09")
    assert sender2.created == []  # 중복 생성 없음


def test_corrupt_state_file_recovers(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "forum_topics.json").write_text("{bad json", encoding="utf-8")
    sender = _FakeSender()
    # 손상 파일이어도 새로 시작해 정상 발행
    n = ForumPublisher(_config(tmp_path), sender).publish("company", ["리포트"], day="07.09")
    assert n == 1
    assert sender.created == ["📈 종목 리포트 07.09"]
