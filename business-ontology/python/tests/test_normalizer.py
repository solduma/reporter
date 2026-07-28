from business_ontology import Normalizer, get_ontology


def _norm() -> Normalizer:
    return Normalizer(get_ontology())


def test_resolve_product_by_id():
    r = _norm().resolve_product("PRD_SEMI_DRAM")
    assert r.resolved
    assert r.canonical_id == "PRD_SEMI_DRAM"
    assert r.matched_via == "id"
    assert r.status == "canonical"


def test_resolve_product_by_alias():
    r = _norm().resolve_product("D램")
    assert r.resolved
    assert r.canonical_id == "PRD_SEMI_DRAM"
    assert r.matched_via == "alias"


def test_resolve_product_by_korean_name():
    r = _norm().resolve_product("DRAM")
    assert r.resolved
    assert r.canonical_id == "PRD_SEMI_DRAM"


def test_resolve_company_strips_suffix():
    r = _norm().resolve_company("삼성전자(주)")
    assert r.resolved
    assert r.canonical_id == "CMP_KRX_005930"


def test_resolve_company_english_with_suffix():
    r = _norm().resolve_company("Samsung Electronics Co.,Ltd.")
    assert r.resolved
    assert r.canonical_id == "CMP_KRX_005930"


def test_resolve_company_unknown_is_pending_review():
    r = _norm().resolve_company("존재안하는회사")
    assert not r.resolved
    assert r.status == "pending_review"
    assert r.canonical_id is None


def test_resolve_industry_by_gics_code():
    r = _norm().resolve_industry("45102010")
    assert r.resolved
    assert r.canonical_id == "IND_GICS_45102010"
    assert r.matched_via == "gics_code"


def test_resolve_industry_by_dart_code():
    r = _norm().resolve_industry("C28", standard="dart")
    assert r.resolved
    assert r.canonical_id == "IND_GICS_45102010"
    assert r.matched_via == "industry_code"


def test_resolve_industry_by_krx_code():
    r = _norm().resolve_industry("021", standard="krx")
    assert r.resolved
    assert r.canonical_id == "IND_GICS_45102010"


def test_resolve_industry_by_korean_name():
    r = _norm().resolve_industry("반도체")
    # industries.yaml 의 alias "메모리반도체" 가 아니면 korean_name "대규모 반도체 제조" 매칭 시도
    # 시드에서 "반도체" 가 alias 로 Semiconductors(45102010) 에 있음
    assert r.resolved
    assert r.canonical_id == "IND_GICS_45102010"


def test_resolve_material_by_alias():
    r = _norm().resolve_material("동")
    assert r.resolved
    assert r.canonical_id == "MAT_METAL_COPPER"


def test_resolve_material_cross_link_from_product():
    # 철강제품(PR_STEEL_PRODUCT) is_also_material_id = MAT_METAL_STEEL
    r = _norm().resolve_material("냉연강판")
    assert r.resolved
    assert r.canonical_id == "MAT_METAL_STEEL"


def test_resolve_unknown_returns_pending_review_not_none_id():
    # 사전 미매치 제품은 자동 new 발급(PRD_AUTO_<slug>) — normalizer 근본 개선.
    r = _norm().resolve_product("전혀없는제품XYZ")
    assert r.status == "canonical"
    assert r.canonical_id is not None
    assert r.canonical_id.startswith("PRD_AUTO_")
    assert r.matched_via == "auto_new"
    assert r.confidence == 0.7


def test_resolve_many_and_coverage():
    norm = _norm()
    mentions = [("DRAM", "product"), ("구리", "raw_material"), ("없는것", "product")]
    results = norm.resolve_many(mentions)
    assert len(results) == 3
    assert results[0].resolved
    assert results[1].resolved
    # 없는것도 자동 new 발급 → 해결. coverage 1.0.
    assert results[2].resolved
    cov = norm.coverage(mentions)
    assert cov == 1.0


