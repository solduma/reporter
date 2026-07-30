"""엔티티 해석(normalizer) — raw mention → 정준 canonical ID.

LLM/normalizer 분리 원칙: LLM 은 raw name + source_quote 만 내고, 이 모듈이 결정론적으로
정준화한다. confidence < 0.85 → pending_review 후보(자동 병합 금지 — 잘못된 병합이 중복보다 나쁨).

financial_ontology.normalizer 의 Resolution/Normalizer 패턴을 미러하되, 노드 타입별 해석과
confidence/pending_review 개념을 추가한다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from .models import BusinessOntology, NodeType

ResolveStatus = Literal["canonical", "pending_review", "rejected", "unknown"]
MatchedVia = Literal[
    "id",
    "korean_name",
    "english_name",
    "alias",
    "gics_code",
    "industry_code",
    "fuzzy",
    "auto_new",
    "keyword",
    "",
]

# 회사명에서 제거할 법인 형태 접두/접미사. strip 후 정확 매칭.
_COMPANY_SUFFIXES = (
    "주식회사",
    "(주)",
    "(株)",
    "㈜",
    "Co.,Ltd.",
    "Co., Ltd.",
    "Co,Ltd.",
    "Co, Ltd.",
    "Ltd.",
    "Ltd",
    "Inc.",
    "Inc",
    "Corp.",
    "Corp",
    "Limited",
    "Incorporated",
)
_CONFIDENCE_THRESHOLD = 0.85
_FUZZY_THRESHOLD = 0.9
# 자동 new 발급 신뢰도 — 정적 사전 매칭(1.0)/사람 검증(1.0)보다 낮아 구분 가능.
_AUTO_NEW_CONFIDENCE = 0.7

# 회사가 아닌 NER 오분류(고객 유형·기관 유형) — reject.
_COMPANY_NONENTITY_BLACKLIST = frozenset(
    {"병ㆍ의원", "병의원", "병원", "약국", "환자", "소비자", "고객", "의원"}
)

# 자동 new 발급 대상 타입 — company·industry 제외.
# company: 상장사는 CorpCodeMap DB(CMP_KRX_)이 정체성 → 패키지는 시드 사전만 매칭하고
#          비상장 자동 new(CMP_GLOBAL_) 발급은 서비스(resolve_company)가 CorpCodeMap 확인 후 주도.
# industry: GICS 8자리 코드가 정체성 → 자유표현은 키워드 매핑.
_AUTO_NEW_TYPES = frozenset({"product", "raw_material", "segment"})

# 자동 발급 canonical_id 접두어. 정적 사전(PRD_/MAT_/SEG_/CMP_GLOBAL_)과 구분.
_AUTO_NEW_PREFIX = {
    "company": "CMP_GLOBAL_",
    "product": "PRD_AUTO_",
    "raw_material": "MAT_AUTO_",
    "segment": "SEG_AUTO_",
}

# industry 자유표현 → GICS 8자리 코드 매핑. 접미사(시장/산업/사업/제조업/제조) 제거 후 키워드 포함 매칭.
# 긴 키워드 우선(_keyword_to_gics 가 len desc 정렬) — "자동차용 도료"는 "자동차용"(부품)이 "도료"(화학)보다 먼저.
# GICS 코드는 시드 industries(133 = 128 GICS + 5 KRX 커스텀)에 존재하는 코드만 사용.
# 시드에 GICS 분류가 없는 한국 특화(교육·농업·어업·사료)는 _INDUSTRY_KEYWORD_TO_CUSTOM(IND_KRX_) 로 매핑.
# 비산업(공공/서비스업/제조)은 _INDUSTRY_NONINDUSTRY_BLACKLIST reject. 모호 약어(AM/ET)·회사(글로벌 전자 기업)은 pending(HITL).
_INDUSTRY_KEYWORD_TO_GICS = {
    # 10205030 석유·가스 정제·마케팅 / 10205040 저장·운송
    "윤활유": "10205030", "석유": "10205030", "정유": "10205030", "석유수출입": "10205040",
    # 15101010 다목적 화학 / 15101020 특수 화학 / 15101030 비료·농약 / 15101040 산업용 가스
    "가스&케미": "15101010", "석유화학": "15101010", "합성수지": "15101010", "화학물질": "15101010", "화학": "15101010",
    "에너지경화": "15101020", "탄산칼슘": "15101020", "첨가제": "15101020", "페인트": "15101020",
    "도료": "15101020", "세제": "15101020", "잉크": "15101020", "접착": "15101020", "코팅": "15101020", "화공": "15101020", "촉매": "15101020",
    "농약": "15101030", "비료": "15101030", "가스": "15101040",
    # 15102010 건축 자재 / 15103010 금속·유리·플라스틱 용기 / 15104010 알루미늄 / 15104020 다목적 금속
    # 15104040 귀금속·광물 / 15104050 철강 / 15105010 임산물 / 15105020 제지
    "PC(Precast": "15102010", "아스팔트": "15102010", "콘크리트": "15102010", "골재": "15102010", "석회": "15102010",
    "금속포장": "15103010", "라미필름": "15103010", "코팅유리": "15103010", "플라스틱": "15103010",
    "병마개": "15103010", "제관": "15103010", "필름": "15103010",
    "알루미늄": "15104010", "세라믹스": "15104020", "세라믹": "15104020", "내화물": "15104020", "금속": "15104020", "제련": "15104020",
    "비금속": "15104040", "광산": "15104040",
    "스테인리스": "15104050", "강관": "15104050", "강교": "15104050", "철강": "15104050",
    "해외조림": "15105010", "임업": "15105010", "제지": "15105020",
    # 20101010 항공·국방 (UAM/항공기/드론/방산/방위)
    "U AM": "20101010", "무인항공": "20101010", "UAM": "20101010", "항공기": "20101010", "드론": "20101010",
    "방산": "20101010", "방위": "20101010", "항공": "20101010", "항법": "20101010",
    # 20104010 건축 제품 / 20104020 건축·엔지니어링 서비스 / 20104030 주택 건설
    "건축": "20104010", "종합건설엔지니어링": "20104020", "전기공사": "20104020", "플랜트": "20104020",
    "소방": "20104020", "토목": "20104020", "건설": "20104030",
    # 20105010 전기 부품·장비 / 20106010 건설기계 / 20106020 산업 기계·부품
    "전자유도가열": "20105010", "특수전원": "20105010",
    "중장비": "20106010", "정밀 부품": "20106020", "캐스터사업": "20106020", "프로토타입": "20106020",
    "MRO": "20106020", "엔진": "20106020", "폼웍": "20106020", "금형": "20106020", "프레스금형": "20106020", "프레스제품": "20106020",
    # 20107020 환경·시설 서비스 (재생/폐기물/리사이클링/소화기/산업안전) / 20107030 사무서비스·외주
    "Recycling": "20107020", "리사이클링": "20107020", "산업 안전": "20107020", "산업폐기물": "20107020",
    "재생처리": "20107020", "폐배터리": "20107020", "소화기": "20107020", "환경": "20107020",
    "IP(지식재산권)": "20107030", "매니지먼트": "20107030", "지적재산권": "20107030", "일괄관리": "20107030",
    # 20108010 항공 화물·물류 / 20108020 여객 항공 / 20108030 해운 / 20108040 철도 / 20108050 물류 인프라 / 20108060 트럭 운송
    "항공운송": "20108010", "국내외항공": "20108020", "항공기정비": "20108020", "항공기취급": "20108020",
    "해양 플랜트": "20108030", "해상운송": "20108030", "선박": "20108030", "조선": "20108030", "해운": "20108030",
    "철도": "20108040", "냉동냉장": "20108050", "물류": "20108050", "창고": "20108050", "운송": "20108060",
    # 25101010 자동차 부품·장비 / 25102010 자동차 제조
    "자동차 부품": "25101010", "등속조인트": "25101010", "자동차부품": "25101010", "ADAS": "25101010",
    "자동차용": "25101010", "자율주행": "25101010", "CCS": "25101010", "HUD": "25101010", "전장": "25101010", "주물": "25101010",
    "자동차 산업": "25102010", "전기차 충전": "25102010", "전기자동차": "25102010", "전기차": "25102010",
    # 25202010 가구 / 25202020 주방용품 / 25203010 레저용품
    "가구": "25202010", "주방": "25202020", "자전거": "25203010",
    # 25402010 방송 / 25404010 인터랙티브 미디어·서비스
    "방송": "25402010", "미술품": "25404010", "운세": "25404010",
    # 25501020 다목적 소매 / 25501030 전자상거래 / 25502030 전문 소매
    "유통판매": "25501020", "소매": "25501020", "소분": "25501020", "유통": "25501020",
    "항공기내 면세": "25501030", "전자상거래": "25501030", "방문판매": "25501030", "통신판매": "25501030", "홈퍼니싱": "25501030",
    "B2B 산업재": "25502030",
    # 30201010 가공 식품 / 30202010 주류 / 30202015 증류주·와인
    "건강기능식품": "30201010", "식품첨가물": "30201010", "식품소재": "30201010", "기내식": "30201010",
    "음식료": "30201010", "과자": "30201010", "식품": "30201010", "장류": "30201010",
    "주류": "30202010", "소주": "30202015", "주정": "30202015",
    # 30301010 가정용품 / 30302010 개인용품 / 30302020 의류·신발·액세서리 제조
    "내구성 소비재": "30301010", "소비재": "30301010",
    "마스크팩": "30302010", "화장품": "30302010", "미용": "30302010",
    "인조피혁": "30302020", "팹리스": "30302020", "봉제": "30302020", "섬유": "30302020",
    "원단": "30302020", "의류": "30302020", "피혁": "30302020",
    # 35101010 헬스케어 장비 / 35102010 헬스케어 시설 / 35103010 헬스케어 기술
    "의료기기": "35101010", "내시경": "35101010", "실버": "35102010",
    "의료 인공지능": "35103010", "의료정보": "35103010", "헬스케어": "35103010", "의료": "35103010",
    # 35201010 제약 / 35202010 생명공학 / 35202020 생명과학 도구·서비스
    "동물용의약품": "35201010", "마이크로니들": "35201010", "원료의약품": "35201010", "의약품원료": "35201010",
    "의약품제조": "35201010", "제약바이오": "35201010", "동물약품": "35201010", "소염진통": "35201010",
    "완제의약": "35201010", "의약품": "35201010", "제약": "35201010",
    "첨단 바이오": "35202010", "재생의학": "35202010", "조직공학": "35202010", "농생명": "35202010",
    "바이오": "35202010", "BT": "35202010", "신약": "35202010",
    "CDMO": "35202020", "CRO": "35202020", "NGS": "35202020",
    # 40101010 다목적 은행 / 40201010 다목적 금융 서비스 / 40201030 자본시장 / 40203010 보험 / 40301010 부동산 운영
    "금융": "40101010", "ATM VAN": "40201010", "VAN사업": "40201010", "물품매도": "40201010", "종합상사": "40201010",
    "지주회사": "40201010", "수출입": "40201010", "핀테크": "40201010", "무역": "40201010",
    "신용정보": "40201030", "보험": "40203010",
    "부동산임대": "40301010", "임대업": "40301010", "주차장": "40301010",
    # 45101010 반도체 장비 / 45101020 대규모 반도체 제조 / 45102010 대규모 반도체 제조
    "디스플레이 장비": "45101010", "핵융합": "45101020", "풍력": "45101020",
    "foundry": "45102010", "파운드리": "45102010", "메모리": "45102010", "반도체": "45102010", "후공정": "45102010",
    # 45201010 응용 소프트웨어 / 45202010 IT 컨설팅·서비스 / 45202020 데이터 처리·외주 서비스
    "소프트웨어": "45201010", "블록체인": "45201010", "인공지능": "45201010", "AI": "45201010",
    "전자 엔지니어링": "45202010", "투자 및 경영": "45202010", "SI 산업": "45202010", "전문,과학": "45202010",
    "경영자문": "45202010", "연구개발": "45202010", "컨설팅": "45202010", "IT": "45202010",
    "제로트러스트": "45202020", "정보보호": "45202020", "보안": "45202020",
    # 45301010 통신 장비 / 45302010 기술 하드웨어·저장·주변장치 / 45302020 전자 제조 서비스
    # 45302030 전자 부품 / 45302040 전자 계기·제어
    "스마트폰": "45301010", "전자통신": "45301010", "광통신": "45301010",
    "산업용 프린터": "45302010", "원자현미경": "45302010", "키오스크": "45302010", "하이테크": "45302010",
    "SSD": "45302010", "나노": "45302010", "서버": "45302010",
    "전기전자": "45302010", "전기/전자": "45302010",
    "Electronic Manufacturing": "45302020", "전기 전자제품": "45302020", "EMS": "45302020",
    "THIN FILM": "45302030", "디스플레이": "45302030", "display": "45302030", "led": "45302030", "광센서": "45302030", "광학솔루션": "45302030", "전고체전지": "45302030", "2차전지": "45302030",
    "이차전지": "45302030", "전지소재": "45302030", "오디오": "45302030",
    "AI Robotics": "45302040", "로봇": "45302040",
    # 50101010 종합 통신 서비스 / 50101020 무선 통신 서비스 / 50201010 영화·엔터테인먼트 / 50201020 인터랙티브 홈 엔터테인먼트 / 50201030 인터랙티브 미디어·서비스
    "기간통신": "50101010", "무선통신": "50101020", "문화서비스": "50201010", "문화관련": "50201010", "드라마": "50201010", "영상": "50201010",
    "게임": "50201020", "멀티미디어": "50201030", "콘텐츠": "50201030",
    # 55101040 독립 전력 생산 / 60101030 주거 REITs / 60102010 부동산 운영·개발
    "재생에너지": "55101040", "원자력": "55101040", "발전": "55101040",
    "부동산투자": "60101030", "리츠": "60101030", "부동산개발": "60102010",
}
# industry 자유표현에서 제거할 접미사(정규화 후 키워드 매칭).
_INDUSTRY_SUFFIX_RE = (" 시장", " 산업", " 사업", " 제조업", " 제조", "업")

# 비산업(NER 오분류·포괄 분류) — industry 가 아니므로 reject. 접미사 제거 후 전체 일치.
# "제조"/"서비스" 는 부분문자열이 아닌 전체 일치이므로 "철강제조업" 등 정상 산업은 reject 안 함.
_INDUSTRY_NONINDUSTRY_BLACKLIST = frozenset({"공공", "공공기관", "서비스", "제조"})

# GICS 시드에 분류가 없는 한국 특화 산업 → IND_KRX_ 커스텀 노드 매핑(접미사 제거 후 포함 매칭).
# 교육(GICS 2023 25101010 이 자동차부품과 충돌)·농업·어업·사료·가축분뇨처리.
# GICS 키워드 매핑(_INDUSTRY_KEYWORD_TO_GICS)이 먼저 시도되므로 "농약" 등은 GICS 가 선점.
_INDUSTRY_KEYWORD_TO_CUSTOM = {
    "교육": "IND_KRX_EDU_EDUCATION", "스마트러닝": "IND_KRX_EDU_EDUCATION",
    "온라인교육": "IND_KRX_EDU_EDUCATION", "원격교육": "IND_KRX_EDU_EDUCATION",
    "직업능력개발": "IND_KRX_EDU_EDUCATION", "직업훈련": "IND_KRX_EDU_EDUCATION",
    "농업": "IND_KRX_AGR_FARMING", "양돈": "IND_KRX_AGR_FARMING", "축산": "IND_KRX_AGR_FARMING",
    "어업": "IND_KRX_AGR_FISHERY", "양식": "IND_KRX_AGR_FISHERY", "원양어업": "IND_KRX_AGR_FISHERY",
    "사료": "IND_KRX_AGR_FEED", "배합사료": "IND_KRX_AGR_FEED",
    "가축분뇨": "IND_KRX_AGR_LIVESTOCK_WASTE", "액비": "IND_KRX_AGR_LIVESTOCK_WASTE",
}


@dataclass(frozen=True)
class Resolution:
    """단일 raw mention 의 해석 결과."""

    term: str
    node_type: NodeType | None
    canonical_id: str | None
    matched_via: MatchedVia
    status: ResolveStatus
    confidence: float

    @property
    def resolved(self) -> bool:
        return self.canonical_id is not None and self.status == "canonical"


def _strip_company_suffix(name: str) -> str:
    cleaned = name.strip()
    for suf in _COMPANY_SUFFIXES:
        if cleaned.startswith(suf):
            cleaned = cleaned[len(suf) :].strip()
        if cleaned.endswith(suf):
            cleaned = cleaned[: -len(suf)].strip()
    # 괄호/공백 정규화
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    cleaned = " ".join(cleaned.split())
    return cleaned


def _tokenize(s: str) -> set[str]:
    return {tok for tok in s.lower().replace("(", " ").replace(")", " ").split() if tok}


def _token_set_ratio(a: str, b: str) -> float:
    """경량 token-set ratio(0~1). rapidfuzz 의존성 회피용 — 정준 사전 매칭 보조용이지 일반 유사도가 아니다."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    # Jaccard 기반 근사. 겹치는 토큰이 많을수록 1에 수렴.
    union = ta | tb
    return len(inter) / len(union)


