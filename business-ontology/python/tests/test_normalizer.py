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
    r = _norm().resolve_product("전혀없는제품XYZ")
    assert r.status == "pending_review"
    assert r.canonical_id is None
    assert r.confidence == 0.0


def test_resolve_many_and_coverage():
    norm = _norm()
    mentions = [("DRAM", "product"), ("구리", "raw_material"), ("없는것", "product")]
    results = norm.resolve_many(mentions)
    assert len(results) == 3
    assert results[0].resolved
    assert results[1].resolved
    assert not results[2].resolved
    cov = norm.coverage(mentions)
    assert 0.0 < cov < 1.0


def test_fuzzy_below_threshold_is_pending():
    # 토큰이 전혀 겹치지 않으면 fuzzy 점수 0 → pending_review
    r = _norm().resolve_product("완전다른이름")
    assert r.status == "pending_review"


def test_empty_term_unknown():
    r = _norm().resolve_product("")
    assert r.status == "unknown"
    assert r.canonical_id is None


def test_confidence_threshold_field():
    norm = Normalizer(get_ontology(), confidence_threshold=0.95)
    # 정확 매칭은 confidence 1.0 이므로 임계치와 무관하게 canonical
    r = norm.resolve_product("DRAM")
    assert r.resolved
