"""섹터 ETF 목록 — 수급 기반 섹터 로테이션의 시세·수급 프록시.

국내는 KODEX/TIGER 대표 섹터 ETF(네이버 domestic 차트), 미국은 SPDR 11 GICS
섹터 + 반도체(네이버 foreign 차트). 미국 심볼은 네이버 RIC 접미사가 티커마다 달라
(XLRE=.K, SMH=.O) 실측 검증한 값을 고정한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectorEtf:
    symbol: str  # 국내=6자리코드 / 미국=네이버 심볼(접미사 포함)
    sector: str  # 표시 섹터명
    market: str  # KR | US


# 국내 대표 섹터 ETF(실측 검증). 종목코드로 domestic 차트 조회.
KR_SECTOR_ETFS: list[SectorEtf] = [
    SectorEtf("091160", "반도체", "KR"),
    SectorEtf("266370", "IT", "KR"),
    SectorEtf("305720", "2차전지", "KR"),
    SectorEtf("244580", "바이오", "KR"),
    SectorEtf("091180", "자동차", "KR"),
    SectorEtf("117460", "에너지화학", "KR"),
    SectorEtf("091170", "은행", "KR"),
    SectorEtf("102970", "증권", "KR"),
    SectorEtf("140700", "보험", "KR"),
    SectorEtf("140710", "운송", "KR"),
    SectorEtf("102960", "기계장비", "KR"),
    SectorEtf("117700", "건설", "KR"),
    SectorEtf("266390", "경기소비재", "KR"),
    SectorEtf("266410", "필수소비재", "KR"),
    SectorEtf("228810", "미디어컨텐츠", "KR"),
]

# 미국 SPDR 11 GICS 섹터 + 반도체(SMH). 네이버 foreign 차트 심볼(접미사 실측).
US_SECTOR_ETFS: list[SectorEtf] = [
    SectorEtf("XLK", "기술", "US"),
    SectorEtf("XLC", "커뮤니케이션", "US"),
    SectorEtf("XLY", "임의소비재", "US"),
    SectorEtf("XLP", "필수소비재", "US"),
    SectorEtf("XLE", "에너지", "US"),
    SectorEtf("XLF", "금융", "US"),
    SectorEtf("XLV", "헬스케어", "US"),
    SectorEtf("XLI", "산업재", "US"),
    SectorEtf("XLB", "소재", "US"),
    SectorEtf("XLRE.K", "리츠", "US"),  # XLRE 는 bare 로 빈 배열 → .K 필요
    SectorEtf("XLU", "유틸리티", "US"),
    SectorEtf("SMH.O", "반도체", "US"),  # Nasdaq 상장 → .O 필요
]
