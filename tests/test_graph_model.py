"""Test cho graph/model.py::GraphAccumulator — gộp idempotent theo (label,id)/(type,from,to)."""
from graph.model import GraphAccumulator


def test_add_node_creates_new_node_with_properties_and_source_chunk_ids():
    acc = GraphAccumulator()
    acc.add_node("SKU", "A1", {"name": "Táo"}, source_chunk_ids=["f.xlsx::Sheet1!A2"])

    nodes = acc.node_list()
    assert len(nodes) == 1
    assert nodes[0] == {
        "label": "SKU", "id": "A1", "properties": {"name": "Táo"},
        "source_chunk_ids": ["f.xlsx::Sheet1!A2"], "source_docs": [],
    }


def test_add_node_same_key_twice_merges_instead_of_duplicating():
    acc = GraphAccumulator()
    acc.add_node("SKU", "A1", {"name": "Táo"}, source_chunk_ids=["c1"])
    acc.add_node("SKU", "A1", {"price": 1000}, source_chunk_ids=["c2"])

    nodes = acc.node_list()
    assert len(nodes) == 1
    assert nodes[0]["properties"] == {"name": "Táo", "price": 1000}
    assert nodes[0]["source_chunk_ids"] == ["c1", "c2"]


def test_add_node_source_chunk_ids_deduplicated():
    acc = GraphAccumulator()
    acc.add_node("SKU", "A1", {}, source_chunk_ids=["c1"])
    acc.add_node("SKU", "A1", {}, source_chunk_ids=["c1", "c2"])

    assert acc.node_list()[0]["source_chunk_ids"] == ["c1", "c2"]


def test_different_labels_same_id_are_different_nodes():
    acc = GraphAccumulator()
    acc.add_node("SKU", "X", {})
    acc.add_node("Category", "X", {})
    assert len(acc.node_list()) == 2


def test_add_edge_creates_new_edge():
    acc = GraphAccumulator()
    acc.add_edge("BELONGS_TO", "SKU", "A1", "Category", "Thực phẩm", {}, source_chunk_ids=["c1"])

    edges = acc.edge_list()
    assert len(edges) == 1
    assert edges[0]["type"] == "BELONGS_TO"
    assert edges[0]["from_id"] == "A1" and edges[0]["to_id"] == "Thực phẩm"
    assert edges[0]["source_chunk_ids"] == ["c1"]


def test_add_edge_same_endpoints_and_type_merges_not_duplicates():
    acc = GraphAccumulator()
    acc.add_edge("STOCKS", "Store", "S1", "SKU", "A1", {"ton_kho": 5}, source_chunk_ids=["c1"])
    acc.add_edge("STOCKS", "Store", "S1", "SKU", "A1", {"ton_kho": 7}, source_chunk_ids=["c2"])

    edges = acc.edge_list()
    assert len(edges) == 1
    assert edges[0]["properties"] == {"ton_kho": 7}
    assert edges[0]["source_chunk_ids"] == ["c1", "c2"]


def test_different_edge_types_between_same_nodes_are_distinct_edges():
    acc = GraphAccumulator()
    acc.add_edge("REFERENCES", "Policy", "P1", "Policy", "P2", {})
    acc.add_edge("HAS_EXCEPTION_FOR", "Policy", "P1", "Policy", "P2", {})
    assert len(acc.edge_list()) == 2


def test_stats_counts_nodes_and_edges_by_label_and_type():
    acc = GraphAccumulator()
    acc.add_node("SKU", "A1", {})
    acc.add_node("SKU", "A2", {})
    acc.add_node("Category", "Thực phẩm", {})
    acc.add_edge("BELONGS_TO", "SKU", "A1", "Category", "Thực phẩm", {})
    acc.add_edge("BELONGS_TO", "SKU", "A2", "Category", "Thực phẩm", {})

    stats = acc.stats()
    assert stats["node_count"] == 3
    assert stats["edge_count"] == 2
    assert stats["node_count_by_label"] == {"SKU": 2, "Category": 1}
    assert stats["edge_count_by_type"] == {"BELONGS_TO": 2}


def test_document_id_is_attached_as_source_docs_for_incremental_updates():
    acc = GraphAccumulator(document_id="doc1")
    acc.add_node("SKU", "A1", {}, ["doc1:Sheet!A2"])
    acc.add_edge("BELONGS_TO", "SKU", "A1", "Category", "X", {}, ["doc1:Sheet!A2"])
    assert acc.node_list()[0]["source_docs"] == ["doc1"]
    assert acc.edge_list()[0]["source_docs"] == ["doc1"]
