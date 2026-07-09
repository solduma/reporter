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
    SectorEtf("091160", "반도체", "KR"),  # KODEX 반도체 — 완제품·대형(삼성전자·SK하이닉스 등)
    SectorEtf("475300", "반도체 소부장", "KR"),  # SOL 반도체전공정 — 소재·부품·장비
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
    SectorEtf("XSD", "반도체 소부장", "US"),  # SPDR 동일가중 — 장비·소재 비중 큼
]

# judal 테마명 키워드 → 국내 섹터 ETF 섹터명. 종목이 속한 테마를 대표 섹터로 접는다.
# 앞쪽 항목이 우선(더 구체적인 것부터). 소문자 부분일치.
_THEME_TO_KR_SECTOR: list[tuple[tuple[str, ...], str]] = [
    (("반도체", "메모리", "dram", "시스템반도체", "네온가스"), "반도체"),
    (("2차전지", "2차 전지", "배터리", "전고체"), "2차전지"),
    (("자동차", "전기차", "수소차", "자율주행", "타이어"), "자동차"),
    (("바이오", "제약", "헬스케어", "진단", "백신"), "바이오"),
    (("은행", "금융지주"), "은행"),
    (("증권",), "증권"),
    (("보험",), "보험"),
    (("건설", "건자재", "시멘트", "리츠"), "건설"),
    (("조선", "운송", "해운", "항공", "물류"), "운송"),
    (("기계", "로봇", "공작기계", "방산"), "기계장비"),
    (("정유", "석유", "화학", "태양광", "풍력", "수소"), "에너지화학"),
    (("게임", "인터넷", "소프트웨어", "인공지능", "ai", "클라우드", "핀테크", "메타버스", "it"), "IT"),
    (("미디어", "엔터", "콘텐츠", "드라마", "웹툰", "광고"), "미디어컨텐츠"),
    (("화장품", "면세", "여행", "카지노", "레저", "의류", "유통"), "경기소비재"),
    (("음식료", "식품", "주류", "담배"), "필수소비재"),
]

# 국내 섹터 ETF 섹터명 → 미국 섹터 ETF 섹터명(GICS). 선행 분석용 대응.
_KR_SECTOR_TO_US: dict[str, str] = {
    "반도체": "반도체",
    "반도체 소부장": "반도체 소부장",
    "IT": "기술",
    "2차전지": "기술",  # 성장 테크로 나스닥·기술 흐름과 동행
    "바이오": "헬스케어",
    "자동차": "임의소비재",
    "에너지화학": "에너지",
    "은행": "금융",
    "증권": "금융",
    "보험": "금융",
    "운송": "산업재",
    "기계장비": "산업재",
    "건설": "리츠",
    "경기소비재": "임의소비재",
    "필수소비재": "필수소비재",
    "미디어컨텐츠": "커뮤니케이션",
}


def themes_to_kr_sector(theme_names: list[str]) -> str | None:
    """종목이 속한 judal 테마명들에서 대표 국내 섹터(ETF 섹터명)를 고른다.

    매핑 우선순위(_THEME_TO_KR_SECTOR 순서)가 앞선 섹터를 먼저 채택한다.
    어느 테마도 매칭 안 되면 None.
    """
    lowered = [t.lower() for t in theme_names]
    # 반도체 소부장: '반도체'가 있으면서 소재·부품·장비·공정 맥락일 때만(다른 섹터 소재/장비 제외).
    _SOBUJANG = ("소재", "부품", "장비", "전공정", "후공정", "소부장")
    for t in theme_names:
        if "반도체" in t and any(k in t for k in _SOBUJANG):
            return "반도체 소부장"
    for keywords, sector in _THEME_TO_KR_SECTOR:
        if any(any(k in t for k in keywords) for t in lowered):
            return sector
    return None


