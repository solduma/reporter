"""judal.co.kr 테마(섹터)·종목 매핑 스크래퍼.

메인 페이지에서 테마 목록(테마명·themeIdx·종목수)을, 테마 상세에서 구성 종목
(코드·이름·시장)을 긁는다. 수급 기반 섹터 로테이션의 섹터↔종목 매핑 소스.
실패는 조용히 흡수(빈 결과 반환)해 파이프라인을 막지 않는다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

_BASE = "https://www.judal.co.kr/"
_THEME_URL = _BASE + "?view=stockList&themeIdx={idx}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# 메인 페이지 테마 링크: themeIdx + 앵커 텍스트('2차전지(22)' 형태).
_THEME_LINK_RE = re.compile(r'themeIdx=(\d+)"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_COUNT_SUFFIX_RE = re.compile(r"\((\d+)\)\s*$")  # 테마명 끝의 '(22)' 종목수

# 테마 상세의 종목 행: 네이버 종목 링크 안에 '이름\n시장 코드' 구조.
_STOCK_RE = re.compile(
    r"code=(\d{6})\"[^>]*>(.*?)</a>", re.DOTALL
)


@dataclass
class Theme:
    idx: int
    name: str  # 종목수 접미사 제거한 순수 테마명
    stock_count: int  # 메인 목록에 표기된 종목수


@dataclass
class ThemeStocks:
    idx: int
    name: str
    stocks: list[tuple[str, str]] = field(default_factory=list)  # (code, stock_name)


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def fetch_themes(session: requests.Session | None = None) -> list[Theme]:
    """메인 페이지에서 전체 테마 목록을 긁는다. 실패 시 빈 리스트."""
    session = session or requests.Session()
    try:
        resp = session.get(_BASE, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("judal main fetch failed: %s", e)
        return []

    themes: dict[int, Theme] = {}
    for idx_str, raw in _THEME_LINK_RE.findall(resp.text):
        label = _clean(raw)
        if not label:
            continue
        idx = int(idx_str)
        count_match = _COUNT_SUFFIX_RE.search(label)
        count = int(count_match.group(1)) if count_match else 0
        name = _COUNT_SUFFIX_RE.sub("", label).strip()
        if name and idx not in themes:
            themes[idx] = Theme(idx=idx, name=name, stock_count=count)
    return list(themes.values())


def fetch_theme_stocks(idx: int, session: requests.Session | None = None) -> ThemeStocks:
    """테마 상세에서 구성 종목(코드·이름)을 긁는다. 실패 시 빈 stocks."""
    session = session or requests.Session()
    try:
        resp = session.get(_THEME_URL.format(idx=idx), headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("judal theme %s fetch failed: %s", idx, e)
        return ThemeStocks(idx=idx, name="", stocks=[])

    name_match = re.search(r"<h1[^>]*>([^<]+)</h1>", resp.text)
    name = _clean(name_match.group(1)) if name_match else ""
    name = re.sub(r"\s*테마주\s*$", "", name).strip()  # '전자결제 테마주' → '전자결제'

    seen: set[str] = set()
    stocks: list[tuple[str, str]] = []
    for code, inner in _STOCK_RE.findall(resp.text):
        if code in seen:
            continue
        # 링크 안쪽은 '이름\n\t시장 코드' → 첫 줄이 종목명.
        first_line = _clean(inner).split("\n")[0].strip()
        # 종목명에서 뒤따르는 '시장 코드' 잔여 제거(파싱 안전장치).
        stock_name = re.sub(r"\s*(KOSPI|KOSDAQ)\s*\d{6}\s*$", "", first_line).strip()
        seen.add(code)
        stocks.append((code, stock_name))
    return ThemeStocks(idx=idx, name=name, stocks=stocks)
