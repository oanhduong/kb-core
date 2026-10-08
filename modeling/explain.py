"""Diễn giải spec thành câu nghiệp vụ (vi + en) — sinh bằng code từ chính quy tắc, nên câu chữ người dùng duyệt
luôn khớp 100% với logic view thực thi (không để LLM tự viết mô tả có thể lệch logic)."""
from __future__ import annotations

import re
from typing import Any

from modeling import expr as E
from modeling.spec import Condition, Evidence, KeyPair, ModelSpec, OutputColumn, Relationship, Rule, Transform

Text = dict[str, str]


def _t(en: str, vi: str) -> Text:
    return {"en": en, "vi": vi}


def _label(spec: ModelSpec, table: str | None, column: str | None) -> str:
    t = spec.table(table)
    if t is None or not column:
        return column or ""
    c = next((c for c in t.columns if c.name == column), None)
    return c.label if c else column


def _tname(spec: ModelSpec, table: str | None) -> str:
    t = spec.table(table)
    return t.title if t else (table or "")


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return str(v)


def transform_text(ts: list[Transform]) -> Text | None:
    if not ts:
        return None
    en, vi = [], []
    for t in ts:
        a = t.args
        if t.op == "number_part":
            en.append("only the digits (STE0870 → 870)")
            vi.append("chỉ phần chữ số (STE0870 → 870)")
        elif t.op == "first_token":
            en.append("the part before the first separator (8644-5686 → 8644)")
            vi.append("phần trước dấu phân cách đầu tiên (8644-5686 → 8644)")
        elif t.op == "remove_prefix":
            en.append(f"without the prefix “{a[0] if a else ''}”")
            vi.append(f"bỏ tiền tố “{a[0] if a else ''}”")
        elif t.op == "remove_suffix":
            en.append(f"without the suffix “{a[0] if a else ''}”")
            vi.append(f"bỏ hậu tố “{a[0] if a else ''}”")
        elif t.op == "left":
            en.append(f"the first {a[0] if a else ''} characters")
            vi.append(f"{a[0] if a else ''} ký tự đầu")
        elif t.op == "right":
            en.append(f"the last {a[0] if a else ''} characters")
            vi.append(f"{a[0] if a else ''} ký tự cuối")
        elif t.op == "replace":
            en.append(f"replacing “{a[0] if a else ''}” with “{a[1] if len(a) > 1 else ''}”")
            vi.append(f"thay “{a[0] if a else ''}” bằng “{a[1] if len(a) > 1 else ''}”")
    if not en:
        return None
    return _t(", ".join(en), ", ".join(vi))


def keys_text(spec: ModelSpec, rel: Relationship) -> Text:
    en, vi = [], []
    for kp in rel.keys:
        b = _label(spec, rel.target_table, kp.base_column)
        s = _label(spec, rel.source_table, kp.source_column)
        same = b.strip().lower() == s.strip().lower()
        e = f"{b}" if same else f"{b} = {s}"
        v = e
        for side, ts in (("main", kp.base_transforms), ("src", kp.source_transforms)):
            tt = transform_text(ts)
            if tt:
                who_en = b if side == "main" else s
                e += f" (using {tt['en']} of {who_en})"
                v += f" (dùng {tt['vi']} của {who_en})"
        en.append(e)
        vi.append(v)
    return _t(" and ".join(en), " và ".join(vi))


def relationship_text(spec: ModelSpec, rel: Relationship) -> Text:
    src, tgt = _tname(spec, rel.source_table), _tname(spec, rel.target_table)
    k = keys_text(spec, rel)
    if rel.cardinality == "one_to_one":
        many_en, many_vi = "at most one matching row", "tối đa một dòng tương ứng"
    else:
        many_en, many_vi = "possibly several rows", "có thể nhiều dòng"
    cov = f" {round(rel.coverage * 100)}% of rows in “{tgt}” find a match." if rel.coverage is not None else ""
    cov_vi = f" {round(rel.coverage * 100)}% dòng của “{tgt}” tìm được dòng khớp." if rel.coverage is not None else ""
    return _t(f"“{src}” connects to “{tgt}” by {k['en']} — each row of “{tgt}” has {many_en} in “{src}”.{cov}",
              f"“{src}” nối với “{tgt}” qua {k['vi']} — mỗi dòng của “{tgt}” có {many_vi} trong “{src}”.{cov_vi}")


