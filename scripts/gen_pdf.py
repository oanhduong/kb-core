"""Sinh 5 file .pdf: 3 catalogue san pham co bang (text-PDF that), 2 hop dong nha cung cap dang 'ban scan'
(render thanh anh co nhieu/nghieng roi luu PDF, de test OCR trong pipeline ingestion)."""
import os
import random
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "pdf")
BRAND = "GreenMart Việt Nam"
styles = getSampleStyleSheet()
random.seed(7)


def catalogue_pdf(filename, title, subtitle, table_header, rows):
    doc = SimpleDocTemplate(os.path.join(OUT_DIR, filename), pagesize=A4,
                             topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=1.8 * cm, rightMargin=1.8 * cm)
    story = []
    title_style = ParagraphStyle("TitleVN", parent=styles["Title"], textColor=colors.HexColor("#1F6F43"))
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(subtitle, styles["Normal"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(
        f"{BRAND} — Catalogue tham khảo dành cho nhân viên tư vấn tại quầy. Giá có thể thay đổi theo chương trình "
        "khuyến mãi từng thời điểm, vui lòng đối chiếu bảng giá SKU cập nhật trên hệ thống POS.",
        styles["Normal"]))
    story.append(Spacer(1, 0.8 * cm))

    data = [table_header] + rows
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F6F43")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
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


def catalogue_food():
    header = ["Mã SP", "Tên sản phẩm", "Quy cách", "Hạn sử dụng", "Giá tham khảo"]
    rows = [
        ["TP-001", "Snack khoai tây vị phô mai", "Gói 150g", "6 tháng", "25.000đ"],
        ["TP-002", "Nước ép trái cây hộp giấy", "Lốc 6 hộp x 200ml", "9 tháng", "45.000đ"],
        ["TP-003", "Xúc xích tiệt trùng", "Gói 500g", "6 tháng", "59.000đ"],
        ["TP-004", "Mật ong nguyên chất", "Chai 500ml", "24 tháng", "129.000đ"],
        ["TP-005", "Trà túi lọc hoa cúc", "Hộp 25 túi", "18 tháng", "39.000đ"],
        ["TP-006", "Sốt me chua ngọt", "Chai 300ml", "12 tháng", "32.000đ"],
        ["TP-007", "Ngũ cốc ăn sáng dinh dưỡng", "Hộp 400g", "12 tháng", "89.000đ"],
    ]
    catalogue_pdf("03_catalogue_thuc_pham_dong_goi.pdf", "CATALOGUE SẢN PHẨM — THỰC PHẨM ĐÓNG GÓI",
                  "Cập nhật Quý 3/2026 — Ngành hàng Thực phẩm khô", header, rows)


def _add_scan_noise(img):
    arr = np.array(img).astype(np.int16)
    noise = np.random.randint(-12, 12, arr.shape[:2])
    for c in range(3):
        arr[:, :, c] = np.clip(arr[:, :, c] + noise, 0, 255)
    img = Image.fromarray(arr.astype(np.uint8))
    img = img.rotate(random.uniform(-1.2, 1.2), expand=False, fillcolor=(250, 248, 242))
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    return img


def _font(size):
    for path in ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def supplier_contract_scan(filename, supplier_name, contract_no, lines):
    W, H = 1654, 2339  # ~A4 at 200dpi
    img = Image.new("RGB", (W, H), (250, 248, 242))
    draw = ImageDraw.Draw(img)
    f_title = _font(34)
    f_body = _font(24)
    f_small = _font(20)

    y = 100
    draw.text((W // 2, y), f"{BRAND.upper()}", font=f_title, fill=(20, 20, 20), anchor="ma")
    y += 60
    draw.text((W // 2, y), "HỢP ĐỒNG NGUYÊN TẮC CUNG CẤP HÀNG HÓA", font=f_title, fill=(20, 20, 20), anchor="ma")
    y += 55
    draw.text((W // 2, y), f"Số: {contract_no}", font=f_body, fill=(40, 40, 40), anchor="ma")
    y += 70
    draw.line([(150, y), (W - 150, y)], fill=(60, 60, 60), width=2)
    y += 50

    draw.text((150, y), f"Bên A (Bên mua): {BRAND} — Địa chỉ: 268 Đường Trần Duy Hưng, Cầu Giấy, Hà Nội", font=f_body, fill=(20, 20, 20))
    y += 45
    draw.text((150, y), f"Bên B (Bên bán): {supplier_name}", font=f_body, fill=(20, 20, 20))
    y += 70

    for line in lines:
        for chunk in [line[i:i + 78] for i in range(0, len(line), 78)]:
            draw.text((150, y), chunk, font=f_small, fill=(30, 30, 30))
            y += 36
        y += 14

    y += 40
    draw.text((150, y), "Đại diện Bên A", font=f_body, fill=(20, 20, 20))
    draw.text((W - 450, y), "Đại diện Bên B", font=f_body, fill=(20, 20, 20))
    y += 140
    draw.text((150, y), "(Đã ký, đóng dấu)", font=f_small, fill=(90, 90, 90))
    draw.text((W - 450, y), "(Đã ký, đóng dấu)", font=f_small, fill=(90, 90, 90))

    img = _add_scan_noise(img)
    img.save(os.path.join(OUT_DIR, filename), "PDF", resolution=200.0)


def contract_a():
    lines = [
        "Điều 1. Đối tượng hợp đồng: Bên B cung cấp cho Bên A các mặt hàng điện máy - gia dụng theo",
        "đơn đặt hàng phát sinh từng đợt, chi tiết SKU và đơn giá theo Phụ lục bảng giá đính kèm.",
        "Điều 2. Thời hạn hợp đồng: 24 tháng kể từ ngày ký, tự động gia hạn nếu không bên nào có ý",
        "kiến chấm dứt bằng văn bản trước 30 ngày.",
        "Điều 3. Phương thức giao nhận: Giao hàng tận kho tổng của Bên A trong vòng 5-7 ngày làm việc",
        "kể từ khi nhận đơn đặt hàng chính thức.",
        "Điều 4. Thanh toán: Công nợ 30 ngày kể từ ngày nhận hàng và xuất hóa đơn GTGT hợp lệ.",
        "Điều 5. Chính sách đổi trả hàng lỗi nhà sản xuất: Bên B nhận đổi trong vòng 15 ngày kể từ",
        "ngày Bên A phát hiện lỗi, chi phí vận chuyển đổi trả do Bên B chi trả.",
        "Điều 6. Điều khoản chung: Hai bên cam kết thực hiện đúng các điều khoản đã thỏa thuận,",
        "mọi tranh chấp phát sinh được giải quyết trên tinh thần thương lượng, hòa giải.",
    ]
    supplier_contract_scan("04_hop_dong_nha_cung_cap_dien_may.pdf", "Công ty TNHH Thương mại Kỹ nghệ Phương Nam",
                            "HĐNT-2026/DM-018", lines)


def contract_b():
    lines = [
        "Điều 1. Đối tượng hợp đồng: Bên B cung cấp cho Bên A các mặt hàng thực phẩm khô đóng gói",
        "theo danh mục SKU đính kèm Phụ lục 01, số lượng đặt hàng theo nhu cầu thực tế từng chi nhánh.",
        "Điều 2. Thời hạn hợp đồng: 12 tháng kể từ ngày ký, có thể gia hạn theo thỏa thuận hai bên.",
        "Điều 3. Tiêu chuẩn chất lượng: Hàng hóa phải có giấy chứng nhận an toàn thực phẩm còn hiệu",
        "lực, hạn sử dụng còn tối thiểu 2/3 thời hạn tại thời điểm giao hàng.",
        "Điều 4. Phương thức giao nhận: Giao hàng trực tiếp đến từng cửa hàng theo lịch phân bổ hàng",
        "tuần do Bên A cung cấp.",
        "Điều 5. Thanh toán: Công nợ 15 ngày kể từ ngày nhận hàng, chiết khấu 2% nếu thanh toán sớm",
        "trong vòng 5 ngày.",
        "Điều 6. Xử lý hàng cận hạn: Bên B hỗ trợ đổi trả hàng cận hạn sử dụng dưới 30 ngày theo",
        "chính sách thu hồi hàng chậm luân chuyển.",
    ]
    supplier_contract_scan("05_hop_dong_nha_cung_cap_thuc_pham.pdf", "Công ty Cổ phần Thực phẩm Sạch Miền Tây",
                            "HĐNT-2026/TP-042", lines)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    catalogue_home_appliances()
    catalogue_electronics()
    catalogue_food()
    contract_a()
    contract_b()
    print("Done: 5 pdf files ->", os.path.abspath(OUT_DIR))
