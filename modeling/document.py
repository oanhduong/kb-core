"""Tài liệu quan hệ của mô hình (vi | en): sheet chính & sheet phụ, quan hệ (nguồn -> đích, khoá nối, chuẩn hoá khoá),
logic từng cột (câu nghiệp vụ + dạng kỹ thuật), pivot table tìm thấy, kết quả đối chiếu với Excel, SQL của view.
Xuất cùng định dạng block của báo cáo -> tái dùng reports/export (HTML/PDF/DOCX) + Markdown thuần."""
from __future__ import annotations

from typing import Any

from modeling.explain import column_text, evidence_text, keys_text, notice_text, rule_formula, transform_text
from modeling.spec import ModelSpec

L = {
    "title": ("Data model — relationship document", "Mô hình dữ liệu — tài liệu quan hệ"),
    "overview": ("Overview", "Tổng quan"),
    "flow": ("Flow", "Luồng xử lý"),
    "sheets": ("Main sheet and supporting sheets", "Sheet chính và sheet phụ"),
    "rels": ("Relationships between sheets", "Quan hệ giữa các sheet"),
    "logic": ("Calculation logic of each column", "Logic tính của từng cột"),
    "pivots": ("Pivot tables found in the file", "Pivot table có trong file"),
    "validation": ("Validation against the Excel file", "Đối chiếu với file Excel"),
    "sql": ("Appendix — view definition (SQL)", "Phụ lục — định nghĩa view (SQL)"),
    "notes": ("Notes from the analysis", "Ghi chú từ bước phân tích"),
    "role_main": ("Main (business output)", "Chính (kết quả nghiệp vụ)"),
    "role_source": ("Supporting", "Phụ (nguồn dữ liệu)"),
    "role_ignored": ("Not used", "Không dùng"),
    "status_confirmed": ("Confirmed", "Đã duyệt"),
    "status_proposed": ("Proposed", "Đề xuất"),
    "status_rejected": ("Rejected", "Bị loại"),
}
KIND = {"input": ("Input", "Đầu vào"), "lookup": ("Lookup", "Tra cứu"), "aggregate": ("Summary", "Tổng hợp"),
        "formula": ("Formula", "Công thức"), "constant": ("Constant", "Hằng số"), "unresolved": ("Not worked out", "Chưa xác định")}


def _l(key: str, lang: str) -> str:
    en, vi = L[key]
    return vi if lang == "vi" else en


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f}%".replace(".0%", "%")


def _md_cell(v: Any) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")


