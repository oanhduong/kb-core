"""Chart spec (Vega-Lite v5) từ kết quả truy vấn — thuần, test được.

build_chart(result, hint): hint do LLM/nguời dùng gợi ý {type, x, y, series, title}; thiếu/sai cột ->
tự chọn theo heuristic (thời gian -> line, nhóm -> bar, 2 số -> scatter, 1 ô -> kpi). Dữ liệu nhúng
inline (giới hạn chart_max_points); UI render bằng vega-embed, export PDF/DOCX render PNG bằng vl-convert.
"""
from __future__ import annotations

from typing import Any

NUMERIC = ("integer", "bigint", "hugeint", "double", "float", "decimal", "real", "smallint", "tinyint", "ubigint", "uinteger", "percent")
TEMPORAL = ("date", "timestamp", "timestamp with time zone", "time")
MAX_CATEGORIES_PIE = 8
MAX_CATEGORIES_BAR = 60

PALETTE = ["#0f766e", "#2563eb", "#d97706", "#dc2626", "#7c3aed", "#059669", "#db2777", "#4b5563", "#ca8a04", "#0891b2"]


def _kind(col_type: str) -> str:
    t = (col_type or "").lower()
    if any(t.startswith(n) for n in NUMERIC) or t.startswith("decimal"):
        return "quantitative"
    if any(t.startswith(n) for n in TEMPORAL):
        return "temporal"
    return "nominal"


def _is_time_like(name: str, kind: str) -> bool:
    n = name.lower()
    return kind == "temporal" or any(k in n for k in ("thang", "month", "nam", "year", "ngay", "date", "quy", "quarter", "tuan", "week", "ky"))


def choose_encoding(columns: list[dict[str, str]], rows: list[list[Any]], hint: dict[str, Any] | None) -> dict[str, Any] | None:
    hint = dict(hint or {})
    names = [c["name"] for c in columns]
    kinds = {c["name"]: _kind(c["type"]) for c in columns}
    if not rows or not columns:
        return None
    if len(rows) == 1 and len(columns) == 1 and kinds[names[0]] == "quantitative":
        return {"type": "kpi", "value": names[0], "title": hint.get("title")}
    quant = [n for n in names if kinds[n] == "quantitative"]
    non_quant = [n for n in names if kinds[n] != "quantitative"]
    x = hint.get("x") if hint.get("x") in names else None
    y = hint.get("y")
    if isinstance(y, str):
        y = [y]
    y = [c for c in (y or []) if c in names and kinds[c] == "quantitative"] or None
    series = hint.get("series") if hint.get("series") in names else None
    ctype = hint.get("type")
    if ctype == "table":
        return {"type": "table", "title": hint.get("title")}
    if x is None:
        x = non_quant[0] if non_quant else (quant[0] if len(quant) >= 2 else None)
    if y is None:
        y = [c for c in quant if c != x][:3]
    if x is None or not y:
        return {"type": "table", "title": hint.get("title")}
    if series is None and len(non_quant) >= 2 and len(y) == 1:
        cand = [n for n in non_quant if n != x]
        distinct = len({r[names.index(cand[0])] for r in rows})
        if 1 < distinct <= 12:
            series = cand[0]
    n_cat = len({r[names.index(x)] for r in rows})
    if ctype not in ("bar", "line", "area", "pie", "scatter"):
        if _is_time_like(x, kinds[x]) and n_cat >= 3:
            ctype = "line"
        elif kinds[x] == "quantitative" and len(y) == 1:
            ctype = "scatter"
        elif n_cat <= MAX_CATEGORIES_PIE and len(y) == 1 and series is None and ctype == "pie":
            ctype = "pie"
        else:
            ctype = "bar"
    if ctype == "pie" and (n_cat > MAX_CATEGORIES_PIE or len(y) != 1):
        ctype = "bar"
    if ctype == "bar" and n_cat > MAX_CATEGORIES_BAR:
        return {"type": "table", "title": hint.get("title"), "note": f"{n_cat} nhóm — quá nhiều để vẽ cột"}
    return {"type": ctype, "x": x, "y": y, "series": series, "title": hint.get("title"), "x_kind": kinds[x],
            "horizontal": ctype == "bar" and n_cat > 12 and not _is_time_like(x, kinds[x])}


