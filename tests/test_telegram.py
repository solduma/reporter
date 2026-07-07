from reporter.telegram import _split


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
