from business_ontology import Edge, Graph, get_ontology


def _graph() -> Graph:
    ont = get_ontology()
    edges = [
        Edge(src="CMP_KRX_005930", dst="IND_GICS_45102010", edge_type="operates_in"),
        Edge(
            src="CMP_KRX_005930",
            dst="PRD_SEMI_DRAM",
            edge_type="manufactures",
            share=0.42,
            period="2024.12",
        ),
        Edge(src="CMP_KRX_005930", dst="PRD_SEMI_NAND", edge_type="manufactures", share=0.18),
        Edge(src="CMP_KRX_005930", dst="MAT_SEMI_SILICON_WAFER", edge_type="uses_material"),
        Edge(
            src="CMP_KRX_005930",
            dst="PRD_SEMI_DRAM",
            edge_type="part_of_value_chain",
            chain_stage="operations",
        ),
        Edge(src="CMP_KRX_005930", dst="SEG_REGION_OVERSEAS", edge_type="has_segment", share=0.68),
        Edge(src="CMP_KRX_006660", dst="CMP_KRX_005930", edge_type="competes_with"),
        Edge(src="CMP_KRX_005930", dst="CMP_KRX_000660", edge_type="competes_with"),
        Edge(src="CMP_KRX_000660", dst="CMP_KRX_005930", edge_type="supplies_to", share=0.1),
    ]
    return Graph(ont, edges)


def test_out_edges_by_type():
    g = _graph()
    manuf = g.out_edges("CMP_KRX_005930", "manufactures")
    assert {e.dst for e in manuf} == {"PRD_SEMI_DRAM", "PRD_SEMI_NAND"}


def test_manufactures_returns_product_ids():
    g = _graph()
    prods = g.manufactures("CMP_KRX_005930")
    ids = {pid for _, pid in prods}
    assert "PRD_SEMI_DRAM" in ids


def test_materials_of():
    g = _graph()
    mats = g.materials_of("CMP_KRX_005930")
    assert mats[0][1] == "MAT_SEMI_SILICON_WAFER"


def test_operates_in():
    g = _graph()
    inds = g.operates_in("CMP_KRX_005930")
    assert inds[0][1] == "IND_GICS_45102010"


def test_competitors_bidirectional():
    g = _graph()
    comps = g.competitors("CMP_KRX_005930")
    # out 엣지(→000660) 와 in 엣지(006660→) 모두 수집
    assert "CMP_KRX_000660" in comps
    assert "CMP_KRX_006660" in comps


def test_suppliers_via_in_edges():
    g = _graph()
    # supplies_to 000660→005930 → 005930 의 supplier 는 000660
    sup = g.suppliers("CMP_KRX_005930")
    assert "CMP_KRX_000660" in {s for _, s in sup}


def test_value_chain_grouped_by_stage():
    g = _graph()
    vc = g.value_chain("CMP_KRX_005930")
    assert "operations" in vc
    assert vc["operations"][0].dst == "PRD_SEMI_DRAM"


def test_peers_by_industry():
    g = _graph()
    # 005930 operates_in 45102010; 다른 회사가 같은 industry 에 operates_in 하면 peer.
    # 예시에 005930 만 있으므로 peer 는 비어야 한다.
    assert g.peers_by_industry("CMP_KRX_005930") == []


def test_neighbors_filtered():
    g = _graph()
    nb = g.neighbors("CMP_KRX_005930", "manufactures")
    assert set(nb) == {"PRD_SEMI_DRAM", "PRD_SEMI_NAND"}


def test_empty_graph():
    g = Graph(get_ontology(), [])
    assert g.edges == []
    assert g.manufactures("CMP_KRX_005930") == []
