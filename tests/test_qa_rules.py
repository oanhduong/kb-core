"""Unit test phần xác định của QA: conflict detector (B4), as_of (C2), money/review (B6), abstention
(B3), confidence (B5)."""
from datetime import date

import pytest

from core.config import get_config
from qa.abstention import decide, sigmoid
from qa.conflicts import detect_conflicts, ensure_conflict_disclosure, extract_price_facts, format_vnd, parse_vnd
from qa.dates import extract_as_of
from domains import get_pack
from qa.review import is_money_question, needs_review

RETAIL_KW = get_pack("retail").review.keywords
from qa.trace import DecisionTrace, compute_confidence

CFG = get_config()


# =====================================================================================
# Conflict detector
# =====================================================================================

def _row(rid, price, eff, store=None, sku="ĐT-018", doc="D1"):
    return {"record_id": rid, "document_id": doc, "store_code": store, "effective_date": eff,
            "meta": {"fields": {"sku": sku, "ten_san_pham": "Loa", "gia": str(price), "category": "Điện tử"}}}


def test_price_facts_from_structured_xlsx_fields():
    facts = extract_price_facts(_row("r1", "1.440.000", "2026-08-01"), "bảng giá, Sheet A dòng 3")
    assert len(facts) == 1 and facts[0].value == 1440000 and facts[0].entity_key == "sku:ĐT-018|store:*"


def test_price_facts_from_free_text_line_with_single_sku():
    rec = {"record_id": "p1", "document_id": "IMG", "store_code": None, "effective_date": "2026-09-01",
           "content": "POSTER KHUYẾN MÃI\nLoa ĐT-018 giá chỉ còn 1.290.000đ\nÁp dụng toàn hệ thống"}
    facts = extract_price_facts(rec, "poster")
    assert [(f.entity_key, f.value) for f in facts] == [("sku:ĐT-018|store:*", 1290000)]


def test_detect_conflict_between_two_active_sources_marks_newest():
    facts = extract_price_facts(_row("r1", 1440000, "2026-08-01"), "bảng giá") + \
        extract_price_facts(_row("r2", 1290000, "2026-09-01", doc="D2"), "poster")
    [c] = detect_conflicts(facts)
    assert c.kind == "price_mismatch" and c.newest_record_id == "r2"
    assert [v.value for v in c.values] == [1290000, 1440000]
    assert c.fingerprint == detect_conflicts(list(reversed(facts)))[0].fingerprint  # idempotent


def test_no_conflict_when_same_value_or_different_store_scope():
    same = extract_price_facts(_row("r1", 100000, "2026-08-01"), "a") + extract_price_facts(_row("r2", 100000, "2026-09-01"), "b")
    assert detect_conflicts(same) == []
    scoped = extract_price_facts(_row("r1", 100000, "2026-08-01"), "a") + \
        extract_price_facts(_row("r2", 90000, "2026-09-01", store="GM-HCM01"), "b")
    assert detect_conflicts(scoped) == []  # giá riêng cửa hàng khác giá toàn hệ thống — không phải mâu thuẫn


def test_same_effective_date_has_no_newest():
    facts = extract_price_facts(_row("r1", 1, "2026-08-01"), "a") + extract_price_facts(_row("r2", 2, "2026-08-01"), "b")
    # giá < 1000 từ cột có cấu trúc vẫn tính (giá trị thật của cột giá)
    [c] = detect_conflicts(facts)
    assert c.newest_record_id is None


def test_disclosure_appended_only_when_answer_misses_a_value():
    facts = extract_price_facts(_row("r1", 1440000, "2026-08-01"), "bảng giá") + \
        extract_price_facts(_row("r2", 1290000, "2026-09-01"), "poster")
    conflicts = detect_conflicts(facts)
    full = "Bảng giá ghi 1.440.000đ [1], poster ghi 1.290.000đ [2] (mới hơn)."
    assert ensure_conflict_disclosure(full, conflicts, {"r1": 1, "r2": 2}) == (full, False)
    partial, appended = ensure_conflict_disclosure("Giá là 1.290.000đ [2].", conflicts, {"r2": 2})
    assert appended and "1.440.000đ theo bảng giá" in partial and "nguồn MỚI HƠN" in partial


def test_vnd_helpers():
    assert parse_vnd("1.440.000") == 1440000 and parse_vnd("590,000") == 590000 and parse_vnd("abc") is None
    assert format_vnd(1440000) == "1.440.000đ"


# =====================================================================================
# as_of
# =====================================================================================

TODAY = date(2026, 10, 3)


@pytest.mark.parametrize("q,expected", [
    ("Giá ĐT-018 ngày 15/7/2026 là bao nhiêu?", date(2026, 7, 15)),
    ("Giá vào 2026-08-01?", date(2026, 8, 1)),
    ("Giá ĐT-018 tháng 7/2026", date(2026, 7, 31)),
    ("Giá hồi tháng 8 là bao nhiêu", date(2026, 8, 31)),
    ("Giá tháng trước", date(2026, 9, 30)),
    ("Giá ĐT-018 hiện tại bao nhiêu?", None),
    ("Khuyến mãi tháng 10 có gì", None),  # tháng hiện tại -> dữ liệu đang hiệu lực
])
def test_extract_as_of(q, expected):
    assert extract_as_of(q, today=TODAY) == expected