def condition_text(spec: ModelSpec, table: str | None, c: Condition) -> Text:
    col = _label(spec, table, c.column)
    if c.as_of_offset_days is not None:
        n = c.as_of_offset_days
        ref_en = "the report date" if n == 0 else f"the report date {'+' if n > 0 else '−'} {abs(n)} days"
        ref_vi = "ngày báo cáo" if n == 0 else f"ngày báo cáo {'+' if n > 0 else '−'} {abs(n)} ngày"
        word = {"gt": ("is after", "sau"), "gte": ("is on or after", "từ"), "lt": ("is before", "trước"), "lte": ("is on or before", "đến hết"),
                "eq": ("is", "là"), "ne": ("is not", "khác")}.get(c.op, (c.op, c.op))
        return _t(f"{col} {word[0]} {ref_en}", f"{col} {word[1]} {ref_vi}")
    v = c.value
    vals = v if isinstance(v, list) else [v]
    vs = ", ".join(f"“{_fmt(x)}”" for x in vals)
    m = {
        "eq": (f"{col} is {vs}", f"{col} là {vs}"), "ne": (f"{col} is not {vs}", f"{col} khác {vs}"),
        "gt": (f"{col} is greater than {vs}", f"{col} lớn hơn {vs}"), "gte": (f"{col} is at least {vs}", f"{col} từ {vs} trở lên"),
        "lt": (f"{col} is less than {vs}", f"{col} nhỏ hơn {vs}"), "lte": (f"{col} is at most {vs}", f"{col} không quá {vs}"),
        "in": (f"{col} is one of {vs}", f"{col} thuộc {vs}"), "not_in": (f"{col} is not one of {vs}", f"{col} không thuộc {vs}"),
        "is_null": (f"{col} is empty", f"{col} để trống"), "not_null": (f"{col} is filled in", f"{col} có giá trị"),
        "contains": (f"{col} contains {vs}", f"{col} chứa {vs}"), "not_contains": (f"{col} does not contain {vs}", f"{col} không chứa {vs}"),
        "starts_with": (f"{col} starts with {vs}", f"{col} bắt đầu bằng {vs}"),
    }
    en, vi = m.get(c.op, (f"{col} {c.op} {vs}", f"{col} {c.op} {vs}"))
    return _t(en, vi)


def _is_date(spec: ModelSpec, table: str | None, column: str | None) -> bool:
    t = spec.table(table)
    c = next((c for c in t.columns if c.name == column), None) if t and column else None
    return bool(c and c.type in ("date", "timestamp"))


def _pretty_formula(spec: ModelSpec, text: str) -> Text:
    try:
        node = E.parse(text)
    except E.ExprError:
        return _t(f"= {text}", f"= {text}")
    if isinstance(node, E.Call) and node.fn == "DAYS":
        a, b = node.args
        if isinstance(b, E.Col) and isinstance(a, E.Call) and a.fn in ("AS_OF", "TODAY"):
            return _t(f"Days from {b.name} to the report date", f"Số ngày từ {b.name} đến ngày báo cáo")
        if isinstance(a, E.Col) and isinstance(b, E.Call) and b.fn in ("AS_OF", "TODAY"):
            return _t(f"Days from the report date to {a.name}", f"Số ngày từ ngày báo cáo đến {a.name}")
        if isinstance(a, E.Col) and isinstance(b, E.Col):
            return _t(f"Days from {b.name} to {a.name}", f"Số ngày từ {b.name} đến {a.name}")
    if isinstance(node, E.Bin) and isinstance(node.left, E.Col) and isinstance(node.right, E.Col):
        words = {"-": ("minus", "trừ"), "+": ("plus", "cộng"), "*": ("times", "nhân"), "/": ("divided by", "chia")}
        if node.op in words:
            w = words[node.op]
            return _t(f"{node.left.name} {w[0]} {node.right.name}", f"{node.left.name} {w[1]} {node.right.name}")
    shown = E.to_text(node).replace("AS_OF()", "REPORT_DATE()")
    return _t(f"Calculated: {shown}", f"Tính theo công thức: {shown.replace('REPORT_DATE()', 'NGÀY_BÁO_CÁO()')}")


