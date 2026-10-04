"""Graph local search (A4) + aggregation có đếm (A5 route "aggregation").

local_search(question):
    1. Trích entity: rule trước (graph.resolve.EntityResolver.find_mentions: mã SKU/cửa hàng/NCC,
       tên ngành hàng/khuyến mãi/chính sách/sản phẩm/alias, "Điều N" của 1 chính sách) — chỉ khi
       rule không ra gì mới gọi LLM (prompt entity_extract, schema cố định) rồi resolve lại.
    2. Node khởi điểm = entity đã resolve; mở rộng TỐI ĐA 2 hop (config graph.max_hops <= 2).
    3. Chấm điểm đường đi (nối 2 entity được hỏi > đi tới loại node câu hỏi nhắc tới > ngắn hơn >
       cạnh rule > cạnh LLM; phạt đường đi qua cửa hàng khác store của user / node stale).
    4. Trả record_id (source_chunk_ids trên cạnh/node của đường đi được chọn) + explanation dạng chữ
       cho từng đường đi — explanation lấy từ đường đi THẬT trong graph, không phải lời model.
aggregate(question): đếm/liệt kê node loại đích (SKU/Store/Promotion/...) nối với anchor (hoặc toàn
graph nếu không có anchor), có lọc trạng thái tồn kho ("hết hàng", "sắp hết"...). Số đếm là
computed fact do code tính, model chỉ diễn đạt lại.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from core.config import AppConfig, get_config, read_prompt
from core.llm import LLMClient, LLMOutputInvalid, LLMUnavailable
from core.logging import log_event
from graph.resolve import EntityMention, EntityResolver, fold_text
from graph.schema import EDGE_LABELS_VI, NODE_LABELS_VI, node_display
from graph.store import GraphEdge, GraphNode, GraphPath, GraphStore, NodeRef
from observability import tracing

logger = logging.getLogger(__name__)

_ARTICLE_RE = re.compile(r"Điều\s+(\d+)", re.IGNORECASE)

# từ khoá -> label node câu hỏi đang nhắm tới (để ưu tiên đường đi)
LABEL_HINTS: dict[str, tuple[str, ...]] = {
    "Promotion": ("khuyen mai", "chuong trinh", "giam gia", "uu dai", "sale"),
    "Policy": ("chinh sach", "quy dinh", "dieu", "doi tra", "bao hanh", "khieu nai", "giao hang", "noi quy", "quy trinh"),
    "Supplier": ("nha cung cap", "ncc", "nhap tu", "cung cap"),
    "Store": ("cua hang", "chi nhanh", "ton kho", "con hang", "het hang", "store"),
    "Category": ("nganh hang", "danh muc", "loai hang"),
    "SKU": ("san pham", "sku", "mat hang", "ma hang"),
}

STOCK_STATUS_FILTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sap het", ("sap het",)),
    ("het hang", ("het hang",)),
    ("du hang", ("du hang", "con hang")),
    ("con hang", ("du hang", "con hang", "sap het")),
)

_AGG_TARGETS: tuple[tuple[str, str], ...] = (
    (r"(?:san pham|sku|mat hang|ma hang)", "SKU"),
    (r"(?:cua hang|chi nhanh|store)", "Store"),
    (r"(?:khuyen mai|chuong trinh)", "Promotion"),
    (r"(?:nha cung cap|ncc)", "Supplier"),
    (r"(?:nganh hang|danh muc)", "Category"),
    (r"(?:chinh sach|dieu khoan|dieu)", "Policy"),
)


class _ExtractedEntity(BaseModel):
    type: str
    text: str = Field(min_length=1, max_length=200)


class EntityExtractionOutput(BaseModel):
    entities: list[_ExtractedEntity] = Field(default_factory=list, max_length=15)


@dataclass
class ScoredPath:
    path: GraphPath
    score: float
    explanation: str


@dataclass
class GraphSearchResult:
    mode: str  # local | aggregation
    entities: list[EntityMention]
    extraction_method: str  # rule | llm | none
    paths: list[ScoredPath] = field(default_factory=list)
    record_ids: list[str] = field(default_factory=list)
    computed_facts: list[str] = field(default_factory=list)
    aggregation: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.record_ids and not self.computed_facts


def explain_path(path: GraphPath) -> str:
    """"SKU ĐT-018 (Loa ...) —[thuộc ngành hàng]→ ngành hàng Điện tử ←[điều chỉnh ngành hàng]— ..."."""
    parts = [node_display(path.nodes[0].label, path.nodes[0].props)]
    for i, edge in enumerate(path.edges):
        cur, nxt = path.nodes[i], path.nodes[i + 1]
        label = EDGE_LABELS_VI.get(edge.type, edge.type)
        forward = edge.src == cur.ref
        arrow = f" —[{label}]→ " if forward else f" ←[{label}]— "
        suffix = " (LLM trích)" if edge.props.get("extracted_by") == "llm" else ""
        parts.append(arrow + node_display(nxt.label, nxt.props) + suffix)
    return "".join(parts)


def _hinted_labels(question: str) -> set[str]:
    fq = fold_text(question)
    return {label for label, keys in LABEL_HINTS.items() if any(re.search(rf"(?<!\w){k}(?!\w)", fq) for k in keys)}


class GraphSearcher:
    def __init__(self, store: GraphStore, resolver: EntityResolver, llm: LLMClient | None = None,
                 cfg: AppConfig | None = None) -> None:
        self.store = store
        self.resolver = resolver
        self.llm = llm
        self.cfg = cfg or get_config()

    # --------------------------------------------------------------- entities
    def extract_entities(self, question: str, *, allow_llm: bool = True) -> tuple[list[EntityMention], str]:
        mentions = self.resolver.find_mentions(question)
        policy_docs = [m for m in mentions if m.label == "Policy" and "#dieu-" not in m.id]
        for art in _ARTICLE_RE.finditer(question):
            for pdoc in policy_docs:
                art_id = f"{pdoc.id}#dieu-{art.group(1)}"
                if self.store.get_node("Policy", art_id) is not None:
                    mentions.append(EntityMention("Policy", art_id, art.group(0), "article", art.span()))
        if mentions:
            return mentions, "rule"
        if not (allow_llm and self.llm is not None and self.cfg.router.llm_fallback):
            return [], "none"
        try:
            out = self.llm.complete_json("entity_extraction", system=read_prompt("entity_extract", self.cfg),
                                         user=f"Câu hỏi: {question}", schema=EntityExtractionOutput,
                                         max_tokens=300, max_retries=1)
        except (LLMOutputInvalid, LLMUnavailable) as e:
            log_event(logger, "graph_entity_llm_failed", level=logging.WARNING, error=str(e)[:300])
            return [], "none"
        resolved = []
        for ent in out.entities:
            if ent.type not in NODE_LABELS_VI:
                continue
            res = self.resolver.resolve(ent.type, ent.text)
            if res.resolved:
                resolved.append(EntityMention(ent.type, res.canonical_id, ent.text, f"llm+{res.method}"))
        return resolved, ("llm" if resolved else "none")

    # --------------------------------------------------------------- local search
    def _score(self, path: GraphPath, starts: set[tuple[str, str]], hinted: set[str], store_code: str | None) -> float:
        end = path.nodes[-1]
        score = 1.0
        if (end.label, end.id) in starts and len(path.nodes) > 1:
            score += 5.0
        if end.label in hinted:
            score += 2.0
        score -= 0.75 * (path.length - 1)
        for e in path.edges:
            score += 0.3 if e.props.get("extracted_by", "rule") == "rule" else -0.2
        for n in path.nodes:
            if n.props.get("stale"):
                score -= 1.0
            if store_code and n.label == "Store" and n.id != store_code:
                score -= 3.0
            if store_code and n.label == "Promotion":
                codes = n.props.get("store_scope_codes") or []
                if codes and store_code not in codes and not n.props.get("store_scope_wildcard"):
                    score -= 3.0
        if end.label == "Policy" and end.props.get("level") == "document" and path.length == 2:
            score -= 0.5  # ưu tiên Điều cụ thể hơn cả văn bản
        return score

    def local_search(self, question: str, mentions: list[EntityMention], *, store_code: str | None = None,
                     extraction_method: str = "rule") -> GraphSearchResult:
        result = GraphSearchResult(mode="local", entities=mentions, extraction_method=extraction_method)
        if not mentions:
            result.warnings.append("Không xác định được thực thể nào trong câu hỏi để bắt đầu tìm trên graph.")
            return result
        starts = [NodeRef(m.label, m.id) for m in mentions]
        hinted = _hinted_labels(question)
        with tracing.span("graph.expand", input={"starts": [f"{s.label}:{s.id}" for s in starts]},
                          max_hops=self.cfg.graph.max_hops) as sp:
            raw = self.store.paths_from(starts, self.cfg.graph.max_hops, limit=self.cfg.graph.max_paths * 50)
            start_keys = {(s.label, s.id) for s in starts}
            scored = [ScoredPath(p, self._score(p, start_keys, hinted, store_code), explain_path(p)) for p in raw]
            scored.sort(key=lambda s: s.score, reverse=True)
            result.paths = scored[: self.cfg.graph.max_paths]
            sp.update(output={"raw_paths": len(raw), "selected": len(result.paths)})
        seen: set[str] = set()
        for sp_ in result.paths:
            for comp in [*reversed(sp_.path.edges), sp_.path.nodes[-1]]:
                for rid in comp.source_chunk_ids if isinstance(comp, (GraphEdge, GraphNode)) else []:
                    if rid not in seen:
                        seen.add(rid)
                        result.record_ids.append(rid)
        # entity được hỏi trực tiếp: thêm nguồn của chính node đó (vd dòng bảng giá của SKU)
        direct: list[str] = []
        for m in mentions:
            node = self.store.get_node(m.label, m.id)
            direct.extend(rid for rid in (node.source_chunk_ids if node else [])[:2] if rid not in direct)
        result.record_ids = direct + [rid for rid in result.record_ids if rid not in direct]
        result.record_ids = result.record_ids[: self.cfg.graph.max_chunks * 3]
        return result

    # --------------------------------------------------------------- aggregation
    @staticmethod
    def detect_target_label(question: str) -> str | None:
        fq = fold_text(question)
        for pattern, label in _AGG_TARGETS:
            if re.search(rf"(?:bao nhieu|may|cac|nhung|liet ke|danh sach|dem|tong so)\s+(?:\w+\s+){{0,2}}{pattern}", fq):
                return label
        for pattern, label in _AGG_TARGETS:
            if re.search(rf"{pattern}\s+(?:\w+\s+){{0,3}}nao", fq):
                return label
        return None

    @staticmethod
    def detect_stock_filter(question: str) -> tuple[str, tuple[str, ...]] | None:
        fq = fold_text(question)
        for key, accepted in STOCK_STATUS_FILTERS:
            if key in fq:
                return key, accepted
        return None

    def aggregate(self, question: str, mentions: list[EntityMention], *, store_code: str | None = None,
                  extraction_method: str = "rule") -> GraphSearchResult:
        result = GraphSearchResult(mode="aggregation", entities=mentions, extraction_method=extraction_method)
        target = self.detect_target_label(question)
        if target is None:
            result.warnings.append("Không xác định được đối tượng cần đếm/liệt kê.")
            return result
        stock_filter = self.detect_stock_filter(question)
        anchors = [m for m in mentions if m.label != target]
        if store_code and target != "Store" and not any(m.label == "Store" for m in anchors) and stock_filter:
            anchors.append(EntityMention("Store", store_code, store_code, "user_scope"))

        def stock_ok(edge: GraphEdge) -> bool:
            if edge.type != "STOCKS" or stock_filter is None:
                return True
            status = fold_text(edge.props.get("trang_thai") or "")
            return any(a in status for a in stock_filter[1])

        matched: dict[str, dict[str, Any]] = {}
        with tracing.span("graph.aggregate", input={"target": target, "anchors": [m.id for m in anchors],
                                                     "stock_filter": stock_filter[0] if stock_filter else None}) as sp:
            if anchors:
                per_anchor: list[dict[str, dict[str, Any]]] = []
                for a in anchors:
                    found: dict[str, dict[str, Any]] = {}
                    for p in self.store.paths_from([NodeRef(a.label, a.id)], self.cfg.graph.max_hops, limit=5000):
                        end = p.nodes[-1]
                        if end.label != target or end.props.get("level") == "article" or (end.label, end.id) == (a.label, a.id):
                            continue
                        if stock_filter and not any(e.type == "STOCKS" for e in p.edges):
                            continue
                        if not all(stock_ok(e) for e in p.edges):
                            continue
                        entry = found.setdefault(end.id, {"node": end, "records": []})
                        for e in p.edges:
                            entry["records"].extend(e.source_chunk_ids)
                    per_anchor.append(found)
                common = set(per_anchor[0]).intersection(*[set(f) for f in per_anchor[1:]]) if per_anchor else set()
                for key in common:
                    recs: list[str] = []
                    for f in per_anchor:
                        recs.extend(f[key]["records"])
                    matched[key] = {"node": per_anchor[0][key]["node"], "records": recs}
            elif target == "SKU" and stock_filter:
                for e in self.store.edges_of_type("STOCKS"):
                    if stock_ok(e) and (not store_code or e.src.id == store_code):
                        entry = matched.setdefault(e.dst.id, {"node": GraphNode("SKU", e.dst.id, {"id": e.dst.id}), "records": []})
                        entry["records"].extend(e.source_chunk_ids)
            else:
                for n in self.store.list_nodes(target):
                    if n.props.get("level") == "article":
                        continue
                    matched[n.id] = {"node": n, "records": n.source_chunk_ids[:1]}
            sp.update(output={"count": len(matched)})

        items = sorted(matched.values(), key=lambda v: v["node"].id)
        names = []
        for v in items[:30]:
            node = self.store.get_node(target, v["node"].id) if target == "SKU" and not v["node"].props.get("name") else v["node"]
            names.append(node_display(target, (node or v["node"]).props))
        anchor_txt = ", ".join(node_display(m.label, {"id": m.id}) for m in anchors) or "toàn hệ thống"
        filter_txt = f", trạng thái tồn kho '{stock_filter[0]}'" if stock_filter else ""
        label_vi = NODE_LABELS_VI.get(target, target).lower()
        result.aggregation = {"target": target, "count": len(items), "anchors": [m.id for m in anchors],
                              "stock_filter": stock_filter[0] if stock_filter else None, "items": names}
        result.computed_facts.append(
            f"Số {label_vi} thoả điều kiện ({anchor_txt}{filter_txt}): {len(items)}."
            + (f" Danh sách: {'; '.join(names)}" + (" ..." if len(items) > len(names) else "") + "." if names else ""))
        seen: set[str] = set()
        for v in items:
            for rid in v["records"]:
                if rid not in seen:
                    seen.add(rid)
                    result.record_ids.append(rid)
        result.record_ids = result.record_ids[: self.cfg.graph.max_chunks * 3]
        return result
