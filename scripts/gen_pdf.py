"""Sinh 5 file .pdf cho test corpus ingest nganh retail Viet Nam:
- 2 catalogue san pham co bang (text-PDF that, co text layer)
- 2 hop dong nha cung cap dang text that (reportlab, co text layer, chon duoc chu)
- 1 file mo phong ban scan (render text thanh anh roi nhung vao PDF, KHONG co text layer -> test OCR)
"""
import os
import random
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "pdf")
BRAND = "GreenMart Việt Nam"
random.seed(7)

# Helvetica (reportlab mac dinh) khong co dau tieng Viet -> dang ky font Arial that de
# render dung Unicode cho cac PDF dang text.
_FONT_DIR = "/System/Library/Fonts/Supplemental"
pdfmetrics.registerFont(TTFont("Arial-VN", os.path.join(_FONT_DIR, "Arial.ttf")))
pdfmetrics.registerFont(TTFont("Arial-VN-Bold", os.path.join(_FONT_DIR, "Arial Bold.ttf")))
pdfmetrics.registerFont(TTFont("Arial-VN-Italic", os.path.join(_FONT_DIR, "Arial Italic.ttf")))
pdfmetrics.registerFontFamily("Arial-VN", normal="Arial-VN", bold="Arial-VN-Bold", italic="Arial-VN-Italic")

styles = getSampleStyleSheet()
styles.add(ParagraphStyle("NormalVN", parent=styles["Normal"], fontName="Arial-VN", leading=14))
styles.add(ParagraphStyle("ArticleHeadVN", parent=styles["Normal"], fontName="Arial-VN-Bold", fontSize=11, spaceBefore=10, spaceAfter=4))