def build_blocks(spec: ModelSpec, *, name: str, schema: str, validation: dict[str, Any] | None, view_sql: str | None,
                 lang: str = "vi") -> tuple[str, list[dict[str, Any]]]:
    vi = lang == "vi"
    main = spec.main()
    blocks: list[dict[str, Any]] = []
    tx = (lambda t: t["vi"]) if vi else (lambda t: t["en"])

    # ---------------------------------------------------------------- tổng quan
    blocks.append({"type": "heading", "text": _l("overview", lang)})
    asof = spec.parameters.as_of_date or ("hôm nay" if vi else "today")
    n_calc = sum(1 for c in spec.columns if c.rule.kind not in ("input", "constant"))
    lines = [
        f"- **{'Mô hình' if vi else 'Model'}:** {name}",
        f"- **{'Sheet chính' if vi else 'Main sheet'}:** {main.title if main else '—'} ({main.row_count if main else 0} {'dòng' if vi else 'rows'})",
        f"- **{'View kết quả' if vi else 'Result view'}:** `{schema}.{spec.view_name}`",
        f"- **{'Số sheet phụ' if vi else 'Supporting sheets'}:** {sum(1 for t in spec.tables if t.role == 'source')}",
        f"- **{'Quan hệ' if vi else 'Relationships'}:** {len(spec.active_relationships())}",
        f"- **{'Cột tính toán' if vi else 'Calculated columns'}:** {n_calc} / {len(spec.columns)}",
        f"- **{'Ngày báo cáo' if vi else 'Report date'}:** {asof}",
    ]
    if validation and validation.get("summary"):
        s = validation["summary"]
        lines.append(f"- **{'Khớp Excel' if vi else 'Matches Excel'}:** {s.get('matched', 0)}/{s.get('calculated', 0)} "
                     f"{'cột tính toán khớp hoàn toàn' if vi else 'calculated columns match fully'} · "
                     f"{'độ khớp tổng' if vi else 'overall'} {_pct(validation.get('score'))}")
    blocks.append({"type": "markdown", "text": "\n".join(lines)})
    blocks.append({"type": "heading", "text": _l("flow", lang)})
    flow = ("Excel sheets → Database tables (schema `{s}`) → Relationships & calculation logic (reviewed) → `{v}`" if not vi else
            "Sheet Excel → Bảng trong cơ sở dữ liệu (schema `{s}`) → Quan hệ & logic tính (đã duyệt) → `{v}`").format(s=schema, v=spec.view_name)
    blocks.append({"type": "markdown", "text": flow})
    if spec.notices:
        blocks.append({"type": "heading", "text": _l("notes", lang)})
        blocks.append({"type": "markdown", "text": "\n".join(f"- {tx(notice_text(n.code, n.params))}" for n in spec.notices)})

    # ---------------------------------------------------------------- sheets
    blocks.append({"type": "heading", "text": _l("sheets", lang)})
    cols = (["Sheet", "Vai trò", "Dòng", "Cột", "Bảng CSDL", "Công thức", "Pivot"] if vi else
            ["Sheet", "Role", "Rows", "Columns", "Database table", "Formulas", "Pivots"])
    rows = []
    for t in sorted(spec.tables, key=lambda t: (t.role != "main", t.role == "ignored", t.title)):
        rows.append([t.title, _l(f"role_{t.role}", lang), t.row_count, len(t.columns), f"{schema}.{t.key}", t.formula_count, len(t.pivots)])
    blocks.append({"type": "table", "columns": cols, "rows": rows})

    # ---------------------------------------------------------------- quan hệ
    blocks.append({"type": "heading", "text": _l("rels", lang)})
    cols = (["Sheet nguồn", "Sheet đích (chính)", "Khoá nối", "Chuẩn hoá khoá", "Số dòng nguồn / dòng đích", "Độ phủ", "Bằng chứng", "Trạng thái"] if vi else
            ["Source sheet", "Target (main) sheet", "Join keys", "Key normalisation", "Source rows per target row", "Coverage", "Evidence", "Status"])
    rows = []
    for r in spec.relationships:
        src, tgt = spec.table(r.source_table), spec.table(r.target_table)
        norm = []
        for kp in r.keys:
            for ts in (kp.base_transforms, kp.source_transforms):
                tt = transform_text(ts)
                if tt:
                    norm.append(tx(tt))
        rows.append([src.title if src else r.source_table, tgt.title if tgt else r.target_table, tx(keys_text(spec, r)), "; ".join(norm) or "—",
                     ("1" if r.cardinality == "one_to_one" else ("nhiều" if vi else "many")), _pct(r.coverage),
                     "; ".join(tx(evidence_text(spec, e)) for e in r.evidence[:2]), _l(f"status_{r.status}", lang)])
    blocks.append({"type": "table", "columns": cols, "rows": rows})

    # ---------------------------------------------------------------- logic cột
    blocks.append({"type": "heading", "text": _l("logic", lang)})
    cols = (["Cột", "Nhóm", "Loại", "Logic (nghiệp vụ)", "Sheet nguồn", "Trường liên quan", "Công thức / quy tắc", "Khớp Excel", "Trạng thái"] if vi else
            ["Column", "Group", "Kind", "Logic (business)", "Source sheet", "Related fields", "Formula / rule", "Matches Excel", "Status"])
    rows = []
    vcols = (validation or {}).get("columns", {})
    for c in spec.columns:
        r = c.rule
        rel = spec.relationship(r.relationship_id)
        src = spec.table(r.table or (rel.source_table if rel else None))
        fields = []
        if rel:
            fields += [kp.source_column for kp in rel.keys]
        if r.column:
            fields.append(r.column)
        fields += [f.column for f in r.filters]
        if src:
            labels = {x.name: x.label for x in src.columns}
            fields = [labels.get(f, f) for f in dict.fromkeys(fields)]
        rate = (vcols.get(c.name) or {}).get("rate") if vcols else (c.check.rate if c.check else None)
        rows.append([c.label, c.group or "", KIND[r.kind][1 if vi else 0], tx(column_text(spec, c)), src.title if src else (main.title if r.kind == "input" and main else ""),
                     ", ".join(fields), rule_formula(spec, r) + (f"  ⟨Excel: {c.excel_formula}⟩" if c.excel_formula else ""), _pct(rate),
                     _l(f"status_{c.status}", lang)])
    blocks.append({"type": "table", "columns": cols, "rows": rows})

    # ---------------------------------------------------------------- pivots
    pivs = [p for t in spec.tables for p in t.pivots]
    if pivs:
        blocks.append({"type": "heading", "text": _l("pivots", lang)})
        cols = ["Pivot", "Sheet", "Vùng" if vi else "Range", "Theo" if vi else "Rows", "Giá trị" if vi else "Values", "Bộ lọc" if vi else "Filters"]
        rows = [[p.name, p.sheet, p.location, ", ".join(p.rows), ", ".join(f"{v.func}({v.field})" for v in p.values),
                 "; ".join(f"{f.field}: " + (", ".join(f.selected) if f.selected else ("≠ " + ", ".join(f.excluded) if f.excluded else ("tất cả" if vi else "all")))
                           for f in p.filters)] for p in pivs]
        blocks.append({"type": "table", "columns": cols, "rows": rows})

    # ---------------------------------------------------------------- validation
    if validation and validation.get("columns"):
        blocks.append({"type": "heading", "text": _l("validation", lang)})
        s = validation.get("summary", {})
        txt = (f"{'Số dòng view' if vi else 'View rows'}: {validation.get('view_rows')} · {'Số dòng Excel' if vi else 'Excel rows'}: {validation.get('excel_rows')}\n\n"
               f"{'Cột khớp hoàn toàn' if vi else 'Fully matching columns'}: {s.get('matched', 0)} · {'Khớp một phần' if vi else 'Partially matching'}: {s.get('partial', 0)} · "
               f"{'Không khớp' if vi else 'Not matching'}: {s.get('mismatch', 0)} · {'Chưa tính' if vi else 'Not calculated'}: {s.get('not_calculated', 0)} · "
               f"{'Cột đầu vào' if vi else 'Input columns'}: {s.get('inputs', 0)}")
        blocks.append({"type": "markdown", "text": txt})
        cols = ["Cột" if vi else "Column", "Đã so" if vi else "Compared", "Khớp" if vi else "Matched", "Tỉ lệ" if vi else "Rate",
                "Ví dụ lệch (dòng: Excel → view)" if vi else "Example differences (row: Excel → view)"]
        rows = []
        for c in spec.columns:
            v = validation["columns"].get(c.name)
            if not v or v.get("kind") == "input":
                continue
            ex = "; ".join(f"{x['row']}: {x['expected']} → {x['actual']}" for x in (v.get("samples") or [])[:3])
            rows.append([c.label, v.get("compared"), v.get("matched"), _pct(v.get("rate")), ex])
        blocks.append({"type": "table", "columns": cols, "rows": rows})

    if view_sql:
        blocks.append({"type": "heading", "text": _l("sql", lang)})
        blocks.append({"type": "markdown", "text": f"```sql\nCREATE VIEW {schema}.{spec.view_name} AS\n{view_sql}\n```"})
    return f"{_l('title', lang)}: {name}", blocks


def to_markdown(title: str, blocks: list[dict[str, Any]]) -> str:
    out = [f"# {title}", ""]
    for b in blocks:
        t = b.get("type")
        if t == "heading":
            out += [f"## {b.get('text', '')}", ""]
        elif t == "markdown":
            out += [b.get("text", ""), ""]
        elif t == "table":
            cols = b.get("columns", [])
            out.append("| " + " | ".join(_md_cell(c) for c in cols) + " |")
            out.append("|" + "|".join("---" for _ in cols) + "|")
            for r in b.get("rows", []):
                out.append("| " + " | ".join(_md_cell(v) for v in r) + " |")
            out.append("")
    return "\n".join(out)
