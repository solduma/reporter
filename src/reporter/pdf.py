"""리포트 PDF 다운로드 및 텍스트 추출."""

from __future__ import annotations

import logging

import fitz  # PyMuPDF
import requests

from .models import Report

logger = logging.getLogger(__name__)

_MAX_PAGES = 3  # 핵심은 앞 3페이지 (차트 설명·면책조항 노이즈 제외)


def extract_text_from_bytes(content: bytes, max_pages: int = _MAX_PAGES) -> str:
    """PDF 바이트에서 앞 max_pages 페이지 텍스트를 추출한다. 다운로드와 분리된 순수 함수."""
    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            parts = [page.get_text() for i, page in enumerate(doc) if i < max_pages]
    except Exception as e:  # PyMuPDF 는 손상 PDF 에 다양한 예외를 던진다
        logger.warning("PDF parse failed: %s", e)
        return ""
    return "\n".join(parts).strip()


def extract_text(report: Report, session: requests.Session) -> str:
    if not report.pdf_url:
        return ""
    try:
        resp = session.get(report.pdf_url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("PDF download failed %s: %s", report.pdf_url, e)
        return ""

    return extract_text_from_bytes(resp.content, _MAX_PAGES)


def enrich_with_text(reports: list[Report]) -> list[Report]:
    """PDF 텍스트를 추출해 report.text 를 채운다. 텍스트가 없는 리포트는 제외한다."""
    session = requests.Session()
    enriched: list[Report] = []
    for r in reports:
        r.text = extract_text(r, session)
        if r.text:
            enriched.append(r)
    return enriched
