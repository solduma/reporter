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


def test_industry_keyword_expanded_mapping():
    """확장 키워드 테이블 — 292 잔여 자유표현 중 시드 GICS 가 있는 분야 대표 케이스.
    시드에 GICS 없는 분류(교육·농업·양식)·비산업(공공/서비스업)은 매핑 제외(아래 None 케이스).
    """
    n = _norm()
    cases = {
        "정유": "IND_GICS_10205030",
        "석유화학 산업": "IND_GICS_15101010",
        "조선": "IND_GICS_20108030",
        "섬유 산업": "IND_GICS_30302020",
        "식품산업": "IND_GICS_30201010",
        "건강기능식품의 제조 및 판매업": "IND_GICS_30201010",
        "통신판매업": "IND_GICS_25501030",
        "전기전자산업": "IND_GICS_45302010",  # "전자" 광범 키워드 대신 "전기전자" 로 매핑
    }
    for raw, expected in cases.items():
        r = n.resolve_industry(raw)
        assert r.canonical_id == expected, f"{raw!r} 기대 {expected}, 실제 {r.canonical_id}"


def test_industry_custom_namespace_mapping():
    """GICS 시드에 분류가 없는 한국 특화 산업 → IND_KRX_ 커스텀 노드 매핑.
    교육(GICS 2023 25101010 이 자동차부품과 충돌)·농업·어업·사료·가축분뇨처리.
    """
    n = _norm()
    cases = {
        "교육": "IND_KRX_EDU_EDUCATION",
        "교육사업": "IND_KRX_EDU_EDUCATION",
        "종합 교육": "IND_KRX_EDU_EDUCATION",
        "스마트러닝": "IND_KRX_EDU_EDUCATION",
        "온라인교육서비스업": "IND_KRX_EDU_EDUCATION",
        "원격교육사업": "IND_KRX_EDU_EDUCATION",
        "농업경영": "IND_KRX_AGR_FARMING",
        "양돈 산업": "IND_KRX_AGR_FARMING",
        "양돈산업": "IND_KRX_AGR_FARMING",
        "원양어업": "IND_KRX_AGR_FISHERY",
        "동물용 사료 제조 및 판매업": "IND_KRX_AGR_FEED",
        "배합사료업": "IND_KRX_AGR_FEED",
        "가축분뇨 수거 및 기능성액비생산": "IND_KRX_AGR_LIVESTOCK_WASTE",
    }
    for raw, expected in cases.items():
        r = n.resolve_industry(raw)
        assert r.resolved, f"{raw!r} 미해결: {r.status}"
        assert r.canonical_id == expected, f"{raw!r} 기대 {expected}, 실제 {r.canonical_id}"


def test_industry_custom_not_override_gics():
    """커스텀 키워드가 GICS 매핑을 덮어쓰지 않는다 — '농약'은 GICS 비료·농약, '농업'은 커스텀.
    '철강제조업'·'자동차부품제조' 는 비산업 reject(제조) 의 부분문자열 오매칭 없이 GICS 유지.
    """
    n = _norm()
    assert n.resolve_industry("농약제조 판매업").canonical_id == "IND_GICS_15101030"
    assert n.resolve_industry("농업경영").canonical_id == "IND_KRX_AGR_FARMING"
    assert n.resolve_industry("철강제조업(전절단가공)").canonical_id == "IND_GICS_15104050"
    assert n.resolve_industry("자동차부품제조").canonical_id == "IND_GICS_25101010"


def test_industry_nonindustry_rejected():
    """비산업(공공/서비스/제조 포괄 분류) — industry 가 아니므로 reject. 전체 일치라 정상 산업은 안 됨."""
    n = _norm()
    for bad in ("공공", "공공기관", "서비스 산업", "서비스업", "제조", "제조업"):
        r = n.resolve_industry(bad)
        assert r.status == "rejected", f"{bad!r} 기대 rejected, 실제 {r.status}({r.canonical_id})"
        assert r.canonical_id is None


