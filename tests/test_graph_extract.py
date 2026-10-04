"""Test phần XÁC ĐỊNH của graph/extract.py (A2): schema pydantic cố định + hành vi bỏ qua khi model trả
sai (không chặn pipeline). Không gọi LLM thật — client giả trả text định sẵn."""
import json
from types import SimpleNamespace

import pytest
from docx import Document as DocxDocument
from pydantic import ValidationError

from core.config import get_config
from core.llm import LLMClient
from graph.build import BuildContext, DocumentInput, build_document
from graph.extract import ExtractedRelation, ExtractionOutput, GraphExtractor


class FakeAnthropic:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else "{}"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def _llm(replies):
    fake = FakeAnthropic(replies)
    return LLMClient(client=fake, sleep=lambda s: None), fake


def test_relation_schema_rejects_unknown_types_and_invalid_pairs():
    ok = ExtractedRelation.model_validate({"type": "APPLIES_TO", "from": {"type": "Promotion", "name": "X"},
                                           "to": {"type": "Category", "name": "Y"}, "confidence": 0.9})
    assert ok.from_.name == "X"
    with pytest.raises(ValidationError):  # cặp from/to sai cho loại cạnh
        ExtractedRelation.model_validate({"type": "APPLIES_TO", "from": {"type": "Store", "name": "X"},
                                          "to": {"type": "Category", "name": "Y"}})
    with pytest.raises(ValidationError):  # loại node mới do model tự sinh
        ExtractedRelation.model_validate({"type": "GOVERNS", "from": {"type": "Department", "name": "X"},
                                          "to": {"type": "Category", "name": "Y"}})
    with pytest.raises(ValidationError):  # loại cạnh mới
        ExtractedRelation.model_validate({"type": "SELLS", "from": {"type": "Store", "name": "X"},
                                          "to": {"type": "SKU", "name": "Y"}})


def _policy(tmp_path):
    p = tmp_path / "01_chinh_sach_doi_tra.docx"
    d = DocxDocument()
    d.add_heading("CHÍNH SÁCH ĐỔI TRẢ", level=0)
    d.add_paragraph("Phụ đề")
    d.add_heading("Điều 6. Trường hợp không áp dụng", level=2)
    # tên khuyến mãi KHÔNG xuất hiện nguyên văn -> rule không bắt được, chỉ LLM mới nối được quan hệ
    d.add_paragraph("Sản phẩm mua trong đợt flash sale giữa tháng chín không áp dụng đổi trả, trừ khi lỗi "
                    "do nhà sản xuất. Quy định này áp dụng cho mọi ngành hàng trên toàn hệ thống cửa hàng GreenMart.")
    d.save(p)
    return DocumentInput(document_id="D9", path=str(p), filename=p.name, source_type="docx", doc_type="policy",
                         title="CHÍNH SÁCH ĐỔI TRẢ", series_key="chinh_sach_doi_tra")


def test_extractor_adds_llm_edge_with_resolved_endpoints(tmp_path):
    reply = json.dumps({"relations": [{
        "type": "HAS_EXCEPTION_FOR", "from": {"type": "Policy", "name": "Điều 6 chính sách này"},
        "to": {"type": "Promotion", "name": "flash sale giữa tháng 9"},
        "evidence": "Sản phẩm mua trong đợt flash sale giữa tháng chín không áp dụng đổi trả", "confidence": 0.9}]})
    llm, fake = _llm([reply])
    ctx = BuildContext(promotions={"Flash sale giữa tháng 9": {}})
    res = build_document(_policy(tmp_path), ctx, extractor=GraphExtractor(llm, get_config()))
    llm_edges = [e for e in res.acc.edge_list() if e["properties"].get("extracted_by") == "llm"]
    assert fake.calls == 1
    assert [(e["from_id"], e["to_id"]) for e in llm_edges] == [("chinh_sach_doi_tra#dieu-6", "Flash sale giữa tháng 9")]
    assert llm_edges[0]["source_docs"] == ["D9~llm"]  # tách khỏi phần rule để sync không xoá nhầm
    assert res.llm_edges == 1


def test_extractor_skips_unit_after_invalid_output_retries_without_raising(tmp_path):
    cfg = get_config()
    retries = cfg.graph.llm_extraction.max_retries
    llm, fake = _llm(["không phải json"] * (retries + 1))
    res = build_document(_policy(tmp_path), BuildContext(), extractor=GraphExtractor(llm, cfg))
    assert fake.calls == retries + 1  # 1 lần đầu + tối đa max_retries lần sửa
    assert any("LLM extraction bỏ qua" in w for w in res.warnings)
    assert res.builder == "policy" and res.acc.nodes  # phần rule vẫn build bình thường


def test_extractor_drops_relations_with_unresolvable_entities(tmp_path):
    reply = json.dumps({"relations": [{"type": "APPLIES_TO", "from": {"type": "Promotion", "name": "KM không tồn tại"},
                                       "to": {"type": "Category", "name": "Ngành bịa"}, "confidence": 0.9}]})
    llm, _ = _llm([reply])
    res = build_document(_policy(tmp_path), BuildContext(), extractor=GraphExtractor(llm, get_config()))
    assert not [e for e in res.acc.edge_list() if e["properties"].get("extracted_by") == "llm"]
    assert ExtractionOutput.model_validate({"relations": []}).relations == []