def rule_text(spec: ModelSpec, rule: Rule, *, column_type: str | None = None) -> Text:
    main = spec.main()
    main_title = main.title if main else ""
    k = rule.kind
    if k == "input":
        return _t(f"Entered directly in “{main_title}” — kept as is (an input, not a calculation).",
                  f"Nhập trực tiếp trong “{main_title}” — giữ nguyên (là dữ liệu đầu vào, không phải phép tính).")
    if k == "constant":
        return _t(f"Always {_fmt(rule.value)}.", f"Luôn bằng {_fmt(rule.value)}.")
    if k == "unresolved":
        return _t("Not worked out yet — pick a suggestion, describe the rule in your own words, or mark it as entered by hand.",
                  "Chưa xác định — chọn một gợi ý, mô tả quy tắc bằng lời, hoặc đánh dấu là nhập tay.")
    if k == "formula":
        out = _pretty_formula(spec, rule.expression or "")
    else:
        rel = spec.relationship(rule.relationship_id)
        table = rule.table or (rel.source_table if rel else None)
        tname = _tname(spec, table)
        col = _label(spec, table, rule.column)
        keys = keys_text(spec, rel) if rel else _t("?", "?")
        if k == "lookup":
            out = _t(f"Taken from “{tname}” → {col}, on the row with the same {keys['en']}.",
                     f"Lấy từ “{tname}” → cột {col}, ở dòng có cùng {keys['vi']}.")
        else:
            date = _is_date(spec, table, rule.column)
            f = rule.func or "count"
            head = {
                "count": (f"Number of rows in “{tname}”", f"Số dòng trong “{tname}”"),
                "count_distinct": (f"Number of different {col} in “{tname}”", f"Số {col} khác nhau trong “{tname}”"),
                "sum": (f"Total {col} in “{tname}”", f"Tổng {col} trong “{tname}”"),
                "avg": (f"Average {col} in “{tname}”", f"Trung bình {col} trong “{tname}”"),
                "min": ((f"Earliest {col}" if date else f"Smallest {col}") + f" in “{tname}”",
                        (f"{col} sớm nhất" if date else f"{col} nhỏ nhất") + f" trong “{tname}”"),
                "max": ((f"Latest {col}" if date else f"Largest {col}") + f" in “{tname}”",
                        (f"{col} muộn nhất" if date else f"{col} lớn nhất") + f" trong “{tname}”"),
                "first": (f"{col} in “{tname}”", f"{col} trong “{tname}”"),
            }[f]
            en = f"{head[0]} for the same {keys['en']}"
            vi = f"{head[1]} có cùng {keys['vi']}"
            if rule.filters:
                conds = [condition_text(spec, table, c) for c in rule.filters]
                en += ", counting only rows where " + " and ".join(c["en"] for c in conds) if f == "count" else \
                    ", only rows where " + " and ".join(c["en"] for c in conds)
                vi += ", chỉ tính các dòng có " + " và ".join(c["vi"] for c in conds)
            out = _t(en + ".", vi + ".")
    if rule.default is not None and k in ("lookup", "aggregate") and not (rule.func in ("count",) and rule.default == 0):
        out = _t(out["en"] + f" If nothing matches: {_fmt(rule.default)}.", out["vi"] + f" Nếu không có dòng khớp: {_fmt(rule.default)}.")
    if rule.round_digits is not None:
        out = _t(out["en"] + f" Rounded to {rule.round_digits} decimals.", out["vi"] + f" Làm tròn {rule.round_digits} chữ số thập phân.")
    return out


def evidence_text(spec: ModelSpec, ev: Evidence) -> Text:
    d = ev.detail
    if ev.kind == "formula":
        f = d.get("formula", "")
        share = d.get("share")
        extra_en = f" (in {round(share * 100)}% of rows)" if share is not None and share < 0.999 else ""
        extra_vi = f" (ở {round(share * 100)}% số dòng)" if share is not None and share < 0.999 else ""
        return _t(f"Excel formula {f}{extra_en}", f"Công thức Excel {f}{extra_vi}")
    if ev.kind == "pivot":
        return _t(f"Pivot table “{d.get('name')}” on sheet “{d.get('sheet')}” ({d.get('summary', '')})",
                  f"Pivot table “{d.get('name')}” ở sheet “{d.get('sheet')}” ({d.get('summary', '')})")
    if ev.kind == "data_match":
        m, c = d.get("matched", 0), d.get("compared", 0)
        return _t(f"Reproduces {m} of {c} values in the Excel file", f"Tính lại khớp {m}/{c} giá trị trong file Excel")
    if ev.kind == "name_match":
        return _t("Column names are similar", "Tên cột tương đồng")
    if ev.kind == "ai":
        return _t(f"Suggested by AI from column meanings{': ' + ev.note if ev.note else ''}",
                  f"AI gợi ý dựa trên ý nghĩa cột{': ' + ev.note if ev.note else ''}")
    if ev.kind == "user":
        who = d.get("by") or ""
        return _t(f"Set by {who}".strip(), f"Do {who} thiết lập".strip() if who else "Do người dùng thiết lập")
    if ev.kind == "structure":
        return _t(ev.note or "Sheet structure", ev.note or "Cấu trúc sheet")
    return _t(ev.kind, ev.kind)