# =====================================================================================
# 1-2) CATALOGUE SAN PHAM CO BANG (2 file, co text layer)
# =====================================================================================
def catalogue_pdf(filename, title, subtitle, table_header, rows):
    doc = SimpleDocTemplate(os.path.join(OUT_DIR, filename), pagesize=A4,
                             topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=1.8 * cm, rightMargin=1.8 * cm)
    story = []
    title_style = ParagraphStyle("TitleVN", parent=styles["Title"], fontName="Arial-VN-Bold", textColor=colors.HexColor("#1F6F43"))
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(subtitle, styles["NormalVN"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(
        f"{BRAND} — Catalogue tham khảo dành cho nhân viên tư vấn tại quầy. Giá có thể thay đổi theo chương trình "
        "khuyến mãi từng thời điểm, vui lòng đối chiếu bảng giá SKU cập nhật trên hệ thống POS.",
        styles["NormalVN"]))
    story.append(Spacer(1, 0.8 * cm))

    data = [table_header] + rows
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F6F43")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Arial-VN-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Arial-VN"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F7F4")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    doc.build(story)


def catalogue_home_appliances():
    header = ["Mã SP", "Tên sản phẩm", "Thông số chính", "Bảo hành", "Giá tham khảo"]
    rows = [
        ["HGD-001", "Nồi chiên không dầu 6L", "Công suất 1700W, hẹn giờ 60p", "12 tháng", "1.590.000đ"],
        ["HGD-002", "Máy xay sinh tố đa năng", "Cối 1.5L, 2 tốc độ + xay nhuyễn", "12 tháng", "890.000đ"],
        ["HGD-003", "Bếp từ đôi cảm ứng", "3000W, 8 mức nhiệt", "18 tháng", "2.490.000đ"],
        ["HGD-004", "Máy hút bụi cầm tay không dây", "Pin 2500mAh, lực hút 12kPa", "12 tháng", "1.290.000đ"],
        ["HGD-005", "Ấm siêu tốc inox 1.8L", "Tự ngắt khi sôi, chống cạn nước", "12 tháng", "349.000đ"],
        ["HGD-006", "Máy lọc không khí phòng khách", "Lọc HEPA H13, diện tích 40m2", "24 tháng", "3.990.000đ"],
        ["HGD-007", "Nồi áp suất điện đa năng 5L", "10 chế độ nấu tự động", "12 tháng", "1.190.000đ"],
    ]
    catalogue_pdf("01_catalogue_do_gia_dung.pdf", "CATALOGUE SẢN PHẨM — ĐỒ GIA DỤNG",
                  "Cập nhật Quý 3/2026 — Ngành hàng Gia dụng nhà bếp", header, rows)


def catalogue_electronics():
    header = ["Mã SP", "Tên sản phẩm", "Thông số chính", "Bảo hành", "Giá tham khảo"]
    rows = [
        ["DT-001", "Tai nghe bluetooth chống ồn", "Pin 30h, chống ồn chủ động ANC", "12 tháng", "1.490.000đ"],
        ["DT-002", "Loa bluetooth di động", "Công suất 20W, chống nước IPX7", "12 tháng", "990.000đ"],
        ["DT-003", "Sạc dự phòng 20000mAh", "Sạc nhanh 22.5W, 2 cổng ra", "12 tháng", "590.000đ"],
        ["DT-004", "Đồng hồ thông minh thể thao", "Đo nhịp tim, chống nước 5ATM", "12 tháng", "2.190.000đ"],
        ["DT-005", "Webcam họp trực tuyến Full HD", "1080p, mic khử tiếng ồn", "12 tháng", "690.000đ"],
        ["DT-006", "Ổ cứng di động 1TB", "USB 3.2, tốc độ đọc 130MB/s", "24 tháng", "1.290.000đ"],
    ]
    catalogue_pdf("02_catalogue_dien_tu_tieu_dung.pdf", "CATALOGUE SẢN PHẨM — ĐIỆN TỬ TIÊU DÙNG",
                  "Cập nhật Quý 3/2026 — Ngành hàng Điện tử phụ kiện", header, rows)


# =====================================================================================
# 3-4) HOP DONG NHA CUNG CAP - DANG TEXT THAT (co text layer, chon duoc chu)
# =====================================================================================
def contract_pdf(filename, supplier_name, contract_no, category, articles):
    doc = SimpleDocTemplate(os.path.join(OUT_DIR, filename), pagesize=A4,
                             topMargin=2.2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm)
    story = []
    title_style = ParagraphStyle("ContractTitleVN", parent=styles["Title"], fontName="Arial-VN-Bold", fontSize=15,
                                  textColor=colors.HexColor("#1F6F43"))
    center = ParagraphStyle("CenterVN", parent=styles["NormalVN"], alignment=1)

    story.append(Paragraph("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", center))
    story.append(Paragraph("Độc lập - Tự do - Hạnh phúc", ParagraphStyle("ItalicCenter", parent=center, fontName="Arial-VN-Italic")))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("HỢP ĐỒNG NGUYÊN TẮC CUNG CẤP HÀNG HÓA", title_style))
    story.append(Paragraph(f"Số: {contract_no}", center))
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph(
        f"<b>Bên A (Bên mua):</b> {BRAND} — Địa chỉ: 268 Trần Duy Hưng, Cầu Giấy, Hà Nội — "
        "Đại diện: Bà Nguyễn Thị Thu Hương, chức vụ: Giám đốc Thu mua.", styles["NormalVN"]))
    story.append(Paragraph(
        f"<b>Bên B (Bên bán):</b> {supplier_name} — Ngành hàng cung cấp: {category}.", styles["NormalVN"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "Sau khi thỏa thuận, hai bên thống nhất ký kết hợp đồng nguyên tắc cung cấp hàng hóa với các điều "
        "khoản như sau:", styles["NormalVN"]))

    for i, (art_title, body) in enumerate(articles, start=1):
        story.append(Paragraph(f"Điều {i}. {art_title}", styles["ArticleHeadVN"]))
        story.append(Paragraph(body, styles["NormalVN"]))

    story.append(Spacer(1, 1 * cm))
    sig_table = Table([["ĐẠI DIỆN BÊN A", "ĐẠI DIỆN BÊN B"], ["(Đã ký, đóng dấu)", "(Đã ký, đóng dấu)"]],
                       colWidths=[8 * cm, 8 * cm])
    sig_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Arial-VN-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Arial-VN-Italic"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, 1), 60),
    ]))
    story.append(sig_table)
    doc.build(story)