def vega_lite_spec(enc: dict[str, Any], columns: list[dict[str, str]], rows: list[list[Any]], max_points: int = 2000) -> dict[str, Any] | None:
    if enc is None or enc["type"] in ("table", "kpi"):
        return None
    names = [c["name"] for c in columns]
    data = [dict(zip(names, r)) for r in rows[:max_points]]
    x, ys, series = enc["x"], enc["y"], enc.get("series")
    x_type = "temporal" if enc.get("x_kind") == "temporal" else ("quantitative" if enc.get("x_kind") == "quantitative" and enc["type"] == "scatter" else "nominal")
    base: dict[str, Any] = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "title": enc.get("title") or "",
                            "data": {"values": data}, "width": "container", "height": 320,
                            "config": {"range": {"category": PALETTE}, "axis": {"labelFontSize": 11, "titleFontSize": 12},
                                       "legend": {"labelFontSize": 11}, "view": {"stroke": None}}}
    tooltip = [{"field": x, "type": x_type}] + [{"field": y, "type": "quantitative", "format": ",.2~f"} for y in ys]
    if series:
        tooltip.insert(1, {"field": series, "type": "nominal"})
    if enc["type"] == "pie":
        return {**base, "mark": {"type": "arc", "innerRadius": 60, "tooltip": True},
                "encoding": {"theta": {"field": ys[0], "type": "quantitative"}, "color": {"field": x, "type": "nominal"}, "tooltip": tooltip}}
    if enc["type"] == "scatter":
        return {**base, "mark": {"type": "point", "filled": True, "size": 60},
                "encoding": {"x": {"field": x, "type": "quantitative"}, "y": {"field": ys[0], "type": "quantitative"},
                             **({"color": {"field": series, "type": "nominal"}} if series else {}), "tooltip": tooltip}}
    mark = {"bar": {"type": "bar", "cornerRadiusEnd": 2}, "line": {"type": "line", "point": True}, "area": {"type": "area", "opacity": 0.75}}[enc["type"]]
    if len(ys) > 1:  # nhiều số liệu -> fold thành series
        spec = {**base, "transform": [{"fold": ys, "as": ["chi_so", "gia_tri"]}], "mark": mark,
                "encoding": {"x": {"field": x, "type": x_type, "sort": None if x_type == "temporal" else "-y" if enc["type"] == "bar" else None},
                             "y": {"field": "gia_tri", "type": "quantitative", "title": None},
                             "color": {"field": "chi_so", "type": "nominal", "title": None},
                             "xOffset" if enc["type"] == "bar" else "detail": {"field": "chi_so"},
                             "tooltip": [{"field": x, "type": x_type}, {"field": "chi_so", "type": "nominal"},
                                         {"field": "gia_tri", "type": "quantitative", "format": ",.2~f"}]}}
    else:
        enc_v: dict[str, Any] = {"x": {"field": x, "type": x_type, "sort": None if x_type == "temporal" else ("-y" if enc["type"] == "bar" and not series else None)},
                                 "y": {"field": ys[0], "type": "quantitative"}, "tooltip": tooltip}
        if series:
            enc_v["color"] = {"field": series, "type": "nominal"}
            if enc["type"] == "bar":
                enc_v["xOffset"] = {"field": series}
        spec = {**base, "mark": mark, "encoding": enc_v}
    if enc.get("horizontal"):
        e = spec["encoding"]
        e["x"], e["y"] = e["y"], e["x"]
        if "xOffset" in e:
            e["yOffset"] = e.pop("xOffset")
        spec["height"] = max(320, 18 * len(data))
    return spec


def build_chart(result: Any, hint: dict[str, Any] | None = None, max_points: int = 2000) -> dict[str, Any] | None:
    """-> {"type", "title", "encoding", "vega_lite" | None, "kpi" | None} hoặc None nếu không có gì để vẽ."""
    enc = choose_encoding(result.columns, result.rows, hint)
    if enc is None:
        return None
    out: dict[str, Any] = {"type": enc["type"], "title": enc.get("title") or (hint or {}).get("title") or "", "encoding": enc}
    if enc["type"] == "kpi":
        out["kpi"] = {"label": enc["value"], "value": result.rows[0][0]}
    elif enc["type"] == "table":
        out["note"] = enc.get("note")
    else:
        out["vega_lite"] = vega_lite_spec(enc, result.columns, result.rows, max_points)
    return out
