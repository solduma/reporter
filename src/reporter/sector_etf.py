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
    SectorEtf("445290", "로봇", "KR"),  # KODEX 로봇액티브
    # 섹터 미분류 전수 검수(#302)로 추가한 실질 섹터 ETF — 국내 ETF 우선 매칭 후 미국 대응.
    SectorEtf("307510", "의료기기", "KR"),  # TIGER 의료기기 (헬스케어 하위, 바이오와 구분)
    SectorEtf("117680", "철강", "KR"),  # KODEX 철강
    SectorEtf("453950", "방산우주", "KR"),  # TIGER 우주방산 (방산·항공우주)
    SectorEtf("098560", "통신", "KR"),  # TIGER 방송통신 (통신장비·5G·통신서비스)
    SectorEtf("228790", "화장품", "KR"),  # TIGER 화장품 (K뷰티·미용)
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
    SectorEtf("LIT", "2차전지", "US"),  # Global X 리튬·배터리 (2차전지 미국 대응)
    SectorEtf("XBI", "바이오", "US"),  # SPDR 바이오테크
    SectorEtf("BOTZ.O", "로봇", "US"),  # Global X 로봇·AI
    SectorEtf("ALB", "리튬", "US"),  # 앨버말 — 리튬 대표주(리튬 ETF 부재)
    # 신규 국내 섹터의 미국 대응(#302).
    SectorEtf("IHI", "의료기기", "US"),  # iShares 미국 의료기기
    SectorEtf("SLX", "철강", "US"),  # VanEck 철강
    SectorEtf("ITA", "방산우주", "US"),  # iShares 미국 항공우주·방산
]

# judal 테마명 키워드 → 국내 섹터 ETF 섹터명. 종목이 속한 테마를 대표 섹터로 접는다.
# 앞쪽 항목이 우선(더 구체적인 것부터). 소문자 부분일치.
# 순서 = 우선순위(더 구체적인 섹터를 앞에). 의료기기·방산우주는 바이오·기계장비보다 앞에 둬
# 더 세분화된 섹터로 먼저 접히게 한다.
_THEME_TO_KR_SECTOR: list[tuple[tuple[str, ...], str]] = [
    (("반도체", "메모리", "dram", "시스템반도체", "네온가스", "oled", "디스플레이", "lcd", "led"), "반도체"),
    (("2차전지", "2차 전지", "배터리", "전고체", "ess", "전력저장"), "2차전지"),
    (("자동차", "전기차", "수소차", "자율주행", "타이어", "중고차", "렌터카"), "자동차"),
    # 의료기기·미용기기·치아·비대면진료는 바이오(제약)와 구분되는 의료기기 섹터.
    (("의료기기", "미용기기", "치아", "임플란트", "비대면 진료", "원격의료"), "의료기기"),
    (("바이오", "제약", "헬스케어", "진단", "백신", "줄기세포", "비만치료", "위고비", "당뇨",
      "마이크로바이옴", "탈모", "면역", "캔서", "항암", "mrna", "봉독"), "바이오"),
    (("철강", "제강", "특수강", "페라이트", "니켈"), "철강"),
    (("방산", "우주", "위성", "드론", "스페이스", "미사일", "무기"), "방산우주"),
    (("통신장비", "통신 장비", "5g", "6g", "광통신", "통신", "전선", "케이블"), "통신"),
    (("화장품", "미용", "뷰티", "성형"), "화장품"),
    (("은행", "금융지주"), "은행"),
    (("증권", "신용평가", "창투사"), "증권"),
    (("보험",), "보험"),
    (("건설", "건자재", "시멘트", "리츠", "리모델링", "인테리어", "가구"), "건설"),
    (("조선", "운송", "해운", "항공", "물류", "골판지"), "운송"),
    (("기계", "로봇", "공작기계", "무인화", "키오스크", "3d프린터", "변압기", "중공업"), "기계장비"),
    (("정유", "석유", "화학", "태양광", "풍력", "수소", "원자력", "smr", "핵융합", "천연가스",
      "비료", "원자재", "자원개발"), "에너지화학"),
    (("게임", "인터넷", "소프트웨어", "인공지능", "ai", "클라우드", "핀테크", "메타버스", "it",
      "보안", "사이버", "양자", "데이터 센터", "가상화폐", "vr"), "IT"),
    (("미디어", "엔터", "콘텐츠", "드라마", "웹툰", "광고", "방송", "교육"), "미디어컨텐츠"),
    (("화장품", "면세", "여행", "카지노", "레저", "의류", "유통", "패션", "아웃도어", "반려동물",
      "유아", "출산", "골프"), "경기소비재"),
    (("음식료", "식품", "주류", "담배", "농업", "농산물", "수산", "사료", "닭고기", "돼지고기",
      "육계", "화장지"), "필수소비재"),
]

