"""Decision trace (B5) — lời giải thích cho supervisor, dựng TỪ DỮ KIỆN THẬT của pipeline (router,
bộ lọc, kết quả retrieval kèm điểm, đường đi graph, citation được dùng, mâu thuẫn, fallback) — KHÔNG
dùng chain-of-thought hay lời tự giải thích của model.

Cấu trúc trả về (field `decision_trace` của response):
    understood  — đã hiểu câu hỏi là gì (loại câu hỏi, thực thể, phạm vi cửa hàng, thời điểm, chủ đề tiền)
    searched    — đã tra ở đâu, bộ lọc gì, bao nhiêu ứng viên
    found       — tìm được gì (nguồn + điểm), cái nào được dùng trong câu trả lời
    graph_paths — đường đi graph làm căn cứ (nếu có)
    basis       — kết luận dựa vào nguồn nào
    confidence  — mức tin cậy (cao/trung bình/thấp) + lý do tính điểm
    degraded    — thành phần lỗi đã được thay thế (reranker/graph/...)
    summary     — 5 dòng tóm tắt tiếng Việt cho UI
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionTrace:
    understood: dict[str, Any] = field(default_factory=dict)
    searched: list[dict[str, Any]] = field(default_factory=list)
    found: list[dict[str, Any]] = field(default_factory=list)
    graph_paths: list[str] = field(default_factory=list)
    computed_facts: list[str] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    confidence: dict[str, Any] = field(default_factory=dict)
    degraded: list[str] = field(default_factory=list)
    outcome: str = ""

    def add_search(self, where: str, **detail: Any) -> None:
        self.searched.append({"where": where, **detail})

    def degrade(self, message: str) -> None:
        if message not in self.degraded:
            self.degraded.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {"understood": self.understood, "searched": self.searched, "found": self.found,
                "graph_paths": self.graph_paths, "computed_facts": self.computed_facts, "basis": self.basis,
                "conflicts": self.conflicts, "confidence": self.confidence, "degraded": self.degraded,
                "outcome": self.outcome, "summary": self.summary()}

    def summary(self) -> list[str]:
        u = self.understood
        qtype_vi = {"lookup": "tra cứu 1 thông tin", "multi_hop": "câu hỏi nối nhiều thực thể",
                    "aggregation": "câu hỏi đếm/liệt kê"}.get(u.get("question_type", ""), u.get("question_type", ""))
        ents = ", ".join(f"{e['type']} {e['id']}" for e in u.get("entities", [])) or "không nhận ra thực thể cụ thể"
        lines = [f"Đã hiểu: {qtype_vi}; thực thể: {ents}; phạm vi: {u.get('store_scope', 'toàn hệ thống')}"
                 + (f"; thời điểm: {u['as_of']}" if u.get("as_of") else "")]
        where = "; ".join(f"{s['where']}" + (f" ({s.get('result')})" if s.get("result") else "") for s in self.searched)
        lines.append(f"Đã tra: {where or 'không tra cứu'}")
        used = [f for f in self.found if f.get("used_in_answer")]
        lines.append(f"Tìm được: {len(self.found)} đoạn liên quan, dùng {len(used)} đoạn làm căn cứ"
                     + (f"; {len(self.graph_paths)} đường đi trên graph" if self.graph_paths else ""))
        lines.append("Dựa vào: " + ("; ".join(self.basis) if self.basis else "không có nguồn đủ tin cậy"))
        c = self.confidence
        lines.append(f"Độ tin cậy: {c.get('level', 'không xác định')}" + (f" ({c.get('score'):.2f})" if isinstance(c.get("score"), float) else "")
                     + (f" — {c['explanation']}" if c.get("explanation") else ""))
        if self.conflicts:
            lines.append(f"Cảnh báo: {len(self.conflicts)} mâu thuẫn giữa các nguồn — đã trình bày đủ các phương án.")
        if self.degraded:
            lines.append("Chạy chế độ dự phòng: " + "; ".join(self.degraded))
        return lines


def compute_confidence(*, top_score: float | None, score_kind: str, coverage: float, conflicts: int,
                       degraded: int, graph_paths: int = 0) -> dict[str, Any]:
    """Điểm tin cậy XÁC ĐỊNH từ tín hiệu retrieval thật (không hỏi model tự chấm)."""
    reasons = []
    if score_kind == "rerank" and top_score is not None:
        base = top_score
        reasons.append(f"điểm rerank cao nhất {top_score:.2f}")
    elif score_kind == "graph":
        base = 0.75 if graph_paths >= 2 else 0.6
        reasons.append(f"{graph_paths} đường đi graph khớp thực thể được hỏi")
    elif score_kind == "dense" and top_score is not None:
        base = min(0.75, max(0.0, top_score))
        reasons.append(f"tương đồng ngữ nghĩa cao nhất {top_score:.2f} (không rerank)")
    else:
        base = 0.3
        reasons.append("không có điểm retrieval")
    score = base * (0.5 + 0.5 * coverage)
    if coverage < 1.0:
        reasons.append(f"{coverage:.0%} câu khẳng định có trích dẫn")
    if conflicts:
        score -= 0.2
        reasons.append("nguồn mâu thuẫn nhau")
    if degraded:
        score -= 0.1
        reasons.append("một thành phần chạy chế độ dự phòng")
    score = max(0.0, min(1.0, score))
    level = "cao" if score >= 0.7 else ("trung bình" if score >= 0.45 else "thấp")
    return {"level": level, "score": round(score, 3), "explanation": ", ".join(reasons)}
