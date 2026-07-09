from unittest.mock import MagicMock

import pytest

from reporter.telegram import TelegramError, TelegramSender, _split, _to_telegram_html


def test_short_text_single_chunk():
    assert _split("hello", limit=100) == ["hello"]


def test_html_escapes_special_chars():
    assert _to_telegram_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_html_converts_bold():
    assert _to_telegram_html("**중요** 및 __강조__") == "<b>중요</b> 및 <b>강조</b>"


def test_html_converts_markdown_link():
    assert _to_telegram_html("[네이버](https://naver.com)") == '<a href="https://naver.com">네이버</a>'


def test_html_leaves_plain_url_untouched():
    # 순수 URL 은 텔레그램이 자동 링크하므로 변환하지 않는다.
    assert _to_telegram_html("https://tinyurl.com/abc") == "https://tinyurl.com/abc"


def test_html_bracket_label_is_escaped_not_broken():
    # [증권사] 처럼 링크가 아닌 대괄호는 그대로 보존돼야 한다(깨지면 안 됨).
    assert _to_telegram_html("[미래에셋] 삼성전자") == "[미래에셋] 삼성전자"


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
    assert payload["parse_mode"] == "HTML"


def test_send_splits_long_message_into_multiple_posts(monkeypatch):
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender, session = _sender_with_mock_session(monkeypatch)

    long_text = "\n".join("x" * 3700 for _ in range(3))  # 분할 한도(_SPLIT_LEN) 초과 → 3청크
    assert sender.send(long_text) == 3
    assert session.post.call_count == 3


def test_send_falls_back_to_plain_on_parse_error(monkeypatch):
    # HTML 파싱 실패 시 서식 없이 원문 그대로 재발송해 브리핑 유실을 막는다.
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender = TelegramSender("token", "123")

    fail = MagicMock()
    fail.ok = False
    fail.status_code = 400
    fail.json.return_value = {"ok": False, "description": "can't parse entities: bad tag"}
    ok = MagicMock()
    ok.ok = True
    ok.json.return_value = {"ok": True, "result": {"message_id": 5}}
    session = MagicMock()
    session.post.side_effect = [fail, ok]
    sender._session = session

    assert sender.send("**굵게** <각주>") == 1
    assert session.post.call_count == 2
    # 폴백은 원문 그대로, parse_mode 없이 재발송한다.
    second = session.post.call_args_list[1]
    assert second.kwargs["json"]["text"] == "**굵게** <각주>"
    assert "parse_mode" not in second.kwargs["json"]


def test_send_does_not_fall_back_on_non_parse_error(monkeypatch):
    # 채팅 없음 등 파싱과 무관한 오류는 폴백하지 않고 그대로 실패시킨다.
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender, session = _sender_with_mock_session(monkeypatch, ok=False, description="chat not found")

    with pytest.raises(TelegramError, match="chat not found"):
        sender.send("메시지")
    assert session.post.call_count == 1


def test_send_raises_on_ok_false(monkeypatch):
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender, _ = _sender_with_mock_session(monkeypatch, ok=False, description="chat not found")

    with pytest.raises(TelegramError, match="chat not found"):
        sender.send("메시지")


def _sender_with_result(monkeypatch, result: dict) -> tuple:
    sender = TelegramSender("token", "123")
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"ok": True, "result": result}
    session = MagicMock()
    session.post.return_value = resp
    sender._session = session
    return sender, session


def test_send_into_topic_passes_thread_and_silent(monkeypatch):
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender, session = _sender_with_result(monkeypatch, {"message_id": 42})

    sender.send("본문", thread_id=101, disable_notification=True)
    payload = session.post.call_args.kwargs["json"]
    assert payload["message_thread_id"] == 101
    assert payload["disable_notification"] is True


def test_send_without_topic_omits_thread(monkeypatch):
    monkeypatch.setattr("reporter.telegram.time.sleep", lambda s: None)
    sender, session = _sender_with_result(monkeypatch, {"message_id": 1})

    sender.send("본문")
    payload = session.post.call_args.kwargs["json"]
    assert "message_thread_id" not in payload
    assert "disable_notification" not in payload


def test_create_forum_topic_returns_thread_id(monkeypatch):
    sender, session = _sender_with_result(monkeypatch, {"message_thread_id": 555})
    assert sender.create_forum_topic("📈 종목 리포트 07.09") == 555
    assert session.post.call_args.kwargs["json"]["chat_id"] == "123"
    assert "createForumTopic" in session.post.call_args.args[0]


def test_send_message_returns_message_id(monkeypatch):
    sender, _ = _sender_with_result(monkeypatch, {"message_id": 777})
    assert sender.send_message("헤더", thread_id=101) == 777


def test_delete_message_swallows_errors(monkeypatch):
    # 이미 없는 메시지 삭제는 조용히 무시(best-effort)해야 한다
    sender, _ = _sender_with_mock_session(monkeypatch, ok=False, description="message to delete not found")
    sender.delete_message(999)  # 예외 없이 통과


def test_http_error_surfaces_as_telegram_error(monkeypatch):
    # 텔레그램은 '포럼 아님/권한 없음'을 HTTP 4xx 로 알린다. raise_for_status 로 새면
    # 폴백(except TelegramError)을 우회하므로, _api 가 TelegramError 로 변환해야 한다.
    sender = TelegramSender("token", "123")
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 400
    resp.json.return_value = {"ok": False, "description": "the chat is not a forum"}
    session = MagicMock()
    session.post.return_value = resp
    sender._session = session

    with pytest.raises(TelegramError, match="not a forum"):
        sender.create_forum_topic("📈 종목 리포트 07.09")


def test_request_exception_surfaces_as_telegram_error(monkeypatch):
    # 전송 계층 예외(타임아웃 등)도 TelegramError 로 통일돼 폴백에 잡혀야 한다.
    import requests

    sender = TelegramSender("token", "123")
    session = MagicMock()
    session.post.side_effect = requests.RequestException("timeout")
    sender._session = session

    with pytest.raises(TelegramError):
        sender.send_message("헤더", thread_id=1)