# 국내 섹터 ETF 섹터명 → 미국 섹터 ETF 섹터명(GICS). 선행 분석용 대응.
_KR_SECTOR_TO_US: dict[str, str] = {
    "반도체": "반도체",
    "반도체 소부장": "반도체 소부장",
    "IT": "기술",
    "2차전지": "2차전지",  # LIT(리튬·배터리)로 직접 대응
    "바이오": "바이오",  # XBI(바이오테크)로 직접 대응
    "로봇": "로봇",  # BOTZ(로봇·AI)로 직접 대응
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
    # 신규 섹터 → 미국 대응(#302).
    "의료기기": "의료기기",  # IHI 직접 대응
    "철강": "철강",  # SLX 직접 대응
    "방산우주": "방산우주",  # ITA 직접 대응
    "통신": "커뮤니케이션",  # XLC(통신서비스)
    "화장품": "필수소비재",  # XLP(생활필수) — 화장품 미국 ETF 부재
}


def themes_to_kr_sector(theme_names: list[str]) -> str | None:
    """종목이 속한 judal 테마명들에서 대표 국내 섹터(ETF 섹터명)를 고른다.

    매핑 우선순위(_THEME_TO_KR_SECTOR 순서)가 앞선 섹터를 먼저 채택한다.
    어느 테마도 매칭 안 되면 None.
    """
    # 멱등 가드: 입력이 이미 확정 섹터명(ETF 섹터)이면 그대로 돌려준다. 키워드 부분일치가
    # 확정명을 재폴딩해 엉뚱한 섹터로 가는 것을 막는다(예: '필수소비재'의 '수소'→에너지화학,
    # '로봇'→기계장비, '경기소비재'→None). 종목 상세가 topdown.kr_sector(이미 폴딩된 값)를
    # 섹터 차트에 다시 넘기는 경로에서 특히 중요.
    _CANONICAL = {e.sector for e in KR_SECTOR_ETFS}
    for t in theme_names:
        if t in _CANONICAL:
            return t

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


# 종목코드 → 섹터 수동 오버라이드. judal 테마가 지주사·우선주 등 노이즈뿐이라 테마로는 못 잡지만
# 실질 사업이 특정 섹터인 종목을 직접 지정한다(전수 검수 #302). 테마 매핑보다 우선한다.
_CODE_SECTOR_OVERRIDE: dict[str, str] = {
    "035610": "의료기기",  # 솔본 — 인바디 등 의료기기 지주(테마=지주사)
}


def stock_kr_sector(code: str | None, theme_names: list[str]) -> str | None:
    """종목의 대표 국내 섹터. 수동 오버라이드(코드)를 먼저 보고, 없으면 테마 키워드로 접는다."""
    if code and code in _CODE_SECTOR_OVERRIDE:
        return _CODE_SECTOR_OVERRIDE[code]
    return themes_to_kr_sector(theme_names)


