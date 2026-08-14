"""DART 원문 zip MinIO 캐시-aside 단위 테스트 — hit/miss/실패 시 fail-open/빈 응답.

fetch_report_zip 은 MinIO(dart-doc/{rcept_no}.zip) hit 시 DART 미호출, miss 시 다운로드+저장.
MinIO 오류는 fail-open(다운로드만)이라 캐시 장애가 수집을 막지 않아야 한다.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.adapters.dart import client
from app.adapters.dart import report_parser as p


def _zip(xml: str, name: str = "doc.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, xml.encode("utf-8"))
    return buf.getvalue()


ZIP = _zip("<DOCUMENT><TITLE>테스트</TITLE></DOCUMENT>")


@pytest.fixture(autouse=True)
def _fake_dart(monkeypatch):
    """DART document.xml 호출을 훅 — 기본값은 실패(캐시 hit 를 DART 호출과 분리)."""
    calls = []

    class _Resp:
        content = ZIP

        def raise_for_status(self):
            pass

    def fake_get(session, url, params=None, **kwargs):
        calls.append(params)
        return _Resp()

    monkeypatch.setattr(p.dart_throttle, "get", fake_get)
    return calls


def _monkey_minio(monkeypatch, store=None):
    """MinIO 스토어를 dict 로 흉내. store=None 이면 get/put 모두 예외(fail-open 검증용)."""
    if store is None:

        def bad_get(key):
            raise ConnectionError("minio down")

        def bad_put(key, data, content_type="application/octet-stream"):
            raise ConnectionError("minio down")

        monkeypatch.setattr(p.minio_store, "get_bytes", bad_get)
        monkeypatch.setattr(p.minio_store, "put_bytes", bad_put)
        return None

    def get_bytes(key):
        return store.get(key)

    def put_bytes(key, data, content_type="application/octet-stream"):
        store[key] = data

    monkeypatch.setattr(p.minio_store, "get_bytes", get_bytes)
    monkeypatch.setattr(p.minio_store, "put_bytes", put_bytes)
    return store


def _mk_session():
    return object()  # throttle.get 이 mock 이라 session 은 실제로 쓰이지 않는다


def test_miss_downloads_and_stores(monkeypatch, _fake_dart):
    store = _monkey_minio(monkeypatch, {})
    out = p.fetch_report_zip("key", "20240000001", _mk_session())
    assert out == ZIP
    assert len(_fake_dart) == 1  # DART 1회
    assert store["dart-doc/20240000001.zip"] == ZIP  # 저장됨


def test_hit_returns_cached_without_dart_call(monkeypatch, _fake_dart):
    _monkey_minio(monkeypatch, {"dart-doc/20240000002.zip": b"cached"})
    out = p.fetch_report_zip("key", "20240000002", _mk_session())
    assert out == b"cached"
    assert _fake_dart == []  # DART 미호출


def test_minio_get_failure_falls_back_to_download(monkeypatch, _fake_dart):
    _monkey_minio(monkeypatch, None)  # get/put 모두 실패
    out = p.fetch_report_zip("key", "20240000003", _mk_session())
    assert out == ZIP
    assert len(_fake_dart) == 1  # 캐시 장애에도 다운로드 진행


def test_minio_put_failure_still_returns_download(monkeypatch, _fake_dart):
    store = {}

    def bad_put(key, data, content_type="application/octet-stream"):
        raise ConnectionError("put down")

    monkeypatch.setattr(p.minio_store, "get_bytes", store.get)
    monkeypatch.setattr(p.minio_store, "put_bytes", bad_put)
    out = p.fetch_report_zip("key", "20240000004", _mk_session())
    assert out == ZIP  # 저장 실패해도 다운로드 반환


def test_empty_dart_response_not_cached(monkeypatch):
    calls = []

    class _Empty:
        content = b""

        def raise_for_status(self):
            pass

    def fake_get(session, url, params=None, **kwargs):
        calls.append(params)
        return _Empty()

    monkeypatch.setattr(p.dart_throttle, "get", fake_get)
    store = _monkey_minio(monkeypatch, {})
    out = p.fetch_report_zip("key", "20240000005", _mk_session())
    assert out == b""
    assert "dart-doc/20240000005.zip" not in store  # 빈 응답은 캐시하지 않음


def test_fetch_document_text_uses_cache(monkeypatch, _fake_dart):
    # fetch_document_text 가 fetch_report_zip 경유 → 캐시 hit 시 DART 미호출.
    _monkey_minio(monkeypatch, {"dart-doc/20240000006.zip": ZIP})
    text = client.fetch_document_text("key", "20240000006", _mk_session())
    assert "테스트" in text
    assert _fake_dart == []


def test_fetch_document_text_miss_downloads(monkeypatch, _fake_dart):
    _monkey_minio(monkeypatch, {})
    text = client.fetch_document_text("key", "20240000007", _mk_session())
    assert "테스트" in text
    assert len(_fake_dart) == 1


def test_fetch_document_text_parse_failure_returns_empty(monkeypatch, _fake_dart):
    _monkey_minio(monkeypatch, {})
    _fake_dart.clear()

    def fake_get(session, url, params=None, **kwargs):
        class _Bad:
            content = b"not a zip"

            def raise_for_status(self):
                pass

        _fake_dart.append(params)
        return _Bad()

    monkeypatch.setattr(p.dart_throttle, "get", fake_get)
    assert client.fetch_document_text("key", "20240000008", _mk_session()) == ""
