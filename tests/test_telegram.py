from unittest.mock import MagicMock

import pytest

from reporter.telegram import TelegramError, TelegramSender, _split


def test_short_text_single_chunk():
    assert _split("hello", limit=100) == ["hello"]


def test_splits_on_newline_boundary():
    text = "a" * 60 + "\n" + "b" * 60
    chunks = _split(text, limit=100)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 60
    assert chunks[1] == "b" * 60


def test_all_chunks_within_limit():
    text = "\n".join("line " + str(i) for i in range(500))
    chunks = _split(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)


def test_single_long_line_is_hard_split():
    text = "x" * 250
    chunks = _split(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_roundtrip_preserves_content_ignoring_join_newlines():
    text = "aaa\nbbb\nccc"
    chunks = _split(text, limit=5)
    # 각 줄이 limit 이하이므로 개행 경계로만 쪼개지고 내용은 보존된다
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_constructor_rejects_missing_credentials():
    with pytest.raises(TelegramError):
        TelegramSender("", "123")
    with pytest.raises(TelegramError):
        TelegramSender("token", "")


def _sender_with_mock_session(monkeypatch, ok: bool = True, description: str = "") -> tuple:
    sender = TelegramSender("token", "123")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"ok": ok, "description": description}
    session = MagicMock()
    session.post.return_value = resp
    sender._session = session
    return sender, session


def test_send_short_message_posts_once(monkeypatch):
    # 페이싱 sleep 이 실제로 돌지 않도록 시간을 정지
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender, session = _sender_with_mock_session(monkeypatch)

    assert sender.send("짧은 메시지") == 1
    assert session.post.call_count == 1
    payload = session.post.call_args.kwargs["json"]
    assert payload["chat_id"] == "123"
    assert payload["text"] == "짧은 메시지"


def test_send_splits_long_message_into_multiple_posts(monkeypatch):
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender, session = _sender_with_mock_session(monkeypatch)

    long_text = "\n".join("x" * 4000 for _ in range(3))  # 4096 한도 초과 → 3청크
    assert sender.send(long_text) == 3
    assert session.post.call_count == 3


def test_send_raises_on_ok_false(monkeypatch):
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender, _ = _sender_with_mock_session(monkeypatch, ok=False, description="chat not found")

    with pytest.raises(TelegramError, match="chat not found"):
        sender.send("메시지")
