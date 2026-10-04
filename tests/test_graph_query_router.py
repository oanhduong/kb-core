"""Test graph/query.py (A4 local search + aggregation) và graph/router.py (A5, phần rule) trên graph
dựng từ data/raw thật trong MemoryGraphStore — không cần DB/LLM."""
import pytest

from graph.build import DEFAULT_DATA_DIR, build_graph
from graph.query import GraphSearcher, explain_path
from graph.resolve import EntityMention, EntityResolver
from graph.router import QuestionRouter, classify_by_rules
from graph.store import MemoryGraphStore


@pytest.fixture(scope="module")
def store():
    acc, _ = build_graph(DEFAULT_DATA_DIR)
    s = MemoryGraphStore()
    s.write_accumulator(acc)
    return s


@pytest.fixture(scope="module")
def searcher(store):
    nodes = {label: [n.props for n in store.list_nodes(label)]
             for label in ("SKU", "Category", "Store", "Promotion", "Policy", "Supplier")}
    return GraphSearcher(store, EntityResolver(nodes), llm=None)


# =====================================================================================
# Router — rule
# =====================================================================================

def _m(label, nid, method="exact"):
    return EntityMention(label, nid, nid, method)


@pytest.mark.parametrize("question,mentions,expected", [
    ("Giá của ĐT-018 bao nhiêu?", [_m("SKU", "ĐT-018")], "lookup"),           # "giá bao nhiêu" = giá trị, không phải đếm
    ("Thời hạn đổi trả là bao nhiêu ngày?", [], "lookup"),
    ("Có bao nhiêu cửa hàng sắp hết hàng ĐT-018?", [_m("SKU", "ĐT-018")], "aggregation"),
    ("Liệt kê các khuyến mãi tháng 7", [], "aggregation"),
    ("Những sản phẩm nào thuộc ngành Điện tử đang sắp hết?", [_m("Category", "Điện tử")], "aggregation"),
    ("ĐT-018 có được áp dụng Tuần lễ điện máy xanh không?",
     [_m("SKU", "ĐT-018"), _m("Promotion", "Tuần lễ điện máy xanh")], "multi_hop"),
    ("Chính sách nào áp dụng cho ngành Điện tử?", [_m("Category", "Điện tử")], "multi_hop"),
])
def test_classify_by_rules(question, mentions, expected):
    qtype, conf, reasons = classify_by_rules(question, mentions)
    assert qtype == expected
    assert 0 < conf <= 1 and reasons


def test_router_routes_lookup_to_vector_and_multihop_to_graph(searcher):
    router = QuestionRouter(searcher=searcher)
    d1 = router.route("Giá của ĐT-018 là bao nhiêu?")
    assert (d1.question_type, d1.route, d1.method) == ("lookup", "vector", "rule")
    d2 = router.route("Loa ĐT-018 thuộc ngành hàng nào và chính sách đổi trả hàng hóa nào áp dụng?")
    assert d2.question_type == "multi_hop" and d2.route == "graph"


def test_router_disabled_graph_sends_everything_to_vector(searcher):
    from core.config import get_config

    cfg = get_config().model_copy(deep=True)
    cfg.router.graph_enabled = False
    d = QuestionRouter(cfg=cfg, searcher=searcher).route("Có bao nhiêu cửa hàng sắp hết hàng ĐT-018?")
    assert d.route == "vector"


# =====================================================================================
# Local search
# =====================================================================================

def test_local_search_sku_to_promotion_via_category(searcher):
    q = "Loa ĐT-018 đang được áp dụng khuyến mãi nào?"
    mentions, method = searcher.extract_entities(q, allow_llm=False)
    assert method == "rule" and ("SKU", "ĐT-018") in {(m.label, m.id) for m in mentions}
    res = searcher.local_search(q, mentions)
    assert res.record_ids and res.record_ids[0].startswith("01_bang_gia_sku:")  # dòng bảng giá của chính SKU
    top_ends = {p.path.nodes[-1].label for p in res.paths[:10]}
    assert "Promotion" in top_ends  # câu hỏi nhắc "khuyến mãi" -> ưu tiên đường đi tới Promotion
    assert any("áp dụng cho" in p.explanation and "ngành hàng Điện tử" in p.explanation for p in res.paths)


def test_local_search_penalizes_promotion_scoped_to_other_store(searcher):
    q = "ĐT-018 có khuyến mãi nào?"
    mentions, _ = searcher.extract_entities(q, allow_llm=False)
    res = searcher.local_search(q, mentions, store_code="GM-HN01")
    scores = {p.path.nodes[-1].id: p.score for p in res.paths if p.path.nodes[-1].label == "Promotion"}
    # "Sale khai trương chi nhánh Thủ Đức" chỉ áp dụng GM-HCM02 -> xếp sau khuyến mãi toàn hệ thống
    assert scores["Sale khai trương chi nhánh Thủ Đức"] < max(v for k, v in scores.items() if k != "Sale khai trương chi nhánh Thủ Đức")


def test_explain_path_is_human_readable(searcher, store):
    from graph.store import NodeRef

    p = store.paths_from([NodeRef("SKU", "ĐT-018")], 1, limit=5)[0]
    text = explain_path(p)
    assert text.startswith("SKU ĐT-018 (Loa bluetooth") and "thuộc ngành hàng" in text


def test_local_search_without_entities_returns_empty_with_warning(searcher):
    res = searcher.local_search("xin chào", [])
    assert res.empty and res.warnings


# =====================================================================================
# Aggregation
# =====================================================================================

def test_aggregation_counts_promotions_applying_to_category(searcher, store):
    q = "Có bao nhiêu chương trình khuyến mãi áp dụng cho ngành hàng Điện máy?"
    mentions, _ = searcher.extract_entities(q, allow_llm=False)
    res = searcher.aggregate(q, mentions)
    expected = {e.src.id for e in store.edges_of_type("APPLIES_TO") if e.dst.id == "Điện máy"}
    assert res.aggregation["target"] == "Promotion"
    assert res.aggregation["count"] == len(expected) > 0
    assert res.computed_facts and str(len(expected)) in res.computed_facts[0]
    assert res.record_ids


def test_aggregation_stock_status_filter_global(searcher, store):
    res = searcher.aggregate("Liệt kê các sản phẩm sắp hết hàng", [])
    expected = {e.dst.id for e in store.edges_of_type("STOCKS") if e.props["trang_thai"] == "Sắp hết"}
    assert res.aggregation["count"] == len(expected) > 0


def test_aggregation_store_scope_applies_to_stock_questions(searcher, store):
    res = searcher.aggregate("Có bao nhiêu sản phẩm sắp hết hàng?", [], store_code="GM-HN01")
    expected = {e.dst.id for e in store.edges_of_type("STOCKS") if e.props["trang_thai"] == "Sắp hết" and e.src.id == "GM-HN01"}
    assert res.aggregation["count"] == len(expected)
