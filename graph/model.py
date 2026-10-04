"""Cấu trúc trung gian thuần Python cho phần đồ thị đang build — tách khỏi graph store (AGE/memory,
xem graph/store.py) để logic build test được không cần Postgres/AGE thật.

GraphAccumulator gộp (idempotent) theo khoá (label, id) cho node và (type, from, to) cho edge: gọi
add_node/add_edge nhiều lần cho CÙNG 1 thực thể chỉ tạo 1 node/edge, source_chunk_ids gộp không
trùng lặp. `document_id` của accumulator (nếu có) được gắn vào source_docs của MỌI node/edge sinh ra
— đây là khoá để cập nhật incremental: xoá đúng phần đóng góp của 1 document (graph/store.py).
"""
from __future__ import annotations

from collections import Counter
from typing import Any


class GraphAccumulator:
    def __init__(self, document_id: str | None = None) -> None:
        self.document_id = document_id
        self.nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    def _docs(self) -> list[str]:
        return [self.document_id] if self.document_id else []

    def add_node(self, label: str, node_id: str, properties: dict[str, Any] | None = None,
                 source_chunk_ids: list[str] | None = None) -> dict[str, Any]:
        key = (label, node_id)
        node = self.nodes.get(key)
        if node is None:
            node = {"label": label, "id": node_id, "properties": {}, "source_chunk_ids": [],
                    "source_docs": self._docs()}
            self.nodes[key] = node
        node["properties"].update(properties or {})
        _extend_unique(node["source_chunk_ids"], source_chunk_ids)
        return node

    def add_edge(self, edge_type: str, from_label: str, from_id: str, to_label: str, to_id: str,
                 properties: dict[str, Any] | None = None, source_chunk_ids: list[str] | None = None) -> dict[str, Any]:
        key = (edge_type, from_label, from_id, to_label, to_id)
        edge = self.edges.get(key)
        if edge is None:
            edge = {
                "type": edge_type, "from_label": from_label, "from_id": from_id,
                "to_label": to_label, "to_id": to_id, "properties": {}, "source_chunk_ids": [],
                "source_docs": self._docs(),
            }
            self.edges[key] = edge
        edge["properties"].update(properties or {})
        _extend_unique(edge["source_chunk_ids"], source_chunk_ids)
        return edge

    def has_node(self, label: str, node_id: str) -> bool:
        return (label, node_id) in self.nodes

    def node_list(self) -> list[dict[str, Any]]:
        return list(self.nodes.values())

    def edge_list(self) -> list[dict[str, Any]]:
        return list(self.edges.values())

    def stats(self) -> dict[str, Any]:
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "node_count_by_label": dict(Counter(n["label"] for n in self.nodes.values())),
            "edge_count_by_type": dict(Counter(e["type"] for e in self.edges.values())),
        }


def _extend_unique(target: list[str], values: list[str] | None) -> None:
    for v in values or []:
        if v not in target:
            target.append(v)