def contract_electronics():
    articles = [
        ("Đối tượng hợp đồng",
         "Bên B cung cấp cho Bên A các mặt hàng điện máy - gia dụng theo đơn đặt hàng phát sinh từng đợt, "
         "chi tiết SKU và đơn giá theo Phụ lục bảng giá đính kèm hợp đồng này."),
        ("Thời hạn hợp đồng",
         "Hợp đồng có thời hạn 24 tháng kể từ ngày ký, tự động gia hạn thêm 12 tháng nếu không bên nào có ý "
         "kiến chấm dứt bằng văn bản trước 30 ngày kể từ ngày hết hạn."),
        ("Phương thức giao nhận",
         "Bên B giao hàng tận kho tổng của Bên A trong vòng 5-7 ngày làm việc kể từ khi nhận đơn đặt hàng "
         "chính thức qua email hoặc hệ thống đặt hàng điện tử."),
        ("Thanh toán",
         "Bên A thanh toán cho Bên B theo hình thức công nợ 30 ngày kể từ ngày nhận hàng và xuất hóa đơn giá "
         "trị gia tăng hợp lệ, chuyển khoản qua tài khoản ngân hàng đã đăng ký."),
        ("Chính sách đổi trả hàng lỗi nhà sản xuất",
         "Bên B nhận đổi trả sản phẩm lỗi nhà sản xuất trong vòng 15 ngày kể từ ngày Bên A phát hiện lỗi, chi "
         "phí vận chuyển đổi trả do Bên B chi trả toàn bộ."),
        ("Điều khoản chung",
         "Hai bên cam kết thực hiện đúng các điều khoản đã thỏa thuận trong hợp đồng này. Mọi tranh chấp phát "
         "sinh được giải quyết trên tinh thần thương lượng, hòa giải; trường hợp không đạt được thỏa thuận sẽ "
         "đưa ra Tòa án có thẩm quyền giải quyết theo quy định pháp luật."),
    ]
    contract_pdf("03_hop_dong_nha_cung_cap_dien_may.pdf", "Công ty TNHH Thương mại Kỹ nghệ Phương Nam",
                 "HĐNT-2026/DM-018", "Điện máy - Gia dụng", articles)


def contract_food():
    articles = [
        ("Đối tượng hợp đồng",
         "Bên B cung cấp cho Bên A các mặt hàng thực phẩm khô đóng gói theo danh mục SKU đính kèm Phụ lục 01, "
         "số lượng đặt hàng theo nhu cầu thực tế của từng chi nhánh."),
        ("Thời hạn hợp đồng",
         "Hợp đồng có thời hạn 12 tháng kể từ ngày ký, có thể gia hạn theo thỏa thuận bằng văn bản của hai bên "
         "trước khi hợp đồng hết hạn."),
        ("Tiêu chuẩn chất lượng",
         "Hàng hóa phải có giấy chứng nhận an toàn thực phẩm còn hiệu lực, hạn sử dụng còn tối thiểu hai phần "
         "ba thời hạn tại thời điểm giao hàng cho Bên A."),
        ("Phương thức giao nhận",
         "Bên B giao hàng trực tiếp đến từng cửa hàng theo lịch phân bổ hàng tuần do Bên A cung cấp, có phiếu "
         "giao nhận xác nhận số lượng thực tế tại từng điểm giao."),
        ("Thanh toán",
         "Bên A thanh toán công nợ trong vòng 15 ngày kể từ ngày nhận hàng, được chiết khấu thêm 2% nếu thanh "
         "toán trong vòng 5 ngày kể từ ngày nhận hàng."),
        ("Xử lý hàng cận hạn sử dụng",
         "Bên B hỗ trợ đổi trả hàng cận hạn sử dụng dưới 30 ngày theo chính sách thu hồi hàng chậm luân chuyển "
         "đã thống nhất giữa hai bên."),
    ]
    contract_pdf("04_hop_dong_nha_cung_cap_thuc_pham.pdf", "Công ty Cổ phần Thực phẩm Sạch Miền Tây",
                 "HĐNT-2026/TP-042", "Thực phẩm khô đóng gói", articles)


# =====================================================================================
# 5) MO PHONG BAN SCAN - KHONG CO TEXT LAYER (render text thanh anh, nhung vao PDF)
# =====================================================================================
def _add_scan_noise(img):
    arr = np.array(img).astype(np.int16)
    noise = np.random.randint(-12, 12, arr.shape[:2])
    for c in range(3):
        arr[:, :, c] = np.clip(arr[:, :, c] + noise, 0, 255)
    img = Image.fromarray(arr.astype(np.uint8))
    img = img.rotate(random.uniform(-1.2, 1.2), expand=False, fillcolor=(250, 248, 242))
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    return img


