"""Router câu hỏi (A5): lookup / multi_hop / aggregation.

Rule trước (rẻ, xác định, giải thích được):
    - Có mã SKU và chỉ đúng 1 thực thể -> lookup (chắc chắn).
    - Có cụm đếm/liệt kê ("bao nhiêu cửa hàng", "những khuyến mãi nào", "liệt kê"...) -> aggregation.
      LƯU Ý "giá bao nhiêu"/"bao nhiêu ngày" là hỏi GIÁ TRỊ (lookup), không phải đếm.
    - >= 2 loại thực thể khác nhau, hoặc 1 thực thể + từ nối quan hệ ("áp dụng cho", "thuộc",
      "chính sách nào", "nhà cung cấp nào"...) -> multi_hop.
LLM classify (prompt router_classify, model rẻ) CHỈ khi rule không chắc (confidence thấp).
Route: lookup -> vector pipeline; multi_hop -> graph local search; aggregation -> graph aggregation.
Config router.graph_enabled=false (hoặc graph.enabled=false) tắt graph hoàn toàn -> mọi câu đi vector;
router.force_route ép 1 loại (debug/khẩn cấp).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from core.config import AppConfig, get_config, read_prompt
from core.llm import LLMClient, LLMOutputInvalid, LLMUnavailable
from core.logging import log_event
from graph.query import GraphSearcher
from graph.resolve import EntityMention, fold_text

logger = logging.getLogger(__name__)

QuestionType = Literal["lookup", "multi_hop", "aggregation"]

_SKU_CODE_RE = re.compile(r"(?<![\w-])[A-Za-zĐđ]{2,5}-?\d{2,6}(?![\w-])")
_AGG_RE = re.compile(
    r"(?:bao nhieu|may)\s+(?:\w+\s+){0,2}(?:san pham|sku|mat hang|ma hang|cua hang|chi nhanh|khuyen mai|chuong trinh|"
    r"nha cung cap|ncc|nganh hang|chinh sach|dieu)"
    r"|liet ke|danh sach|dem\s|tong so|tat ca (?:cac )?(?:san pham|sku|cua hang|khuyen mai|nha cung cap|chinh sach)"
    r"|(?:nhung|cac)\s+(?:\w+\s+){0,2}(?:san pham|sku|cua hang|chi nhanh|khuyen mai|chuong trinh|nha cung cap)\s+(?:\w+\s+){0,4}nao")
_RELATION_CUE_RE = re.compile(
    r"ap dung cho|ap dung tai|thuoc nganh|thuoc danh muc|nganh hang nao|danh muc nao|chinh sach nao|quy dinh nao|"
    r"dieu nao|khuyen mai nao|"
    r"chuong trinh nao|nha cung cap nao|ncc nao|cua hang nao|tham chieu|lien quan den|ngoai le|co duoc doi|"
    r"co duoc bao hanh|ai cung cap|cung cap boi|nhap tu")


@dataclass
class RouteDecision:
    question_type: QuestionType
    route: Literal["vector", "graph", "graph_aggregation"]
    method: Literal["rule", "llm", "config", "default"]
    confidence: float
    reasons: list[str] = field(default_factory=list)
    entities: list[EntityMention] = field(default_factory=list)
    extraction_method: str = "none"

    def to_dict(self) -> dict:
        return {"question_type": self.question_type, "route": self.route, "method": self.method,
                "confidence": round(self.confidence, 2), "reasons": self.reasons,
                "entities": [{"type": m.label, "id": m.id, "text": m.text, "method": m.method} for m in self.entities],
                "entity_extraction": self.extraction_method}


class _LLMRoute(BaseModel):
    question_type: QuestionType
    confidence: float = Field(ge=0.0, le=1.0)


def classify_by_rules(question: str, mentions: list[EntityMention]) -> tuple[QuestionType, float, list[str]]:
    """Thuần, không gọi LLM/DB — unit test trực tiếp."""
    fq = fold_text(question)
    has_sku_code = bool(_SKU_CODE_RE.search(question)) or any(m.label == "SKU" and m.method != "name" for m in mentions)
    distinct = {(m.label, m.id) for m in mentions}
    labels = {m.label for m in mentions}
    if _AGG_RE.search(fq):
        return "aggregation", 0.85, ["có cụm đếm/liệt kê nhiều thực thể"]
    if len(labels) >= 2:
        return "multi_hop", 0.8, [f"nhắc {len(labels)} loại thực thể: {', '.join(sorted(labels))}"]
    if distinct and _RELATION_CUE_RE.search(fq):
        # "ĐT-018 thuộc ngành hàng nào / được áp dụng khuyến mãi nào" — 1 thực thể nhưng hỏi QUAN HỆ
        return "multi_hop", 0.75, ["có thực thể + từ nối quan hệ (hỏi thực thể liên quan)"]
    if has_sku_code and len(distinct) <= 1:
        return "lookup", 0.9, ["có mã SKU và chỉ 1 thực thể"]
    if len(distinct) >= 2:
        return "multi_hop", 0.6, ["nhắc nhiều thực thể cùng loại"]
    if distinct:
        return "lookup", 0.6, ["1 thực thể, không có từ nối quan hệ"]
    return "lookup", 0.45, ["không nhận ra thực thể nào — mặc định tra cứu vector"]


class QuestionRouter:
    def __init__(self, cfg: AppConfig | None = None, llm: LLMClient | None = None,
                 searcher: GraphSearcher | None = None) -> None:
        self.cfg = cfg or get_config()
        self.llm = llm
        self.searcher = searcher

    @property
    def graph_on(self) -> bool:
        return self.cfg.graph.enabled and self.cfg.router.graph_enabled and self.searcher is not None

    def _route_for(self, qtype: QuestionType) -> str:
        if not self.graph_on or qtype == "lookup":
            return "vector"
        return "graph" if qtype == "multi_hop" else "graph_aggregation"

    def route(self, question: str) -> RouteDecision:
        rc = self.cfg.router
        mentions: list[EntityMention] = []
        extraction = "none"
        if self.searcher is not None and self.graph_on:
            # rule-only ở bước router; LLM entity extraction (đắt hơn) để graph search tự gọi nếu cần
            mentions, extraction = self.searcher.extract_entities(question, allow_llm=False)
        if rc.force_route:
            d = RouteDecision(rc.force_route, "vector", "config", 1.0, ["router.force_route trong config"], mentions, extraction)
            d.route = self._route_for(rc.force_route)  # type: ignore[assignment]
            return d
        qtype, conf, reasons = classify_by_rules(question, mentions)
        method: str = "rule"
        if conf < rc.llm_min_confidence + 0.2 and rc.llm_fallback and self.llm is not None and self.graph_on:
            try:
                out = self.llm.complete_json("router", system=read_prompt("router_classify", self.cfg),
                                             user=f"Câu hỏi: {question}", schema=_LLMRoute, max_tokens=60, max_retries=1)
                if out.confidence >= rc.llm_min_confidence:
                    reasons.append(f"rule không chắc ({conf:.2f}) -> LLM phân loại {out.question_type} ({out.confidence:.2f})")
                    qtype, conf, method = out.question_type, out.confidence, "llm"
                else:
                    reasons.append(f"LLM cũng không chắc ({out.confidence:.2f}) -> giữ kết quả rule")
            except (LLMOutputInvalid, LLMUnavailable) as e:
                reasons.append("LLM router lỗi -> giữ kết quả rule")
                log_event(logger, "router_llm_failed", level=logging.WARNING, error=str(e)[:200])
        if not self.graph_on and qtype != "lookup":
            reasons.append("graph đang tắt (config) -> đi vector")
        decision = RouteDecision(qtype, self._route_for(qtype), method, conf, reasons, mentions, extraction)  # type: ignore[arg-type]
        log_event(logger, "route_decision", question_type=qtype, route=decision.route, method=method, confidence=conf)
        return decision