# =====================================================================================
# Money / review
# =====================================================================================

@pytest.mark.parametrize("q,money", [
    ("Giá ĐT-018 bao nhiêu?", True), ("gia ban DT-018 la bao nhieu", True),
    ("Khách được hoàn tiền trong bao lâu?", True), ("Mức bồi thường khi giao hàng trễ?", True),
    ("Chiết khấu cho đơn 10 triệu?", True), ("Quy trình đổi trả thế nào?", False),
    ("Hợp đồng NCC ký ngày nào?", False), ("Đánh giá nhân viên theo tiêu chí nào?", False),
    ("Nồi chiên thuộc ngành gia dụng không?", False),
])
def test_money_question_detection(q, money):
    assert is_money_question(q, RETAIL_KW)[0] is money


def test_review_policy_comes_from_domain_pack():
    assert needs_review("Giá ĐT-018 bao nhiêu?", "viewer", CFG, "retail")[0] is True
    assert needs_review("Giá ĐT-018 bao nhiêu?", "editor", CFG, "retail")[0] is False  # supervisor = editor
    assert needs_review("Giá ĐT-018 bao nhiêu?", "viewer", CFG, "generic")[0] is False  # pack tổng quát không duyệt


# =====================================================================================
# Abstention + confidence + trace
# =====================================================================================

def test_abstention_thresholds_from_config():
    th = CFG.abstention.rerank_min_score
    assert decide(CFG, candidates=0, rerank_top=None, dense_top=None, bm25_hits=0).reason == "no_results"
    assert decide(CFG, candidates=5, rerank_top=th - 0.01, dense_top=0.9, bm25_hits=1).abstain
    assert not decide(CFG, candidates=5, rerank_top=th + 0.01, dense_top=0.1, bm25_hits=0).abstain
    # reranker lỗi -> ngưỡng dense dự phòng, hoặc có BM25 khớp mã SKU
    assert decide(CFG, candidates=5, rerank_top=None, dense_top=0.1, bm25_hits=0).abstain
    assert not decide(CFG, candidates=5, rerank_top=None, dense_top=0.1, bm25_hits=1).abstain
    # graph có nguồn -> không abstain dù vector yếu
    assert not decide(CFG, candidates=3, rerank_top=0.01, dense_top=0.1, bm25_hits=0, graph_sources=2).abstain
    assert 0.49 < sigmoid(0.0) < 0.51


def test_confidence_levels_are_deterministic():
    high = compute_confidence(top_score=0.92, score_kind="rerank", coverage=1.0, conflicts=0, degraded=0)
    assert high["level"] == "cao"
    low = compute_confidence(top_score=0.4, score_kind="rerank", coverage=0.5, conflicts=1, degraded=1)
    assert low["level"] == "thấp" and "mâu thuẫn" in low["explanation"]


def test_decision_trace_summary_is_human_readable():
    t = DecisionTrace(understood={"question_type": "multi_hop", "entities": [{"type": "SKU", "id": "ĐT-018"}],
                                  "store_scope": "GM-HN01 + dữ liệu toàn hệ thống"})
    t.add_search("Đồ thị tri thức (graph)", result="3 đoạn nguồn")
    t.found = [{"used_in_answer": True}, {"used_in_answer": False}]
    t.basis = ["[1] chính sách đổi trả, Điều 5"]
    t.confidence = {"level": "cao", "score": 0.81, "explanation": "điểm rerank cao"}
    t.degrade("Reranker lỗi -> dùng thứ tự hybrid")
    lines = t.summary()
    assert lines[0].startswith("Đã hiểu: câu hỏi nối nhiều thực thể; thực thể: SKU ĐT-018")
    assert any(line.startswith("Dựa vào: [1] chính sách đổi trả, Điều 5") for line in lines)
    assert any("dự phòng" in line for line in lines)


@pytest.mark.parametrize("pack,expect_hint", [("retail", True), ("generic", False)])
def test_assistant_system_prompt_carries_pack_hints(pack, expect_hint):
    from assistant.agent import AssistantRequest, AssistantService
    from core.config import get_config
    from core.principal import Principal, WorkspaceContext

    svc = AssistantService.__new__(AssistantService)
    svc.cfg = get_config()
    ctx = WorkspaceContext("ws1", "Test", pack, "viewer", {}, {}, Principal(subject="u1"))
    text = "".join(b["text"] for b in svc._system(AssistantRequest(question="?", ctx=ctx)))
    assert ("Kiến thức nghiệp vụ" in text and "NGÀNH HÀNG" in text) is expect_hint