def _font(size, bold=False):
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    path = f"/System/Library/Fonts/Supplemental/{name}"
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def scan_delivery_handover():
    """Mo phong ban scan cua mot bien ban ban giao hang hoa danh may/viet tay -> khong co text layer."""
    filename = "05_scan_bien_ban_ban_giao_hang_hoa.pdf"
    W, H = 1654, 2339  # ~A4 tai 200dpi
    img = Image.new("RGB", (W, H), (250, 248, 242))
    draw = ImageDraw.Draw(img)
    f_title = _font(32, bold=True)
    f_body = _font(23)
    f_small = _font(19)

    y = 90
    draw.text((W // 2, y), BRAND.upper(), font=f_title, fill=(20, 20, 20), anchor="ma")
    y += 55
    draw.text((W // 2, y), "BIÊN BẢN BÀN GIAO HÀNG HÓA", font=f_title, fill=(20, 20, 20), anchor="ma")
    y += 50
    draw.text((W // 2, y), "Số: BBGH-2026/0817 — Ngày 17/08/2026", font=f_body, fill=(40, 40, 40), anchor="ma")
    y += 65
    draw.line([(150, y), (W - 150, y)], fill=(60, 60, 60), width=2)
    y += 45

    lines = [
        "Hôm nay, ngày 17 tháng 08 năm 2026, tại kho tổng chi nhánh GM-HN01, chúng tôi gồm có:",
        "Bên giao: Ông Trần Văn Bình — Nhân viên giao nhận, Công ty TNHH Thương mại Kỹ nghệ Phương Nam.",
        "Bên nhận: Bà Lê Thị Hồng — Thủ kho, hệ thống GreenMart Việt Nam.",
        "",
        "Hai bên cùng tiến hành kiểm đếm và bàn giao lô hàng theo đơn đặt hàng số PO-2026-0742, kết quả cụ",
        "thể như sau:",
        "  1. Tivi LED 55 inch - TechVision: số lượng 20 thùng, tình trạng nguyên vẹn, đủ tem niêm phong.",
        "  2. Tủ lạnh Inverter 300L - CoolMax: số lượng 15 thùng, tình trạng nguyên vẹn.",
        "  3. Máy giặt cửa trước 9kg - WashPro: số lượng 10 thùng, phát hiện 01 thùng bị móp góc, đã ghi",
        "     nhận riêng để đổi trả theo chính sách hợp đồng.",
        "",
        "Bên nhận xác nhận đã nhận đủ số lượng nêu trên, trừ 01 thùng máy giặt bị móp góc sẽ được Bên giao",
        "đổi mới trong vòng 7 ngày làm việc theo đúng điều khoản hợp đồng đã ký giữa hai bên.",
        "",
        "Biên bản được lập thành 02 bản, mỗi bên giữ 01 bản có giá trị pháp lý như nhau.",
    ]
    for line in lines:
        draw.text((150, y), line, font=f_small, fill=(30, 30, 30))
        y += 34

    y += 50
    draw.text((280, y), "BÊN GIAO", font=f_body, fill=(20, 20, 20), anchor="ma")
    draw.text((W - 280, y), "BÊN NHẬN", font=f_body, fill=(20, 20, 20), anchor="ma")
    y += 130
    draw.text((280, y), "Trần Văn Bình", font=f_small, fill=(90, 90, 90), anchor="ma")
    draw.text((W - 280, y), "Lê Thị Hồng", font=f_small, fill=(90, 90, 90), anchor="ma")

    img = _add_scan_noise(img)
    img.save(os.path.join(OUT_DIR, filename), "PDF", resolution=200.0)

    reference_text = f"{BRAND.upper()}\nBIÊN BẢN BÀN GIAO HÀNG HÓA\nSố: BBGH-2026/0817 — Ngày 17/08/2026\n\n" + "\n".join(
        l for l in lines if l.strip()) + "\n\nBÊN GIAO: Trần Văn Bình\nBÊN NHẬN: Lê Thị Hồng"
    return {"filename": filename, "reference_text": reference_text}


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    catalogue_home_appliances()
    catalogue_electronics()
    contract_electronics()
    contract_food()
    meta = scan_delivery_handover()
    print("Done: 5 pdf files ->", os.path.abspath(OUT_DIR))
    print("  scan reference_text chars:", len(meta["reference_text"]))
