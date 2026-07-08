"""URL 단축기 단위 테스트 — 캐시·실패 폴백을 목킹으로 검증한다."""

from pathlib import Path
from unittest.mock import MagicMock

from reporter.shortener import UrlShortener


def _session(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_shortens_and_caches(tmp_path: Path):
    session = _session("https://tinyurl.com/abc123")
    s = UrlShortener(tmp_path / "cache.json", session)
    long = "https://example.com/very/long/path.pdf"

    assert s.shorten(long) == "https://tinyurl.com/abc123"
    # 재호출은 캐시 → 네트워크 호출 안 늘어남
    assert s.shorten(long) == "https://tinyurl.com/abc123"
    assert session.get.call_count == 1


def test_cache_persists_across_instances(tmp_path: Path):
    cache = tmp_path / "cache.json"
    UrlShortener(cache, _session("https://tinyurl.com/xyz")).shorten("http://a")
    # 새 인스턴스는 파일 캐시에서 읽어 네트워크 없이 반환
    s2 = _session("SHOULD_NOT_BE_USED")
    assert UrlShortener(cache, s2).shorten("http://a") == "https://tinyurl.com/xyz"
    assert s2.get.call_count == 0


def test_error_response_falls_back_to_original(tmp_path: Path):
    # tinyurl 이 'Error' 등 비-URL 응답 → 원본 반환
    s = UrlShortener(tmp_path / "c.json", _session("Error"))
    assert s.shorten("http://a") == "http://a"


def test_empty_url_returned_asis(tmp_path: Path):
    s = UrlShortener(tmp_path / "c.json", _session("x"))
    assert s.shorten("") == ""