def kr_sector_to_us(kr_sector: str | None) -> str | None:
    """국내 섹터명 → 미국 섹터 ETF 섹터명. 없으면 None."""
    return _KR_SECTOR_TO_US.get(kr_sector) if kr_sector else None


# 미국 섹터 ETF 구성종목 API 가 없어, 섹터별 대표종목을 정적 매핑한다.
# (네이버 심볼, 표시명) — Nasdaq 은 '.O', NYSE 는 접미사 없음(실측). 시세는 조회 시 붙인다.
US_SECTOR_STOCKS: dict[str, list[tuple[str, str]]] = {
    "반도체": [("NVDA.O", "엔비디아"), ("AVGO.O", "브로드컴"), ("TSM", "TSMC"),
             ("AMD.O", "AMD"), ("QCOM.O", "퀄컴"), ("TXN.O", "텍사스인스트루먼트")],
    "반도체 소부장": [("ASML.O", "ASML"), ("AMAT.O", "어플라이드머티어리얼즈"),
                ("LRCX.O", "램리서치"), ("KLAC.O", "KLA"), ("TER.O", "테라다인"),
                ("ENTG.O", "엔테그리스")],
    "기술": [("AAPL.O", "애플"), ("MSFT.O", "마이크로소프트"), ("NVDA.O", "엔비디아"),
           ("ORCL.N", "오라클"), ("CRM.N", "세일즈포스"), ("ADBE.O", "어도비")],
    "커뮤니케이션": [("GOOGL.O", "알파벳"), ("META.O", "메타"), ("NFLX.O", "넷플릭스"),
              ("DIS", "디즈니"), ("T", "AT&T"), ("VZ", "버라이즌")],
    "임의소비재": [("AMZN.O", "아마존"), ("TSLA.O", "테슬라"), ("HD", "홈디포"),
             ("MCD", "맥도날드"), ("NKE", "나이키"), ("SBUX.O", "스타벅스")],
    "필수소비재": [("WMT", "월마트"), ("PG", "P&G"), ("KO", "코카콜라"),
             ("PEP.O", "펩시코"), ("COST.O", "코스트코")],
    "에너지": [("XOM", "엑슨모빌"), ("CVX", "셰브론"), ("COP", "코노코필립스"),
            ("SLB", "슐럼버거")],
    "금융": [("JPM", "JP모간"), ("BAC", "뱅크오브아메리카"), ("WFC", "웰스파고"),
           ("V", "비자"), ("MA", "마스터카드"), ("GS", "골드만삭스")],
    "헬스케어": [("LLY", "일라이릴리"), ("JNJ", "존슨앤존슨"), ("UNH", "유나이티드헬스"),
            ("MRK", "머크"), ("ABBV", "애브비"), ("PFE", "화이자")],
    "산업재": [("CAT", "캐터필러"), ("GE", "GE에어로스페이스"), ("BA", "보잉"),
            ("HON.O", "허니웰"), ("UPS", "UPS"), ("RTX", "RTX")],
    "소재": [("LIN.O", "린데"), ("SHW", "셔윈윌리엄스"), ("FCX", "프리포트맥모란"),
           ("NEM", "뉴몬트"), ("APD", "에어프로덕츠")],
    "리츠": [("PLD", "프로로지스"), ("AMT", "아메리칸타워"), ("EQIX.O", "에퀴닉스"),
           ("WELL", "웰타워"), ("SPG", "사이먼프로퍼티")],
    "유틸리티": [("NEE", "넥스트에라"), ("SO", "서던컴퍼니"), ("DUK", "듀크에너지"),
            ("CEG.O", "컨스텔레이션"), ("AEP.O", "아메리칸일렉트릭파워")],
}


def us_sector_stocks(us_sector: str | None) -> list[tuple[str, str]]:
    """미국 섹터명 → 대표종목 (심볼, 표시명) 목록. 없으면 빈 리스트."""
    return US_SECTOR_STOCKS.get(us_sector, []) if us_sector else []
