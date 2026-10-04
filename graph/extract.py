"""LLM extraction cho văn bản tự do (A2) — CHỈ phần diễn giải trong docx chính sách và transcript
đào tạo, nơi quan hệ nằm trong câu chữ (dữ liệu có cấu trúc đã do graph/build.py xử lý bằng rule).

- Schema cố định (graph/schema.py): model chỉ được trả các loại node/edge đã khai báo; pydantic
  validate (Literal + kiểm tra cặp from/to hợp lệ cho từng loại edge).
- Retry tối đa graph.llm_extraction.max_retries lần khi output sai schema (core.llm.complete_json);
  hết lượt hoặc model lỗi -> bỏ qua đoạn đó, ghi warning + log, KHÔNG chặn pipeline.
- Endpoint phải resolve được về node ĐANG CÓ (graph.resolve.EntityResolver) — model không tạo được
  thực thể mới; tên không resolve được bị bỏ (log để admin bổ sung alias).
- Nội dung đưa vào model bọc <untrusted_document> (security/provenance.py) — dữ liệu, không phải lệnh.
- Edge LLM mang extracted_by="llm", confidence, evidence và source_docs "<doc>~llm" (tách khỏi phần
  rule để làm lại phần rule không xoá mất cạnh LLM — xem graph/sync.py).
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.config import AppConfig, get_config, read_prompt
from core.llm import LLMClient, LLMOutputInvalid, LLMUnavailable
from core.logging import log_event
from graph.build import BuildContext, DocumentInput, load_units
from graph.model import GraphAccumulator
from graph.resolve import EntityResolver, fold_text
from graph.schema import is_valid_edge
from security.provenance import wrap_untrusted

logger = logging.getLogger(__name__)

NodeType = Literal["SKU", "Category", "Store", "Promotion", "Policy", "Supplier"]
EdgeType = Literal["BELONGS_TO", "APPLIES_TO", "GOVERNS", "REFERENCES", "STOCKS", "SUPPLIES", "HAS_EXCEPTION_FOR"]

_RELATION_HINT_RE = re.compile(
    r"áp dụng|không áp dụng|ngoại trừ|ngoại lệ|trừ khi|thuộc|cung cấp|theo (?:quy định|điều)|tham chiếu|"
    r"khuyến mãi|ngành hàng|nhà cung cấp|cửa hàng|chi nhánh", re.IGNORECASE)
_ARTICLE_IN_NAME_RE = re.compile(r"Điều\s+(\d+)", re.IGNORECASE)
_SELF_POLICY_RE = re.compile(r"^(chính sách|quy định|quy trình|nội quy)( này)?$|^(văn bản|tài liệu) này$", re.IGNORECASE)
MIN_CONFIDENCE = 0.5
MIN_UNIT_CHARS = 150
WINDOW_CHARS = 1500


class EntityRef(BaseModel):
    type: NodeType
    name: str = Field(min_length=1, max_length=200)


class ExtractedRelation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: EdgeType
    from_: EntityRef = Field(alias="from")
    to: EntityRef
    evidence: str = Field(default="", max_length=400)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_schema(self) -> "ExtractedRelation":
        if not is_valid_edge(self.type, self.from_.type, self.to.type):
            raise ValueError(f"quan hệ {self.type} không hợp lệ giữa {self.from_.type} -> {self.to.type}")
        return self


class ExtractionOutput(BaseModel):
    relations: list[ExtractedRelation] = Field(default_factory=list, max_length=40)


def _text_units(doc: DocumentInput) -> list[tuple[str, str, list[str]]]:
    """-> list (location, text, source record_ids) cần đưa qua LLM."""
    if doc.source_type == "docx":
        out = []
        for u in load_units(doc):
            if u.kind in ("article", "section") and len(u.content) >= MIN_UNIT_CHARS and _RELATION_HINT_RE.search(u.content):
                out.append((u.location, u.content, [doc.rid(u.location)]))
        return out
    # transcript/training: gom record liền kề thành cửa sổ ~WINDOW_CHARS
    windows: list[tuple[str, str, list[str]]] = []
    buf, ids, start_loc = [], [], None
    for rec in doc.records:
        text = (rec.get("content") or "").strip()
        if not text:
            continue
        if start_loc is None:
            start_loc = rec.get("location", "")
        buf.append(text)
        ids.append(rec["record_id"])
        if sum(len(t) for t in buf) >= WINDOW_CHARS:
            windows.append((start_loc, " ".join(buf), ids))
            buf, ids, start_loc = [], [], None
    if buf:
        windows.append((start_loc or "", " ".join(buf), ids))
    return [w for w in windows if _RELATION_HINT_RE.search(w[1])]


class GraphExtractor:
    def __init__(self, llm: LLMClient, cfg: AppConfig | None = None) -> None:
        self.llm = llm
        self.cfg = cfg or get_config()

    def _resolve(self, resolver: EntityResolver, ref: EntityRef, doc_policy_id: str | None,
                 unit_article: str | None) -> str | None:
        if ref.type == "Policy" and doc_policy_id:
            m = _ARTICLE_IN_NAME_RE.search(ref.name)
            name_no_article = _ARTICLE_IN_NAME_RE.sub("", ref.name).strip(" ,.-")
            if _SELF_POLICY_RE.match(name_no_article or "chính sách này"):
                return f"{doc_policy_id}#dieu-{m.group(1)}" if m else (unit_article or doc_policy_id)
        res = resolver.resolve(ref.type, ref.name)
        return res.canonical_id

    def extract_document(self, doc: DocumentInput, ctx: BuildContext, rule_acc: GraphAccumulator,
                         out_acc: GraphAccumulator) -> tuple[int, list[str]]:
        conf = self.cfg.graph.llm_extraction
        warnings: list[str] = []
        if not conf.enabled or doc.doc_type not in conf.doc_types:
            return 0, warnings
        nodes = ctx.nodes_by_label()
        for n in rule_acc.node_list():
            nodes.setdefault(n["label"], []).append({"id": n["id"], **n["properties"]})
        resolver = EntityResolver(nodes)
        doc_policy_id = doc.series_key if doc.source_type == "docx" else None
        system = read_prompt("graph_extract", self.cfg)
        units = _text_units(doc)[: conf.max_chunks_per_document]
        added = skipped_unresolved = failed = 0
        for location, text, record_ids in units:
            m = _ARTICLE_IN_NAME_RE.search(location.split(" > ")[-1]) if doc_policy_id else None
            unit_article = f"{doc_policy_id}#dieu-{m.group(1)}" if m else None
            user = ("Trích quan hệ từ đoạn văn sau:\n"
                    + wrap_untrusted(text, source=f"{doc.filename} — {location}", doc_type=doc.doc_type))
            try:
                out = self.llm.complete_json("graph_extraction", system=system, user=user, schema=ExtractionOutput,
                                             max_tokens=1200, max_retries=conf.max_retries)
            except (LLMOutputInvalid, LLMUnavailable) as e:
                failed += 1
                warnings.append(f"LLM extraction bỏ qua đoạn {location!r}: {e}")
                log_event(logger, "graph_extract_skipped", level=logging.WARNING, document_id=doc.document_id,
                          location=location, error=str(e)[:300])
                continue
            for rel in out.relations:
                if rel.confidence < MIN_CONFIDENCE:
                    continue
                src = self._resolve(resolver, rel.from_, doc_policy_id, unit_article)
                dst = self._resolve(resolver, rel.to, doc_policy_id, unit_article)
                if not src or not dst or src == dst:
                    skipped_unresolved += 1
                    log_event(logger, "graph_extract_unresolved", level=logging.INFO, document_id=doc.document_id,
                              relation=rel.type, from_name=rel.from_.name, to_name=rel.to.name)
                    continue
                if fold_text(rel.evidence) and fold_text(rel.evidence)[:40] not in fold_text(text):
                    rel.evidence = ""  # evidence không có trong đoạn gốc -> không lưu (tránh bằng chứng bịa)
                out_acc.add_node(rel.from_.type, src, {}, [])
                out_acc.add_node(rel.to.type, dst, {}, [])
                out_acc.add_edge(rel.type, rel.from_.type, src, rel.to.type, dst,
                                 {"extracted_by": "llm", "confidence": rel.confidence, "evidence": rel.evidence[:200]},
                                 record_ids)
                added += 1
        for node in out_acc.nodes.values():  # node chạm của phần LLM không mang nguồn (nguồn thuộc rule/doc khác)
            node["source_docs"] = []
        log_event(logger, "graph_extract_done", document_id=doc.document_id, units=len(units), edges=added,
                  unresolved=skipped_unresolved, failed_units=failed)
        if skipped_unresolved:
            warnings.append(f"{skipped_unresolved} quan hệ LLM bị bỏ vì tên thực thể không resolve được — "
                            "cân nhắc thêm alias (graph_aliases).")
        return added, warnings
