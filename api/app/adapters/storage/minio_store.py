"""MinIO PDF 저장소. 리포트 PDF 원본을 버킷에 저장하고 스트리밍 제공한다."""

from __future__ import annotations

import logging
from functools import lru_cache

from minio import Minio
from minio.error import S3Error

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _client() -> Minio:
    s = get_settings()
    client = Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key,
        secure=s.minio_secure,
    )
    if not client.bucket_exists(s.minio_bucket):
        client.make_bucket(s.minio_bucket)
    return client


def put_pdf(object_key: str, data: bytes) -> str:
    """PDF 바이트를 저장하고 객체 키를 반환한다."""
    return put_bytes(object_key, data, content_type="application/pdf")


def get_pdf(object_key: str) -> bytes | None:
    return get_bytes(object_key)


def put_bytes(object_key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """임의 바이트를 저장하고 객체 키를 반환한다. DART 원문 zip 캐시 등에 사용."""
    import io

    s = get_settings()
    _client().put_object(
        s.minio_bucket,
        object_key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return object_key


def get_bytes(object_key: str) -> bytes | None:
    s = get_settings()
    try:
        resp = _client().get_object(s.minio_bucket, object_key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except S3Error as e:
        logger.warning("minio get failed %s: %s", object_key, e)
        return None
