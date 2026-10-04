"""Xuất báo cáo: DOCX (python-docx), PDF (HTML -> weasyprint), XLSX (mọi block bảng/chart data, mỗi block 1 sheet).
Chart Vega-Lite render PNG bằng vl-convert (không cần trình duyệt/node)."""
from __future__ import annotations

import base64
import html
import io
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_CITE_RE = re.compile(r"\[(\d+)\]")


def chart_png(vega_lite: dict[str, Any], scale: float = 2.0) -> bytes | None:
    try:
        import vl_convert as vlc

        spec = dict(vega_lite)
        if spec.get("width") == "container":
            spec["width"] = 640
        return vlc.vegalite_to_png(spec, scale=scale)
    except Exception as e:  # thiếu vl-convert/spec lỗi -> bỏ ảnh, giữ bảng số liệu
        logger.warning("render chart PNG lỗi: %s", e)
        return None


def _md_to_html(text: str) -> str:
    """Markdown tối giản (heading, bullet, bảng, đậm) -> HTML; đủ cho nội dung do model sinh."""
    lines = text.split("\n")
    out: list[str] = []
    in_ul = in_table = False
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
                out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if line.startswith(("- ", "* ", "• ")):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            lvl = len(m.group(1)) + 1
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
        elif line.strip() in ("---", "***"):
            out.append("<hr/>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    if in_ul:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    s = _CITE_RE.sub(r"<sup class='cite'>[\1]</sup>", s)
    return s


def report_html(title: str, blocks: list[dict[str, Any]], *, workspace_name: str = "", generated_at: str = "") -> str:
    parts = [f"<h1>{html.escape(title)}</h1>", f"<p class='meta'>{html.escape(workspace_name)} · {html.escape(generated_at)}</p>"]
    for b in blocks:
        t = b.get("type")
        if t == "heading":
            if b.get("level", 2) == 1:
                continue
            parts.append(f"<h2>{html.escape(b.get('text', ''))}</h2>")
        elif t == "markdown":
            parts.append(_md_to_html(b.get("text", "")))
        elif t == "chart":
            png = chart_png((b.get("chart") or {}).get("vega_lite") or {})
            if png:
                parts.append(f"<figure><img src='data:image/png;base64,{base64.b64encode(png).decode()}'/>"
                             f"<figcaption>{html.escape(b.get('title') or '')}</figcaption></figure>")
        elif t == "kpi":
            k = b.get("kpi") or {}
            parts.append(f"<div class='kpi'><div class='v'>{html.escape(str(k.get('value')))}</div><div class='l'>{html.escape(b.get('title') or k.get('label', ''))}</div></div>")
        elif t == "table":
            cols = [c["name"] if isinstance(c, dict) else str(c) for c in b.get("columns", [])]
            rows = b.get("rows", [])[:200]
            parts.append((f"<h4>{html.escape(b.get('title') or '')}</h4>" if b.get("title") else "") + "<table><tr>"
                         + "".join(f"<th>{html.escape(c)}</th>" for c in cols) + "</tr>"
                         + "".join("<tr>" + "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in r) + "</tr>" for r in rows) + "</table>")
        elif t == "sources":
            items = b.get("items") or []
            if items:
                parts.append("<h2>Nguồn tham chiếu</h2><ol class='sources'>" + "".join(
                    f"<li>{html.escape(it.get('label', ''))}" + (f" <span class='eff'>(hiệu lực {html.escape(str(it['effective_date']))})</span>" if it.get("effective_date") else "") + "</li>"
                    for it in items) + "</ol>")
    css = """
    body{font-family:'DejaVu Sans',Arial,sans-serif;font-size:11pt;color:#1c2430;line-height:1.5;margin:28px}
    h1{color:#0f766e;font-size:22pt;margin-bottom:4px} h2{color:#0f766e;font-size:15pt;margin-top:22px;border-bottom:1px solid #d9e2ec;padding-bottom:4px}
    h3,h4{font-size:12pt;margin:14px 0 6px} .meta{color:#64748b;font-size:9.5pt}
    table{border-collapse:collapse;width:100%;margin:8px 0;font-size:9.5pt} th,td{border:1px solid #d9e2ec;padding:4px 6px;text-align:left} th{background:#f1f5f9}
    figure{margin:12px 0;text-align:center} img{max-width:100%} figcaption{color:#64748b;font-size:9.5pt}
    .kpi{display:inline-block;border:1px solid #d9e2ec;border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0} .kpi .v{font-size:20pt;font-weight:700;color:#0f766e} .kpi .l{color:#64748b;font-size:9.5pt}
    sup.cite{color:#0f766e;font-size:8pt} ol.sources li{margin-bottom:2px} .eff{color:#64748b}
    """
    return f"<!doctype html><html lang='vi'><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{css}</style></head><body>{''.join(parts)}</body></html>"


def export_pdf(title: str, blocks: list[dict[str, Any]], **meta: Any) -> bytes:
    import os

    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")
    from weasyprint import HTML

    return HTML(string=report_html(title, blocks, **meta)).write_pdf()


def export_docx(title: str, blocks: list[dict[str, Any]], *, workspace_name: str = "", generated_at: str = "") -> bytes:
    from docx import Document as Docx
    from docx.shared import Inches, Pt

    doc = Docx()
    doc.add_heading(title, level=0)
    doc.add_paragraph(f"{workspace_name} · {generated_at}").runs[0].font.size = Pt(9)
    for b in blocks:
        t = b.get("type")
        if t == "heading":
            if b.get("level", 2) != 1:
                doc.add_heading(b.get("text", ""), level=1)
        elif t == "markdown":
            _docx_markdown(doc, b.get("text", ""))
        elif t == "chart":
            png = chart_png((b.get("chart") or {}).get("vega_lite") or {})
            if png:
                doc.add_picture(io.BytesIO(png), width=Inches(6.0))
                if b.get("title"):
                    cap = doc.add_paragraph(b["title"])
                    cap.runs[0].italic = True
        elif t == "kpi":
            k = b.get("kpi") or {}
            p = doc.add_paragraph()
            p.add_run(f"{k.get('value')}").bold = True
            p.add_run(f"  {b.get('title') or k.get('label', '')}")
        elif t == "table":
            cols = [c["name"] if isinstance(c, dict) else str(c) for c in b.get("columns", [])]
            rows = b.get("rows", [])[:200]
            if b.get("title"):
                doc.add_heading(b["title"], level=3)
            table = doc.add_table(rows=1, cols=len(cols))
            table.style = "Light Grid Accent 1"
            for i, c in enumerate(cols):
                table.rows[0].cells[i].text = c
            for r in rows:
                cells = table.add_row().cells
                for i, v in enumerate(r[:len(cols)]):
                    cells[i].text = "" if v is None else str(v)
        elif t == "sources" and b.get("items"):
            doc.add_heading("Nguồn tham chiếu", level=1)
            for i, it in enumerate(b["items"], 1):
                doc.add_paragraph(f"[{i}] {it.get('label', '')}" + (f" (hiệu lực {it['effective_date']})" if it.get("effective_date") else ""))
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _docx_markdown(doc: Any, text: str) -> None:
    table_buf: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table_buf
        if not table_buf:
            return
        cols = len(table_buf[0])
        t = doc.add_table(rows=0, cols=cols)
        t.style = "Light Grid Accent 1"
        for r in table_buf:
            cells = t.add_row().cells
            for i, v in enumerate(r[:cols]):
                cells[i].text = re.sub(r"\*\*|`", "", v)
        table_buf = []

    for line in text.split("\n"):
        s = line.rstrip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            table_buf.append(cells)
            continue
        flush_table()
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        if m:
            doc.add_heading(re.sub(r"\*\*", "", m.group(2)), level=min(3, len(m.group(1)) + 1))
        elif s.startswith(("- ", "* ", "• ")):
            _runs(doc.add_paragraph(style="List Bullet"), s[2:])
        elif s.strip() in ("---", "***", ""):
            continue
        else:
            _runs(doc.add_paragraph(), s)
    flush_table()


def _runs(p: Any, s: str) -> None:
    for part in re.split(r"(\*\*.+?\*\*)", s):
        if part.startswith("**") and part.endswith("**"):
            p.add_run(part[2:-2]).bold = True
        elif part:
            p.add_run(part)


def export_xlsx(title: str, blocks: list[dict[str, Any]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Tổng quan"
    ws0.append([title])
    n = 0
    for b in blocks:
        if b.get("type") == "table" and b.get("rows"):
            n += 1
            name = re.sub(r"[\[\]\*\?/\\:]", " ", (b.get("title") or f"Bảng {n}"))[:28] or f"Bang {n}"
            ws = wb.create_sheet(f"{n}. {name}"[:31])
            cols = [c["name"] if isinstance(c, dict) else str(c) for c in b.get("columns", [])]
            ws.append(cols)
            for r in b["rows"]:
                ws.append([v if isinstance(v, (int, float, str)) or v is None else str(v) for v in r])
            if b.get("sql"):
                ws0.append([f"{n}. {b.get('title') or ''}", b["sql"]])
        elif b.get("type") == "chart" and (b.get("chart") or {}).get("vega_lite"):
            n += 1
            data = b["chart"]["vega_lite"].get("data", {}).get("values") or []
            if data:
                ws = wb.create_sheet(f"{n}. {re.sub(r'[\\[\\]\\*\\?/\\\\:]', ' ', b.get('title') or 'Chart')[:24]}"[:31])
                keys = list(data[0].keys())
                ws.append(keys)
                for row in data:
                    ws.append([row.get(k) for k in keys])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
