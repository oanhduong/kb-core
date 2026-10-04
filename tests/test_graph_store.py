"""Test graph/store.py: escape Cypher (an toàn injection/dollar-quote), MemoryGraphStore, và integration
AgeGraphStore trên Postgres + Apache AGE THẬT khi đặt RETAILKB_TEST_PG_DSN (bỏ qua nếu không có)."""
import os
import uuid

import pytest

from graph.model import GraphAccumulator
from graph.store import (
    AgeGraphStore,
    GraphUnavailable,
    MemoryGraphStore,
    NodeRef,
    _cypher_literal,
    _parse_agtype,
    _validate_identifier,
)


@pytest.mark.parametrize("name", ["SKU", "Category", "BELONGS_TO", "retail_kb"])
def test_validate_identifier_accepts_safe_names(name):
    assert _validate_identifier(name, "Label") == name


@pytest.mark.parametrize("name", ["SKU) DETACH DELETE (n", "a-b", "", "1abc", "x;y"])
def test_validate_identifier_rejects_unsafe_names(name):
    with pytest.raises(ValueError):
        _validate_identifier(name, "Label")


def test_cypher_literal_escapes_quotes_backslash_newline_and_dollar():
    assert _cypher_literal("Táo's") == "'Táo\\'s'"
    assert _cypher_literal("a\\b") == "'a\\\\b'"
    assert _cypher_literal("dòng 1\ndòng 2") == "'dòng 1\\ndòng 2'"
    # '$' không được xuất hiện thô — sẽ phá dollar-quote $cy$ ... $cy$ bao quanh câu Cypher
    lit = _cypher_literal("giá $cy$ MATCH (n) DETACH DELETE n $cy$")
    assert "$" not in lit and "\\u0024" in lit


def test_cypher_literal_scalars_lists_maps():
    assert _cypher_literal(5) == "5"
    assert _cypher_literal(1.5) == "1.5"
    assert _cypher_literal(True) == "true"
    assert _cypher_literal(None) == "null"
    assert _cypher_literal(float("nan")) == "null"
    assert _cypher_literal(["a", 1]) == "['a', 1]"
    assert _cypher_literal({"id": "A1", "price": 100}) == "{id: 'A1', price: 100}"
    with pytest.raises(ValueError):
        _cypher_literal({"bad key": 1})


def test_parse_agtype_strips_type_suffixes():
    assert _parse_agtype('{"id": 1, "label": "SKU", "properties": {}}::vertex') == {"id": 1, "label": "SKU", "properties": {}}
    assert _parse_agtype('[1, "x"]') == [1, "x"]


# =====================================================================================
# MemoryGraphStore
# =====================================================================================

def _acc(doc_id, nodes, edges):
    acc = GraphAccumulator(document_id=doc_id)
    for label, nid, props, src in nodes:
        acc.add_node(label, nid, props, src)
    for etype, fl, fid, tl, tid, src in edges:
        acc.add_edge(etype, fl, fid, tl, tid, {}, src)
    return acc


def test_memory_store_merges_sources_from_multiple_documents():
    s = MemoryGraphStore()
    s.write_accumulator(_acc("D1", [("SKU", "A1", {"price": 10}, ["D1:r1"])], []))
    s.write_accumulator(_acc("D2", [("SKU", "A1", {"price": 12}, ["D2:r9", "D1:r1"])], []))
    n = s.get_node("SKU", "A1")
    assert n.props["price"] == 12
    assert n.props["source_chunk_ids"] == ["D1:r1", "D2:r9"]
    assert n.props["source_docs"] == ["D1", "D2"]


def test_memory_store_remove_document_only_strips_that_document():
    s = MemoryGraphStore()
    s.write_accumulator(_acc("D1", [("SKU", "A1", {}, ["D1:r1"]), ("Category", "C", {}, ["D1:r1"])],
                             [("BELONGS_TO", "SKU", "A1", "Category", "C", ["D1:r1"])]))
    s.write_accumulator(_acc("D2", [("SKU", "A1", {}, ["D2:r2"])], []))
    out = s.remove_document("D1")
    assert out["removed_edges"] == 1
    assert s.get_node("Category", "C") is None
    assert s.get_node("SKU", "A1").props["source_chunk_ids"] == ["D2:r2"]


def test_memory_store_paths_are_bounded_and_acyclic():
    s = MemoryGraphStore()
    s.write_accumulator(_acc("D", [("SKU", "A", {}, ["D:1"]), ("Category", "C", {}, ["D:1"]), ("Policy", "P", {}, ["D:2"])],
                             [("BELONGS_TO", "SKU", "A", "Category", "C", ["D:1"]), ("GOVERNS", "Policy", "P", "Category", "C", ["D:2"])]))
    paths = s.paths_from([NodeRef("SKU", "A")], 2, limit=100)
    assert {tuple(n.id for n in p.nodes) for p in paths} == {("A", "C"), ("A", "C", "P")}


# =====================================================================================
# Integration: Apache AGE thật
# =====================================================================================

TEST_DSN = os.environ.get("RETAILKB_TEST_PG_DSN")
age = pytest.mark.skipif(not TEST_DSN, reason="cần RETAILKB_TEST_PG_DSN trỏ tới Postgres có Apache AGE")


@pytest.fixture
def age_store():
    import psycopg

    name = f"t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS age")
    store = AgeGraphStore(TEST_DSN, graph_name=name)
    store.ensure()
    yield store
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute("LOAD 'age'")
        conn.execute("SELECT ag_catalog.drop_graph(%s, true)", [name])


@age
def test_age_store_merge_remove_paths_and_stats(age_store):
    s = age_store
    s.write_accumulator(_acc("D1", [("SKU", "ĐT-018", {"name": "Loa $$ 30W 'x'", "price": 10}, ["D1:r1"]),
                                    ("Category", "Điện tử", {}, ["D1:r1"])],
                             [("BELONGS_TO", "SKU", "ĐT-018", "Category", "Điện tử", ["D1:r1"])]))
    s.write_accumulator(_acc("D2", [("Policy", "p#dieu-5", {"level": "article"}, ["D2:a5"])],
                             [("GOVERNS", "Policy", "p#dieu-5", "Category", "Điện tử", ["D2:a5"])]))
    node = s.get_node("SKU", "ĐT-018")
    assert node.props["name"] == "Loa $$ 30W 'x'"
    paths = s.paths_from([NodeRef("SKU", "ĐT-018")], 2, limit=50)
    assert {tuple(n.id for n in p.nodes) for p in paths} == {("ĐT-018", "Điện tử"), ("ĐT-018", "Điện tử", "p#dieu-5")}
    governs = [p for p in paths if p.length == 2][0].edges[1]
    assert governs.type == "GOVERNS" and governs.src.id == "p#dieu-5" and governs.dst.id == "Điện tử"
    assert s.stats()["edge_count"] == 2

    out = s.remove_document("D2")
    assert out["removed_edges"] == 1 and out["removed_nodes"] == 1
    assert s.get_node("Policy", "p#dieu-5") is None
    assert s.get_node("SKU", "ĐT-018") is not None
    assert len(s.edges_of_type("BELONGS_TO")) == 1
    assert s.list_nodes("Supplier") == []  # label chưa tồn tại -> rỗng, không lỗi


def test_age_store_unreachable_raises_graph_unavailable():
    s = AgeGraphStore("postgresql://nobody@127.0.0.1:1/none", connect_timeout=1)
    with pytest.raises(GraphUnavailable):
        s.stats()