def test_industry_ambiguous_stays_pending():
    """모호 약어(AM/ET)·회사(글로벌 전자 기업)·'전자' 단독 — 매핑 없이 pending(HITL)."""
    n = _norm()
    for raw in ("AM사업", "ET", "글로벌 전자 기업", "전자"):
        r = n.resolve_industry(raw)
        assert r.status == "pending_review", f"{raw!r} 기대 pending, 실제 {r.status}({r.canonical_id})"
        assert r.canonical_id is None


def test_industry_llm_none_residual_mapping():
    """LLM-NONE 잔여 13건 중 매핑 가능 9건 키워드 승격 — GICS 8건 + 커스텀 교육 1건.
    나머지 4건(렌탈사업·정보통신·2차 산업·UV)은 모호해 HITL pending 잔류.
    """
    n = _norm()
    cases = {
        "메탈로센촉매": "IND_GICS_15101020",  # 촉매 → 특수 화학
        "세라믹 제품의 제조, 혼합 및 판매업": "IND_GICS_15104020",  # 세라믹(스 없이) → 다목적 금속(세라믹스와 동일)
        "항법": "IND_GICS_20101010",  # 항법 → 항공·국방
        "프레스금형 제조판매업": "IND_GICS_20106020",  # 프레스금형/금형 → 산업 기계·부품
        "기타 프레스제품 제조업": "IND_GICS_20106020",  # 프레스제품 → 산업 기계·부품
        "Display": "IND_GICS_45302030",  # display(영문) → 전자 부품
        "LED": "IND_GICS_45302030",  # led → 전자 부품
        "광센서": "IND_GICS_45302030",  # 광센서 → 전자 부품
        "직업능력개발훈련사업": "IND_KRX_EDU_EDUCATION",  # 직업능력개발/직업훈련 → 커스텀 교육
    }
    for raw, expected in cases.items():
        r = n.resolve_industry(raw)
        assert r.resolved, f"{raw!r} 미해결: {r.status}"
        assert r.canonical_id == expected, f"{raw!r} 기대 {expected}, 실제 {r.canonical_id}"
    # 모호 4건은 pending 잔류(과매칭 회피 — 렌탈=비즈니스모델, 정보통신=서비스/장비 모호, 2차 산업=광범 부문, UV=경화/화장품/인쇄 모호).
    for raw in ("렌탈사업", "정보통신", "2차 산업", "UV"):
        r = n.resolve_industry(raw)
        assert r.status == "pending_review", f"{raw!r} 기대 pending, 실제 {r.status}({r.canonical_id})"
        assert r.canonical_id is None


def test_industry_ingest_residual_mapping():
    """신규 ingest 잔여(LG전자 066570 사업보고서) 키워드 승격.
    기간통신사업→종합 통신 서비스(50101010), 광학솔루션사업→전자 부품(45302030).
    '기간통신'은 통신 장비(45301010)·통신 판매(25501030)와 구분되어 서비스(5010)로,
    '광학솔루션'은 '광학' 단독 과매칭 회피용 구체 키워드.
    """
    n = _norm()
    cases = {
        "기간통신사업": "IND_GICS_50101010",
        "광학솔루션사업": "IND_GICS_45302030",
    }
    for raw, expected in cases.items():
        r = n.resolve_industry(raw)
        assert r.resolved, f"{raw!r} 미해결: {r.status}"
        assert r.canonical_id == expected, f"{raw!r} 기대 {expected}, 실제 {r.canonical_id}"


def test_auto_canonical_id_collision_avoidance():
    """정적 사전 node_id 와 충돌 시 접미 _n 회피."""
    n = _norm()
    # DRAM 은 정적 사전 PRD_SEMI_DRAM 이 있으므로 auto_canonical_id 가 충돌 회피.
    cid = n.auto_canonical_id("product", "DRAM")
    assert cid != "PRD_SEMI_DRAM"
    assert cid.startswith("PRD_")
