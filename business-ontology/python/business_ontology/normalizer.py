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
# GICS 코드는 시드 industries(2023 sub-industry 128)에 존재하는 코드만 사용 — 존재 검증 필요.
_INDUSTRY_KEYWORD_TO_GICS = {
    "반도체": "45102010",
    "메모리": "45102010",
    "파운드리": "45102010",
    "foundry": "45102010",
    "디스플레이 장비": "45101010",
    "디스플레이": "45302030",
    "이차전지": "45302030",
    "스마트폰": "45301010",
    "풍력": "45101020",
    "핵융합": "45101020",
    "의약품원료": "35201010",
    "완제의약": "35201010",
    "의약품제조": "35201010",
    "신약": "35202010",
    "의료정보": "35103010",
    "의료기기": "35101010",
    "내시경": "35101010",
    "전장": "25101010",
    "방산": "20101010",
    "방위": "20101010",
    "무인항공": "20101010",
    "드론": "20101010",
    "로봇": "45302040",
    "금융": "40101010",
    "철도": "20108040",
    "무선통신": "50101020",
    "전자유도가열": "20105010",
    "특수전원": "20105010",
    "화장품": "30302010",
}
# industry 자유표현에서 제거할 접미사(정규화 후 키워드 매칭).
_INDUSTRY_SUFFIX_RE = (" 시장", " 산업", " 사업", " 제조업", " 제조", "업")


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
        return res

    def _keyword_to_gics(self, raw: str) -> str | None:
        """자유표현 산업명에서 키워드 포함 매칭 → GICS 8자리 코드. 긴 키워드 우선, 대소문자 무관."""
        norm = raw.strip().lower()
        for kw in sorted(_INDUSTRY_KEYWORD_TO_GICS, key=len, reverse=True):
            if kw.lower() in norm:
                return _INDUSTRY_KEYWORD_TO_GICS[kw]
        return None

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
