"""Unit test xuất báo cáo: Markdown -> HTML, DOCX, XLSX, PNG chart (vl-convert). Không cần DB/LLM."""
import io
import zipfile

from reports.export import _md_to_html, chart_png, export_docx, export_xlsx, report_html

CHART = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "data": {"values": [{"x": "A", "y": 3}, {"x": "B", "y": 5}]},
         "mark": "bar", "encoding": {"x": {"field": "x", "type": "nominal"}, "y": {"field": "y", "type": "quantitative"}}, "width": "container", "height": 200}
BLOCKS = [
    {"id": "1", "type": "heading", "text": "Báo cáo", "level": 1},
    {"id": "2", "type": "heading", "text": "Tổng quan", "level": 2},
    {"id": "3", "type": "markdown", "text": "## Kết quả\nDoanh thu **tăng 12%** [1].\n\n| Ngành | SKU |\n|---|---|\n| Điện máy | 38 |\n\n- ý 1 [2]\n- ý 2",
     "citations": [{"n": 1, "label": "bảng giá, Sheet A dòng 2"}]},
    {"id": "4", "type": "chart", "title": "SKU theo ngành", "chart": {"type": "bar", "vega_lite": CHART}, "sql": "SELECT 1"},
    {"id": "5", "type": "kpi", "title": "Tổng SKU", "kpi": {"label": "tong", "value": 300}},
    {"id": "6", "type": "table", "title": "Chi tiết", "columns": [{"name": "sku", "type": "varchar"}, {"name": "gia", "type": "bigint"}], "rows": [["A1", 1000], ["A2", None]]},
    {"id": "7", "type": "sources", "items": [{"label": "bảng giá, Sheet A dòng 2", "effective_date": "2026-08-01"}]},
]


def test_markdown_to_html_handles_tables_lists_headings_and_citations():
    html = _md_to_html(BLOCKS[2]["text"])
    assert "<h3>Kết quả</h3>" in html and "<b>tăng 12%</b>" in html and "<sup class='cite'>[1]</sup>" in html
    assert html.count("<tr>") == 2 and "<li>ý 1" in html


def test_report_html_renders_all_block_types():
    html = report_html("Báo cáo", BLOCKS, workspace_name="WS", generated_at="hôm nay")
    assert "data:image/png;base64" in html and "class='kpi'" in html and "Nguồn tham chiếu" in html and "hiệu lực 2026-08-01" in html
    assert "<th>sku</th>" in html


def test_chart_png_is_real_png():
    png = chart_png(CHART)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_export_docx_and_xlsx_are_valid_office_files():
    docx = export_docx("Báo cáo", BLOCKS, workspace_name="WS", generated_at="x")
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        assert "Tổng quan" in xml and "tăng 12%" in xml and "Nguồn tham chiếu" in xml
        assert any(n.startswith("word/media/") for n in z.namelist())  # ảnh chart
    xlsx = export_xlsx("Báo cáo", BLOCKS)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx))
    assert len(wb.sheetnames) == 3  # Tổng quan + chart data + bảng
    ws = wb[wb.sheetnames[-1]]
    assert ws["A1"].value == "sku" and ws["B2"].value == 1000
