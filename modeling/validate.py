"""Đối chiếu kết quả view (đọc từ warehouse) với sheet chính của file gốc — DoD "view khớp Excel ở các trường chính".

Mỗi cột: input (sao chép, chỉ kiểm khớp khoá/dòng) | match (khớp 100%) | partial (>= 90%) | mismatch | not_calculated (chưa có
quy tắc -> NULL). score = số ô khớp / số ô đã so trên các cột tính toán.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from modeling.evaluate import compare_frames
from modeling.spec import ModelSpec, now_iso


def validate(spec: ModelSpec, base: pd.DataFrame, view: pd.DataFrame) -> dict[str, Any]:
    checks = compare_frames(spec, base, view)
    cols: dict[str, Any] = {}
    summary = {"calculated": 0, "matched": 0, "partial": 0, "mismatch": 0, "not_calculated": 0, "inputs": 0}
    cells_cmp = cells_ok = 0
    for c in spec.columns:
        ch = checks.get(c.name)
        kind = c.rule.kind
        if kind in ("input",):
            summary["inputs"] += 1
            cols[c.name] = {"kind": "input", **(ch.model_dump() if ch else {})}
            continue
        summary["calculated"] += 1
        if kind == "unresolved":
            summary["not_calculated"] += 1
            cols[c.name] = {"kind": "not_calculated", **(ch.model_dump() if ch else {})}
            continue
        d = ch.model_dump() if ch else {"compared": 0, "matched": 0, "rate": None}
        rate = d.get("rate")
        if rate is None:
            status = "no_data"
        elif rate >= 0.999:
            status = "match"
            summary["matched"] += 1
        elif rate >= 0.9:
            status = "partial"
            summary["partial"] += 1
        else:
            status = "mismatch"
            summary["mismatch"] += 1
        cells_cmp += d.get("compared") or 0
        cells_ok += d.get("matched") or 0
        cols[c.name] = {"kind": status, **d}
    rows_ok = set(base["_row"].tolist()) == set(view["source_row"].tolist()) if "source_row" in view else False
    return {"validated_at": now_iso(), "excel_rows": int(len(base)), "view_rows": int(len(view)), "rows_aligned": bool(rows_ok),
            "score": (cells_ok / cells_cmp) if cells_cmp else None, "summary": summary, "columns": cols}