def test_fuzzy_below_threshold_is_pending():
    # 토큰이 전혀 겹치지 않으면 fuzzy 매칭 실패 → 자동 new 발급(canonical).
    r = _norm().resolve_product("완전다른이름")
    assert r.status == "canonical"
    assert r.matched_via == "auto_new"


def test_empty_term_unknown():
    r = _norm().resolve_product("")
    assert r.status == "unknown"
    assert r.canonical_id is None


def test_confidence_threshold_field():
    norm = Normalizer(get_ontology(), confidence_threshold=0.95)
    # 정확 매칭은 confidence 1.0 이므로 임계치와 무관하게 canonical
    r = norm.resolve_product("DRAM")
    assert r.resolved


# --- 자동 new 발급 + 회사 비엔티티 reject + industry 키워드 매핑(normalizer 근본 개선) ---
def test_auto_new_product_idempotent():
    """같은 이름 → 같은 자동 canonical(출처 무관). 두 번 해석해도 동일 ID."""
    n = _norm()
    r1 = n.resolve_product("Galaxy S24")
    r2 = n.resolve_product("Galaxy S24")
    assert r1.canonical_id == r2.canonical_id == "PRD_AUTO_GALAXY_S24"
    assert r1.matched_via == "auto_new" and r1.status == "canonical"


def test_auto_new_material_falls_back_when_no_cross_link():
    """제품 교차링크 없는 미매치 원재료 → 자동 new 발급(MAT_AUTO_)."""
    r = _norm().resolve_material("회사고유원재료XYZ")
    assert r.status == "canonical"
    assert r.canonical_id.startswith("MAT_AUTO_")


def test_auto_new_segment():
    r = _norm().resolve_segment("회사고유부문XYZ")
    assert r.status == "canonical"
    assert r.canonical_id.startswith("SEG_AUTO_")


def test_company_blacklist_rejected():
    """회사 NER 오분류(병ㆍ의원) → rejected."""
    for bad in ("병ㆍ의원", "병의원", "약국", "환자", "소비자", "고객"):
        r = _norm().resolve_company(bad)
        assert r.status == "rejected", bad
        assert r.canonical_id is None


def test_company_auto_new_not_in_package():
    """패키지 normalizer 는 회사 자동 new 를 발급하지 않는다(상장사 CMP_KRX_ 회귀 방지).
    서비스(resolve_company)가 CorpCodeMap 확인 후 비상장 CMP_GLOBAL_ 발급 주도.
    """
    r = _norm().resolve_company("존재안하는주식회사")
    assert r.status == "pending_review"
    assert r.canonical_id is None


def test_industry_keyword_mapping():
    """자유표현 산업명 → GICS 키워드 매핑."""
    n = _norm()
    assert n.resolve_industry("메모리 반도체 시장").canonical_id == "IND_GICS_45102010"
    assert n.resolve_industry("Foundry 시장").canonical_id == "IND_GICS_45102010"
    assert n.resolve_industry("방산").canonical_id == "IND_GICS_20101010"
    assert n.resolve_industry("철도산업").canonical_id == "IND_GICS_20108040"
    assert n.resolve_industry("산업용 로봇 제조업").canonical_id == "IND_GICS_45302040"


def test_industry_keyword_no_match_stays_pending():
    """매핑 키워드 없는 자유표현 → pending 유지(자동 new 안 함)."""
    r = _norm().resolve_industry("글로벌 전자 기업")
    assert r.status == "pending_review"
    assert r.canonical_id is None


def test_auto_canonical_id_collision_avoidance():
    """정적 사전 node_id 와 충돌 시 접미 _n 회피."""
    n = _norm()
    # DRAM 은 정적 사전 PRD_SEMI_DRAM 이 있으므로 auto_canonical_id 가 충돌 회피.
    cid = n.auto_canonical_id("product", "DRAM")
    assert cid != "PRD_SEMI_DRAM"
    assert cid.startswith("PRD_")