def _strip_industry_suffix(term: str) -> str:
    """산업명에서 포괄 접미사( 시장/ 산업/ 사업/ 제조업/ 제조/업)를 하나 제거. 비산업 판정용."""
    t = term.strip()
    for suf in _INDUSTRY_SUFFIX_RE:
        if t.endswith(suf):
            return t[: -len(suf)].strip()
    return t


def _slugify(name: str) -> str:
    """자동 발급 canonical_id 의 slug — 영문·숫자가 있으면 정제, 없으면 안정 해시 8자리.

    이름 기반이므로 같은 이름은 같은 slug → 같은 canonical(출처 stock_code 무관).
    """
    # 영문 알파벳이 하나라도 있으면 ASCII 정제(대문자, 알파벳+숫자+언더스코어).
    if re.search(r"[A-Za-z]", name):
        slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
        slug = re.sub(r"_+", "_", slug)
        if slug:
            return slug[:32]
    # 한글/비ASCII 전용 이름 — 안정 해시(결정론적).
    return hashlib.sha1(name.strip().encode("utf-8")).hexdigest()[:8].upper()


class Normalizer:
    """노드 타입별 정준 해석. ontology 의 YAML 사전 + 산업 매핑을 사용."""

    def __init__(
        self, ontology: BusinessOntology, *, confidence_threshold: float = _CONFIDENCE_THRESHOLD
    ):
        self._ont = ontology
        self._threshold = confidence_threshold
        # 타입별 역색인 — korean/english/alias → canonical_id
        self._by_type: dict[NodeType, dict[str, dict[str, str]]] = {
            "company": self._index_companies(),
            "industry": self._index_industries(),
            "product": self._index_products(),
            "raw_material": self._index_materials(),
            "segment": self._index_segments(),
        }

    def _index_companies(self) -> dict[str, dict[str, str]]:
        korean, english, alias = {}, {}, {}
        for nid, n in self._ont.companies.items():
            korean.setdefault(n.korean_name, nid)
            if n.english_name:
                english.setdefault(n.english_name, nid)
            for a in n.aliases:
                if a:
                    alias.setdefault(a, nid)
        return {"korean_name": korean, "english_name": english, "alias": alias}

    def _index_industries(self) -> dict[str, dict[str, str]]:
        korean, english, alias = {}, {}, {}
        for nid, n in self._ont.industries.items():
            korean.setdefault(n.korean_name, nid)
            if n.english_name:
                english.setdefault(n.english_name, nid)
            for a in n.aliases:
                if a:
                    alias.setdefault(a, nid)
        return {"korean_name": korean, "english_name": english, "alias": alias}

    def _index_products(self) -> dict[str, dict[str, str]]:
        korean, english, alias = {}, {}, {}
        for nid, n in self._ont.products.items():
            korean.setdefault(n.korean_name, nid)
            if n.english_name:
                english.setdefault(n.english_name, nid)
            for a in n.aliases:
                if a:
                    alias.setdefault(a, nid)
        return {"korean_name": korean, "english_name": english, "alias": alias}

    def _index_materials(self) -> dict[str, dict[str, str]]:
        korean, english, alias = {}, {}, {}
        for nid, n in self._ont.materials.items():
            korean.setdefault(n.korean_name, nid)
            if n.english_name:
                english.setdefault(n.english_name, nid)
            for a in n.aliases:
                if a:
                    alias.setdefault(a, nid)
        return {"korean_name": korean, "english_name": english, "alias": alias}

    def _index_segments(self) -> dict[str, dict[str, str]]:
        korean, english, alias = {}, {}, {}
        for nid, n in self._ont.segments.items():
            korean.setdefault(n.korean_name, nid)
            if n.english_name:
                english.setdefault(n.english_name, nid)
            for a in n.aliases:
                if a:
                    alias.setdefault(a, nid)
        return {"korean_name": korean, "english_name": english, "alias": alias}

    def resolve_company(self, name: str) -> Resolution:
        stripped = _strip_company_suffix(name)
        # 비엔티티 NER 오분류(고객/기관 유형) — 회사가 아니므로 reject.
        if (stripped or name) in _COMPANY_NONENTITY_BLACKLIST or name in _COMPANY_NONENTITY_BLACKLIST:
            return Resolution(name, "company", None, "", "rejected", 0.0)
        return self._resolve_typed(stripped or name, "company", prestrip=True)

    def resolve_industry(self, raw: str, standard: str | None = None) -> Resolution:
        if standard in ("dart", "krx", "ksic"):
            node_id = self._ont.by_industry_code.get((standard, raw))
            if node_id:
                return Resolution(
                    term=raw,
                    node_type="industry",
                    canonical_id=node_id,
                    matched_via="industry_code",
                    status="canonical",
                    confidence=1.0,
                )
        # GICS 코드 직접 매칭
        if raw.isdigit():
            node_id = self._ont.by_gics_code.get(raw)
            if node_id:
                return Resolution(
                    term=raw,
                    node_type="industry",
                    canonical_id=node_id,
                    matched_via="gics_code",
                    status="canonical",
                    confidence=1.0,
                )
        # 비산업(공공/서비스/제조 등 포괄 분류) — industry 가 아니므로 reject.
        # 접미사 제거 후 전체 일치(부분문자열 아님) → "철강제조업" 등은 reject 안 함.
        if self._is_nonindustry(raw):
            return Resolution(raw, "industry", None, "", "rejected", 0.0)
        res = self._resolve_typed(raw, "industry")
        if res.resolved:
            return res
        # 정규화 실패 시 자유표현 키워드 → GICS 매핑(긴 키워드 우선 포함 매칭).
        gics = self._keyword_to_gics(raw)
        if gics:
            node_id = self._ont.by_gics_code.get(gics)
            if node_id:
                return Resolution(
                    raw, "industry", node_id, "keyword", "canonical", _AUTO_NEW_CONFIDENCE
                )
        # GICS 시드에 없는 한국 특화 분류(교육·농업·어업·사료) → IND_KRX_ 커스텀 노드.
        custom = self._keyword_to_custom(raw)
        if custom and custom in self._ont.industries:
            return Resolution(
                raw, "industry", custom, "keyword", "canonical", _AUTO_NEW_CONFIDENCE
            )
        return res

    def _keyword_to_gics(self, raw: str) -> str | None:
        """자유표현 산업명에서 키워드 포함 매칭 → GICS 8자리 코드. 긴 키워드 우선, 대소문자 무관."""
        norm = raw.strip().lower()
        for kw in sorted(_INDUSTRY_KEYWORD_TO_GICS, key=len, reverse=True):
            if kw.lower() in norm:
                return _INDUSTRY_KEYWORD_TO_GICS[kw]
        return None

    def _keyword_to_custom(self, raw: str) -> str | None:
        """자유표현 산업명 → IND_KRX_ 커스텀 노드 id. 긴 키워드 우선, 대소문자 무관."""
        norm = raw.strip().lower()
        for kw in sorted(_INDUSTRY_KEYWORD_TO_CUSTOM, key=len, reverse=True):
            if kw.lower() in norm:
                return _INDUSTRY_KEYWORD_TO_CUSTOM[kw]
        return None

    def _is_nonindustry(self, raw: str) -> bool:
        """포괄 분류(공공/서비스/제조) 여부 — 접미사 제거 후 전체 일치로 판정(부분문자열 아님)."""
        term = raw.strip()
        if term in _INDUSTRY_NONINDUSTRY_BLACKLIST:
            return True
        stripped = _strip_industry_suffix(term)
        return stripped in _INDUSTRY_NONINDUSTRY_BLACKLIST

    def auto_canonical_id(self, node_type: NodeType, name: str) -> str:
        """자동 new 발급 canonical_id — 이름 기반 slug. 정적 사전 node_id 충돌 시 접미 _n.

        company(auto_new 제외 타입) 도 서비스가 CorpCodeMap 확인 후 비상장 발급에 사용.
        """
        prefix = _AUTO_NEW_PREFIX[node_type]
        cid = prefix + _slugify(name)
        if cid not in self._ont.node_ids:
            return cid
        base, i = cid, 2
        while cid in self._ont.node_ids:
            cid = f"{base}_{i}"
            i += 1
        return cid

    def resolve_product(self, raw: str) -> Resolution:
        res = self._resolve_typed(raw, "product")
        return res if res.resolved else self._auto_new(raw, "product")

    def resolve_material(self, raw: str) -> Resolution:
        res = self._resolve_typed(raw, "raw_material")
        if res.resolved:
            return res
        # 원재료 사전에 없으면 제품 사전의 is_also_material_id 교차링크 시도(자동 new 보다 우선).
        prod = self._resolve_typed(raw, "product")
        if prod.resolved and prod.canonical_id:
            pnode = self._ont.product(prod.canonical_id)
            if pnode and pnode.is_also_material_id:
                return Resolution(
                    term=raw,
                    node_type="raw_material",
                    canonical_id=pnode.is_also_material_id,
                    matched_via="alias",
                    status="canonical",
                    confidence=prod.confidence,
                )
        return self._auto_new(raw, "raw_material")

    def resolve_segment(self, raw: str) -> Resolution:
        res = self._resolve_typed(raw, "segment")
        return res if res.resolved else self._auto_new(raw, "segment")

    def resolve(self, raw: str, node_type: NodeType, standard: str | None = None) -> Resolution:
        if node_type == "company":
            return self.resolve_company(raw)
        if node_type == "industry":
            return self.resolve_industry(raw, standard=standard)
        if node_type == "product":
            return self.resolve_product(raw)
        if node_type == "raw_material":
            return self.resolve_material(raw)
        if node_type == "segment":
            return self.resolve_segment(raw)
        return Resolution(raw, None, None, "", "unknown", 0.0)

    def resolve_many(
        self, mentions: list[tuple[str, NodeType]], standard: str | None = None
    ) -> list[Resolution]:
        return [self.resolve(raw, nt, standard=standard) for raw, nt in mentions]

    def coverage(self, mentions: list[tuple[str, NodeType]], standard: str | None = None) -> float:
        if not mentions:
            return 0.0
        resolved = sum(1 for r in self.resolve_many(mentions, standard=standard) if r.resolved)
        return resolved / len(mentions)

    def _resolve_typed(
        self, raw: str, node_type: NodeType, *, prestrip: bool = False
    ) -> Resolution:
        idx = self._by_type[node_type]
        term = raw.strip()
        if not term:
            return Resolution(term, node_type, None, "", "unknown", 0.0)

        # 1. ID 직접 매칭
        if node_type == "industry" and term in self._ont.industries:
            return Resolution(term, node_type, term, "id", "canonical", 1.0)
        if node_type == "company" and term in self._ont.companies:
            return Resolution(term, node_type, term, "id", "canonical", 1.0)
        if node_type == "product" and term in self._ont.products:
            return Resolution(term, node_type, term, "id", "canonical", 1.0)
        if node_type == "raw_material" and term in self._ont.materials:
            return Resolution(term, node_type, term, "id", "canonical", 1.0)
        if node_type == "segment" and term in self._ont.segments:
            return Resolution(term, node_type, term, "id", "canonical", 1.0)

        candidates = [term]
        if prestrip:
            # 회사명은 접두/접미사 제거 원본도 후보에 추가(resolve_company 가 이미 strip 했으나 중복 안전).
            candidates.append(_strip_company_suffix(term))

        # 2. 정확 매칭 — korean_name → english_name → alias
        for cand in candidates:
            for via, index in (
                ("korean_name", idx["korean_name"]),
                ("english_name", idx["english_name"]),
                ("alias", idx["alias"]),
            ):
                node_id = index.get(cand)
                if node_id:
                    return Resolution(term, node_type, node_id, via, "canonical", 1.0)

        # 3. 퍼지 매칭 — token-set ratio ≥ 0.9. 사전 키 전수 대상(사전 크기가 작아 허용).
        best_id: str | None = None
        best_score = 0.0
        for cand in candidates:
            for index in (idx["korean_name"], idx["english_name"], idx["alias"]):
                for key, node_id in index.items():
                    score = _token_set_ratio(cand, key)
                    if score > best_score:
                        best_score = score
                        best_id = node_id
        if best_id is not None and best_score >= _FUZZY_THRESHOLD:
            # 퍼지는 threshold 를 채워도 confidence 를 score 로 둬 정확 매칭보다 신뢰도를 낮춘다.
            conf = best_score
            status: ResolveStatus = "canonical" if conf >= self._threshold else "pending_review"
            return Resolution(term, node_type, best_id, "fuzzy", status, conf)

        # 4. 무매치 — pending_review. 자동 new 발급은 resolve_product/segment/material 에서
        #    사전·교차링크 확인 후 수행(원재료는 제품 교차링크 우선).
        return Resolution(term, node_type, None, "", "pending_review", 0.0)

    def _auto_new(self, raw: str, node_type: NodeType) -> Resolution:
        """이름 기반 자동 canonical 발급(회사 고유 엔티티). _AUTO_NEW_TYPES 대상만. 빈 term 은 unknown."""
        if node_type not in _AUTO_NEW_TYPES:
            return Resolution(raw, node_type, None, "", "pending_review", 0.0)
        term = raw.strip()
        if not term:
            return Resolution(raw, node_type, None, "", "unknown", 0.0)
        cid = self.auto_canonical_id(node_type, term)
        return Resolution(raw, node_type, cid, "auto_new", "canonical", _AUTO_NEW_CONFIDENCE)
