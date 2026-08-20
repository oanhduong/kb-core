"""Sinh các file fixture cố định, kích thước nhỏ cho tests/test_parsers.py.

Chạy MỘT LẦN bằng tay (python tests/fixtures/generate_fixtures.py), rồi commit
các file output vào repo. Khác với scripts/gen_*.py (sinh data/raw ngẫu nhiên mỗi
lần chạy), các fixture ở đây phải cố định để test có kết quả tái lập được.
"""
import os
import subprocess

from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Font
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

OUT_DIR = os.path.dirname(__file__)
FONT_DIR = "/System/Library/Fonts/Supplemental"


def _font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


# =====================================================================================
# 1) tiny.docx — 2 heading lồng nhau (level 1 > level 2) + 1 bảng 3x3
# =====================================================================================
def gen_tiny_docx():
    doc = Document()
    doc.add_heading("CHƯƠNG I. QUY ĐỊNH CHUNG", level=1)
    doc.add_heading("Điều 1. Mục đích", level=2)
    doc.add_paragraph("Quy định áp dụng thử nghiệm cho pipeline ingest.")
    table = doc.add_table(rows=3, cols=3)
    rows = [
        ["Cột A", "Cột B", "Cột C"],
        ["1", "Hàng hai cột B", "x"],
        ["2", "Hàng ba cột B", "y"],
    ]
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            table.cell(r, c).text = val
    doc.save(os.path.join(OUT_DIR, "tiny.docx"))


# =====================================================================================
# 2) tiny.xlsx — 2 sheet. Sheet2 có merged cell header (A1:B1) + merged cell dọc
#    trong vùng dữ liệu (A3:A4) để test fill-down xuống ô con.
# =====================================================================================
def gen_tiny_xlsx():
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Sheet1"
    ws1.append(["sku", "ten"])
    for c in range(1, 3):
        ws1.cell(row=1, column=c).font = Font(bold=True)
    ws1.append(["A1", "Táo"])
    ws1.append(["A2", "Cam"])

    ws2 = wb.create_sheet("Sheet2")
    ws2.merge_cells("A1:B1")
    ws2["A1"] = "NHÓM A"
    ws2["A1"].font = Font(bold=True)
    ws2["A2"] = "Cột 1"
    ws2["B2"] = "Cột 2"
    ws2["A2"].font = Font(bold=True)
    ws2["B2"].font = Font(bold=True)
    ws2["A3"] = "Danh mục X"
    ws2["B3"] = "Giá trị 1"
    ws2["B4"] = "Giá trị 2"
    ws2.merge_cells("A3:A4")
    ws2["A5"] = "Ghi chú áp dụng cho cả nhóm"
    ws2.merge_cells("A5:B5")

    wb.save(os.path.join(OUT_DIR, "tiny.xlsx"))


# =====================================================================================
# 3) tiny_text.pdf — PDF thật, có text layer (reportlab)
# =====================================================================================
def gen_tiny_text_pdf():
    pdfmetrics.registerFont(TTFont("Arial-VN", os.path.join(FONT_DIR, "Arial.ttf")))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("NormalVN", parent=styles["Normal"], fontName="Arial-VN"))
    doc = SimpleDocTemplate(os.path.join(OUT_DIR, "tiny_text.pdf"), pagesize=A4)
    doc.build([Paragraph(
        "Đây là nội dung thử nghiệm cho file PDF có lớp văn bản thật, dùng để kiểm tra parse_pdf.",
        styles["NormalVN"],
    )])


# =====================================================================================
# 4) tiny_scan.pdf — render chữ thành ảnh rồi lưu PDF -> KHÔNG có text layer
# =====================================================================================
def gen_tiny_scan_pdf():
    w, h = 600, 300
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((30, 120), "Biên bản bàn giao (giả lập bản scan)", font=_font("Arial.ttf", 24), fill=(0, 0, 0))
    img.save(os.path.join(OUT_DIR, "tiny_scan.pdf"), "PDF", resolution=150.0)


# =====================================================================================
# 5) tiny.png — có chữ tiếng Việt có dấu
# =====================================================================================
def gen_tiny_png():
    w, h = 300, 120
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((10, 40), "Xin chào Việt Nam", font=_font("Arial Bold.ttf", 28), fill=(20, 20, 20))
    img.save(os.path.join(OUT_DIR, "tiny.png"), "PNG")


# =====================================================================================
# 6) tiny.wav — ~5 giây, giọng đọc tiếng Việt thật qua gTTS rồi convert 16kHz mono
# =====================================================================================
def gen_tiny_wav():
    from gtts import gTTS

    mp3_path = os.path.join(OUT_DIR, "_tmp_tiny.mp3")
    gTTS(text="Xin chào quý khách, cảm ơn quý khách đã ghé thăm cửa hàng Green Mart hôm nay.", lang="vi").save(mp3_path)
    wav_path = os.path.join(OUT_DIR, "tiny.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_path, "-ac", "1", "-ar", "16000", wav_path],
        check=True, capture_output=True,
    )
    os.remove(mp3_path)


# =====================================================================================
# 7) corrupted.docx — byte rác, không phải file .docx thật
# =====================================================================================
def gen_corrupted_docx():
    with open(os.path.join(OUT_DIR, "corrupted.docx"), "wb") as f:
        f.write(b"day khong phai file docx that, chi la byte rac de test loi.")


# =====================================================================================
# 8) empty.xlsx — workbook hợp lệ nhưng không có dữ liệu
# =====================================================================================
def gen_empty_xlsx():
    wb = Workbook()
    wb.save(os.path.join(OUT_DIR, "empty.xlsx"))


if __name__ == "__main__":
    gen_tiny_docx()
    gen_tiny_xlsx()
    gen_tiny_text_pdf()
    gen_tiny_scan_pdf()
    gen_tiny_png()
    gen_tiny_wav()
    gen_corrupted_docx()
    gen_empty_xlsx()
    print("Done: fixtures ->", OUT_DIR)