NOTICES: dict[str, Text] = {
    "main_no_formulas": _t("The main sheet has no formulas (values were pasted). The logic below was worked out from the data, pivot tables and column meanings — please review each column.",
                           "Sheet chính không có công thức (giá trị đã được dán). Logic bên dưới được suy ra từ dữ liệu, pivot table và ý nghĩa cột — vui lòng duyệt từng cột."),
    "main_has_formulas": _t("Logic was read from {count} Excel formulas in the main sheet.", "Logic được đọc từ {count} công thức Excel trong sheet chính."),
    "duplicate_keys": _t("“{column}” repeats in the main sheet ({rows} rows, {distinct} distinct). The view keeps one row per Excel row.",
                         "“{column}” bị lặp trong sheet chính ({rows} dòng, {distinct} giá trị khác nhau). View giữ đúng mỗi dòng Excel một dòng."),
    "pivots_found": _t("Found {count} pivot tables — used as evidence for summary calculations.",
                       "Tìm thấy {count} pivot table — dùng làm bằng chứng cho các phép tổng hợp."),
    "as_of_inferred": _t("“Days since …” columns imply a report date of {date}. You can change it.",
                         "Các cột “số ngày kể từ …” cho thấy ngày báo cáo là {date}. Bạn có thể đổi."),
    "unmatched_columns": _t("{count} calculated columns could not be reproduced from the supporting sheets.",
                            "{count} cột tính toán chưa tái tạo được từ các sheet phụ."),
    "unlinked_tables": _t("{count} sheets have no detectable link to the main sheet.", "{count} sheet không tìm thấy liên kết với sheet chính."),
    "truncated": _t("“{table}” was cut at {rows} rows.", "“{table}” bị cắt ở {rows} dòng."),
    "no_llm": _t("AI suggestions are off (no model configured) — only formulas, pivots and data patterns were used.",
                 "Gợi ý AI đang tắt (chưa cấu hình model) — chỉ dùng công thức, pivot và mẫu dữ liệu."),
}


def notice_text(code: str, params: dict[str, Any]) -> Text:
    tpl = NOTICES.get(code, _t(code, code))
    safe = {k: _fmt(v) for k, v in params.items()}
    return {lang: re.sub(r"\{(\w+)\}", lambda m: safe.get(m.group(1), m.group(0)), s) for lang, s in tpl.items()}


def column_text(spec: ModelSpec, col: OutputColumn) -> Text:
    return rule_text(spec, col.rule, column_type=col.type)


def keypair_text(spec: ModelSpec, rel: Relationship, kp: KeyPair) -> Text:
    return keys_text(spec, rel.model_copy(update={"keys": [kp]}))


def rule_formula(spec: ModelSpec, rule: Rule) -> str:
    """Dạng kỹ thuật gọn của quy tắc (cho tài liệu/người kỹ thuật) — vẫn dùng tên nghiệp vụ, không phải SQL."""
    k = rule.kind
    main = spec.main()
    if k == "input":
        return f"= {main.title if main else ''}[{_label(spec, spec.main_table, rule.column) if rule.column else '…'}]"
    if k == "constant":
        return f"= {_fmt(rule.value)}"
    if k == "formula":
        return "= " + (rule.expression or "")
    if k == "unresolved":
        return "—"
    rel = spec.relationship(rule.relationship_id)
    table = rule.table or (rel.source_table if rel else None)
    tname = _tname(spec, table)
    col = _label(spec, table, rule.column) if rule.column else "*"
    on = ", ".join(f"{_label(spec, rel.target_table, kp.base_column)} = {_label(spec, table, kp.source_column)}" for kp in rel.keys) if rel else "?"
    sym = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "in": "IN", "not_in": "NOT IN", "is_null": "IS EMPTY",
           "not_null": "IS NOT EMPTY", "contains": "CONTAINS", "not_contains": "NOT CONTAINS", "starts_with": "STARTS WITH"}
    conds = []
    for c in rule.filters:
        rhs = (f"REPORT_DATE{'+' if (c.as_of_offset_days or 0) >= 0 else '-'}{abs(c.as_of_offset_days or 0)}d" if c.as_of_offset_days is not None
               else ("(" + ", ".join(_fmt(v) for v in c.value) + ")" if isinstance(c.value, list) else ("" if c.op in ("is_null", "not_null") else repr(_fmt(c.value)))))
        conds.append(f"{_label(spec, table, c.column)} {sym.get(c.op, c.op)} {rhs}".strip())
    where = f" WHERE {' AND '.join(conds)}" if conds else ""
    if k == "lookup":
        return f"LOOKUP({tname}.{col}) ON {on}{where}"
    f = (rule.func or "count").upper()
    out = f"{f}({tname}.{col}) ON {on}{where}"
    if rule.default is not None:
        out += f" ELSE {_fmt(rule.default)}"
    return out
