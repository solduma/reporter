from business_ontology import BusinessOntology, get_ontology


def test_get_ontology_returns_business_ontology():
    ont = get_ontology()
    assert isinstance(ont, BusinessOntology)


def test_get_ontology_cached():
    a = get_ontology()
    b = get_ontology()
    assert a is b


def test_industries_loaded():
    ont = get_ontology()
    # GICS 시드(128) + KRX 커스텀(교육·농업·어업·사료 등) ≥ 100
    assert len(ont.industries) >= 100
    gics = [n for n in ont.industries.values() if n.gics_sector]
    sectors = {n.gics_sector for n in gics}
    groups = {n.gics_group for n in gics}
    assert len(sectors) == 11
    # GICS 그룹 수는 에디션별로 24~25 — 시드는 24개(구조 유효, 확장 가능).
    assert len(groups) >= 24


def test_industry_gics_code_shape():
    ont = get_ontology()
    for nid, n in ont.industries.items():
        if nid.startswith("IND_GICS_"):
            assert nid == f"IND_GICS_{n.gics_sub_industry}"
            assert len(n.gics_sub_industry) == 8
            assert n.gics_sector == n.gics_sub_industry[:2]
            assert n.gics_group == n.gics_sub_industry[:4]
            assert n.gics_industry == n.gics_sub_industry[:6]
            assert n.code == ""
        else:  # IND_KRX_ 커스텀 — gics_* 빈 문자열, code 필수.
            assert nid.startswith("IND_KRX_")
            assert n.gics_sub_industry == ""
            assert n.code


def test_products_and_materials_loaded():
    ont = get_ontology()
    assert "PRD_SEMI_DRAM" in ont.products
    assert "MAT_METAL_COPPER" in ont.materials


def test_is_also_material_cross_link_valid():
    ont = get_ontology()
    for p in ont.products.values():
        if p.is_also_material_id:
            assert p.is_also_material_id in ont.materials, (
                f"{p.id}.is_also_material_id → {p.is_also_material_id} 가 materials 에 없음"
            )


def test_companies_seed_loaded():
    ont = get_ontology()
    assert "CMP_KRX_005930" in ont.companies
    samsung = ont.companies["CMP_KRX_005930"]
    assert samsung.corp_code == "00126380"
    assert samsung.stock_code == "005930"


def test_by_gics_code_index():
    ont = get_ontology()
    assert ont.by_gics_code.get("45102010") == "IND_GICS_45102010"


def test_by_industry_code_mapping():
    ont = get_ontology()
    # krx_to_gics: "021" → IND_GICS_45102010
    assert ont.by_industry_code.get(("krx", "021")) == "IND_GICS_45102010"
    # dart_industry: "C28" → IND_GICS_45102010
    assert ont.by_industry_code.get(("dart", "C28")) == "IND_GICS_45102010"


def test_edge_types_loaded():
    ont = get_ontology()
    assert "manufactures" in ont.edge_types
    assert "part_of_value_chain" in ont.edge_types
    assert ont.edge_types["competes_with"].directed is False


def test_node_ids_aggregate():
    ont = get_ontology()
    ids = ont.node_ids
    assert "IND_GICS_45102010" in ids
    assert "PRD_SEMI_DRAM" in ids
    assert "CMP_KRX_005930" in ids