def kr_sector_to_us(kr_sector: str | None) -> str | None:
    """국내 섹터명 → 미국 섹터 ETF 섹터명. 없으면 None."""
    return _KR_SECTOR_TO_US.get(kr_sector) if kr_sector else None


# 국내 섹터(ETF 섹터명) → 네이버 산업 리포트 industry_name 후보. 명칭 체계가 달라(섹터 '바이오' vs
# 리포트 '제약') 매핑한다. 산업 리포트 실제 값: IT·건설·게임·반도체·제약·화장품·조선·철강금속 등.
# (커버리지·딥다이브가 종목이 속한 산업 리포트를 찾는 공용 매핑 — 한 곳에서 소유한다.)
_KR_SECTOR_TO_REPORT_INDUSTRIES: dict[str, tuple[str, ...]] = {
    "반도체": ("반도체", "전기전자"), "반도체 소부장": ("반도체", "전기전자"),
    "2차전지": ("전기전자", "석유화학"), "바이오": ("제약",), "의료기기": ("제약",),
    "자동차": ("자동차",), "조선": ("조선",), "건설": ("건설",), "철강": ("철강금속",),
    "에너지화학": ("석유화학", "에너지", "유틸리티"), "IT": ("IT", "전기전자"),
    "미디어컨텐츠": ("미디어", "게임"), "통신": ("통신",), "게임": ("게임",),
    "화장품": ("화장품",), "경기소비재": ("유통",), "필수소비재": ("음식료", "유통"),
    "기계장비": ("기타",), "로봇": ("기타", "IT"), "방산우주": ("기타",),
    "은행": ("은행",), "증권": ("증권",), "보험": ("보험",), "운송": ("항공운송",),
}


def kr_sector_to_report_industries(kr_sector: str | None) -> tuple[str, ...]:
    """국내 섹터명 → 산업 리포트 industry_name 후보들. 매핑 없으면 빈 튜플."""
    return _KR_SECTOR_TO_REPORT_INDUSTRIES.get(kr_sector or "", ())


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
    "로봇": [("ISRG.O", "인튜이티브서지컬"), ("ROK", "로크웰오토메이션"),
           ("TER.O", "테라다인"), ("NVDA.O", "엔비디아")],
    "바이오": [("VRTX.O", "버텍스"), ("REGN.O", "리제네론"), ("GILD.O", "길리어드"),
            ("AMGN.O", "암젠"), ("MRNA.O", "모더나")],
    "리튬": [("ALB", "앨버말"), ("SQM", "SQM"), ("LAC", "리튬아메리카스")],
    "2차전지": [("ALB", "앨버말"), ("SQM", "SQM"), ("LAC", "리튬아메리카스"), ("TSLA.O", "테슬라")],
}


def us_sector_stocks(us_sector: str | None) -> list[tuple[str, str]]:
    """미국 섹터명 → 대표종목 (심볼, 표시명) 목록. 없으면 빈 리스트."""
    return US_SECTOR_STOCKS.get(us_sector, []) if us_sector else []


# 지수 > 섹터 > 종목 흐름: 국내 지수 ↔ 미국 지수 추종 ETF(차트용). (한국명, 한국심볼, 미국명, 미국심볼)
INDEX_PAIRS: list[tuple[str, str, str, str]] = [
    ("코스피", "KOSPI", "나스닥100(QQQ)", "QQQ.O"),
    ("코스닥", "KOSDAQ", "러셀2000(IWM)", "IWM"),
]


def kr_sector_etf(sector: str) -> SectorEtf | None:
    """국내 섹터명 → 국내 섹터 ETF. 없으면 None."""
    return next((e for e in KR_SECTOR_ETFS if e.sector == sector), None)


def us_sector_etf(us_sector: str | None) -> SectorEtf | None:
    """미국 섹터명 → 미국 섹터 ETF. 없으면 None."""
    return next((e for e in US_SECTOR_ETFS if e.sector == us_sector), None) if us_sector else None
