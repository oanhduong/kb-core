"""Sinh 5 file .png cho test corpus ingest nganh retail Viet Nam:
poster khuyen mai, bang gia viet tay mo phong, anh chup man hinh POS,
bien hieu quay, phieu bao hanh.

Tra ve dict {filename: reference_text} - noi dung chu that da "ve" len anh,
dung lam ground truth cho danh gia OCR (anh khong co text layer)."""
import os
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "image")
random.seed(11)
np.random.seed(11)

FONT_DIR = "/System/Library/Fonts/Supplemental"


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def photo_effect(img, rotate_deg=1.5, noise_amt=10, blur=0.5, out_path=None):
    """Mo phong anh chup dien thoai: nhieu, hoi nghieng, hoi mo. Luu PNG (khong nen loss nhu JPEG)."""
    arr = np.array(img.convert("RGB")).astype(np.int16)
    noise = np.random.randint(-noise_amt, noise_amt, arr.shape[:2])
    for c in range(3):
        arr[:, :, c] = np.clip(arr[:, :, c] + noise, 0, 255)
    img2 = Image.fromarray(arr.astype(np.uint8))
    img2 = img2.rotate(random.uniform(-rotate_deg, rotate_deg), expand=True, fillcolor=(30, 30, 30))
    img2 = img2.filter(ImageFilter.GaussianBlur(radius=blur))
    img2 = ImageEnhance.Brightness(img2).enhance(random.uniform(0.92, 1.05))
    img2 = ImageEnhance.Contrast(img2).enhance(random.uniform(0.95, 1.08))
    img2.save(out_path, "PNG")


def gradient_bg(w, h, c1, c2, vertical=True):
    base = Image.new("RGB", (w, h), c1)
    top = Image.new("RGB", (w, h), c2)
    mask = Image.new("L", (w, h))
    mdata = []
    for y in range(h):
        for x in range(w):
            v = y / h if vertical else x / w
            mdata.append(int(255 * v))
    mask.putdata(mdata)
    return Image.composite(top, base, mask)


# =====================================================================================
# 1) POSTER KHUYEN MAI (thiet ke digital sach, khong hieu ung anh chup)
# =====================================================================================
def poster_khuyen_mai():
    W, H = 1200, 1500
    img = gradient_bg(W, H, (255, 214, 89), (255, 122, 89))
    draw = ImageDraw.Draw(img)
    draw.text((W // 2, 140), "GREENMART", font=font("Arial Black.ttf", 90), fill=(255, 255, 255), anchor="ma", stroke_width=3, stroke_fill=(180, 40, 20))
    draw.text((W // 2, 250), "VIỆT NAM", font=font("Arial Bold.ttf", 60), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 420), "KHAI TRƯƠNG", font=font("Arial Bold.ttf", 100), fill=(200, 20, 20), anchor="ma", stroke_width=4, stroke_fill=(255, 255, 255))
    draw.text((W // 2, 540), "CHI NHÁNH THỦ ĐỨC", font=font("Arial Bold.ttf", 55), fill=(60, 20, 10), anchor="ma")
    draw.ellipse([W // 2 - 260, 650, W // 2 + 260, 1170], fill=(200, 20, 20), outline=(255, 255, 255), width=8)
    draw.text((W // 2, 810), "GIẢM ĐẾN", font=font("Arial Bold.ttf", 48), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 870), "30%", font=font("Arial Black.ttf", 150), fill=(255, 232, 79), anchor="ma")
    draw.text((W // 2, 1050), "TOÀN BỘ NGÀNH HÀNG", font=font("Arial Bold.ttf", 38), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 1260), "Từ 01/07 đến 07/07/2026", font=font("Arial.ttf", 42), fill=(60, 20, 10), anchor="ma")
    draw.text((W // 2, 1330), "268 Trần Duy Hưng, TP. Thủ Đức — Hotline: 1900 1234", font=font("Arial.ttf", 30), fill=(60, 20, 10), anchor="ma")
    img.save(os.path.join(OUT_DIR, "01_poster_khuyen_mai.png"), "PNG")
    return ("GREENMART VIỆT NAM. KHAI TRƯƠNG CHI NHÁNH THỦ ĐỨC. GIẢM ĐẾN 30% TOÀN BỘ NGÀNH HÀNG. "
            "Từ 01/07 đến 07/07/2026. 268 Trần Duy Hưng, TP. Thủ Đức — Hotline: 1900 1234")


# =====================================================================================
# 2) BANG GIA VIET TAY MO PHONG (co hieu ung anh chup)
# =====================================================================================
def bang_gia_viet_tay():
    W, H = 1100, 1400
    img = Image.new("RGB", (W, H), (238, 230, 205))
    draw = ImageDraw.Draw(img)
    for y in range(180, H - 60, 90):
        draw.line([(40, y), (W - 40, y)], fill=(200, 190, 160), width=2)
    draw.rectangle([0, 0, W, 120], fill=(60, 110, 60))
    title = "QUẦY RAU CỦ TƯƠI - GIÁ HÔM NAY"
    draw.text((W // 2, 60), title, font=font("Arial Bold.ttf", 48), fill=(255, 255, 255), anchor="mm")

    rows = [
        ("Rau muống bó", "8.000đ"), ("Cà chua (kg)", "22.000đ"), ("Khoai tây (kg)", "18.000đ"),
        ("Bí đỏ (kg)", "15.000đ"), ("Dưa leo (kg)", "16.000đ"), ("Cải ngọt bó", "9.000đ"),
        ("Hành lá bó", "6.000đ"), ("Ớt sừng (kg)", "35.000đ"),
    ]
    y = 200
    for name, price in rows:
        jx, jy = random.randint(-6, 6), random.randint(-8, 8)
        rot_img = Image.new("RGBA", (900, 90), (0, 0, 0, 0))
        rdraw = ImageDraw.Draw(rot_img)
        rdraw.text((10, 10), name, font=font("Arial Bold.ttf", 40), fill=(30, 30, 30, 255))
        rdraw.text((650, 10), price, font=font("Arial Black.ttf", 44), fill=(160, 20, 20, 255))
        rot_img = rot_img.rotate(random.uniform(-1.5, 1.5), expand=True)
        img.paste(rot_img, (80 + jx, y + jy), rot_img)
        y += 90

    note = "Giá có thể thay đổi trong ngày — vui lòng hỏi nhân viên quầy"
    draw.text((W // 2, H - 30), note, font=font("Arial.ttf", 24), fill=(90, 80, 60), anchor="mm")
    photo_effect(img, rotate_deg=2.5, noise_amt=14, blur=0.8, out_path=os.path.join(OUT_DIR, "02_bang_gia_viet_tay.png"))
    lines = [title] + [f"{n} {p}" for n, p in rows] + [note]
    return "\n".join(lines)


# =====================================================================================
# 3) ANH CHUP MAN HINH POS (screenshot UI - sac net, khong hieu ung anh chup)
# =====================================================================================
def anh_chup_man_hinh_pos():
    W, H = 1000, 1400
    img = Image.new("RGB", (W, H), (245, 246, 248))
    draw = ImageDraw.Draw(img)

    # Thanh trang thai tren cung
    draw.rectangle([0, 0, W, 60], fill=(31, 111, 67))
    draw.text((24, 30), "GreenMart POS — Quầy 03", font=font("Arial Bold.ttf", 22), fill=(255, 255, 255), anchor="lm")
    draw.text((W - 24, 30), "17/08/2026  14:32", font=font("Arial.ttf", 18), fill=(230, 230, 230), anchor="rm")

    draw.text((24, 90), "Mã hóa đơn: HD20260817-0342", font=font("Arial.ttf", 20), fill=(60, 60, 60), anchor="lm")
    draw.text((24, 120), "Thu ngân: Nguyễn Thị Hoa", font=font("Arial.ttf", 20), fill=(60, 60, 60), anchor="lm")

    # Bang danh sach san pham
    headers = ["Sản phẩm", "SL", "Đơn giá", "Thành tiền"]
    col_x = [24, 560, 660, 830]
    y = 170
    draw.rectangle([16, y, W - 16, y + 44], fill=(31, 111, 67))
    for h, x in zip(headers, col_x):
        draw.text((x, y + 22), h, font=font("Arial Bold.ttf", 19), fill=(255, 255, 255), anchor="lm")
    y += 44

    items = [
        ("Nồi chiên không dầu 6L", 1, "1.590.000đ", "1.590.000đ"),
        ("Gạo thơm đặc sản 5kg", 2, "129.000đ", "258.000đ"),
        ("Sữa tươi tiệt trùng lốc 4 hộp", 3, "35.000đ", "105.000đ"),
        ("Nước rửa chén 750ml", 1, "37.000đ", "37.000đ"),
        ("Tai nghe bluetooth chống ồn", 1, "779.000đ", "779.000đ"),
    ]
    for i, (name, qty, price, total) in enumerate(items):
        row_bg = (255, 255, 255) if i % 2 == 0 else (240, 244, 241)
        draw.rectangle([16, y, W - 16, y + 50], fill=row_bg)
        draw.text((col_x[0], y + 25), name, font=font("Arial.ttf", 18), fill=(30, 30, 30), anchor="lm")
        draw.text((col_x[1], y + 25), str(qty), font=font("Arial.ttf", 18), fill=(30, 30, 30), anchor="lm")
        draw.text((col_x[2], y + 25), price, font=font("Arial.ttf", 18), fill=(30, 30, 30), anchor="lm")
        draw.text((col_x[3], y + 25), total, font=font("Arial.ttf", 18), fill=(30, 30, 30), anchor="lm")
        y += 50

    y += 20
    draw.line([(16, y), (W - 16, y)], fill=(200, 200, 200), width=2)
    y += 20
    draw.text((col_x[2], y), "Tạm tính:", font=font("Arial.ttf", 20), fill=(60, 60, 60), anchor="lm")
    draw.text((W - 24, y), "2.769.000đ", font=font("Arial Bold.ttf", 20), fill=(30, 30, 30), anchor="rm")
    y += 36
    draw.text((col_x[2], y), "Giảm giá (thẻ TV):", font=font("Arial.ttf", 20), fill=(60, 60, 60), anchor="lm")
    draw.text((W - 24, y), "-83.070đ", font=font("Arial Bold.ttf", 20), fill=(200, 20, 20), anchor="rm")
    y += 44
    draw.rectangle([16, y, W - 16, y + 60], fill=(31, 111, 67))
    draw.text((col_x[2], y + 30), "TỔNG CỘNG:", font=font("Arial Bold.ttf", 24), fill=(255, 255, 255), anchor="lm")
    draw.text((W - 24, y + 30), "2.685.930đ", font=font("Arial Bold.ttf", 26), fill=(255, 232, 79), anchor="rm")
    y += 100

    draw.text((24, y), "Hình thức thanh toán: Thẻ ngân hàng — VISA ****4821", font=font("Arial.ttf", 19), fill=(60, 60, 60), anchor="lm")
    y += 60

    # Cac nut chuc nang
    buttons = [("THANH TOÁN", (31, 111, 67)), ("IN HÓA ĐƠN", (74, 144, 226)), ("HỦY GIAO DỊCH", (200, 20, 20))]
    bw, bh, gap = 290, 70, 25
    bx = 24
    for label, color in buttons:
        draw.rounded_rectangle([bx, y, bx + bw, y + bh], radius=10, fill=color)
        draw.text((bx + bw // 2, y + bh // 2), label, font=font("Arial Bold.ttf", 19), fill=(255, 255, 255), anchor="mm")
        bx += bw + gap

    img.save(os.path.join(OUT_DIR, "03_anh_chup_man_hinh_pos.png"), "PNG")
    lines = ["GreenMart POS — Quầy 03", "Mã hóa đơn: HD20260817-0342", "Thu ngân: Nguyễn Thị Hoa"]
    lines += [f"{n} x{q} {p} = {t}" for n, q, p, t in items]
    lines += ["Tạm tính: 2.769.000đ", "Giảm giá (thẻ TV): -83.070đ", "TỔNG CỘNG: 2.685.930đ",
              "Hình thức thanh toán: Thẻ ngân hàng — VISA ****4821", "THANH TOÁN", "IN HÓA ĐƠN", "HỦY GIAO DỊCH"]
    return "\n".join(lines)


# =====================================================================================
# 4) BIEN HIEU QUAY (bang hieu vat ly, co hieu ung anh chup)
# =====================================================================================
def bien_hieu_quay():
    W, H = 1200, 700
    img = Image.new("RGB", (W, H), (18, 60, 42))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, W - 20, H - 20], outline=(255, 232, 79), width=8)
    draw.text((W // 2, 160), "GREENMART", font=font("Arial Black.ttf", 70), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 260), "VIỆT NAM", font=font("Arial Bold.ttf", 40), fill=(255, 232, 79), anchor="ma")
    draw.line([(200, 350), (W - 200, 350)], fill=(255, 232, 79), width=3)
    draw.text((W // 2, 400), "QUẦY CHĂM SÓC KHÁCH HÀNG", font=font("Arial Bold.ttf", 58), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 490), "Đổi trả — Bảo hành — Khiếu nại — Tư vấn", font=font("Arial.ttf", 32), fill=(220, 220, 220), anchor="ma")
    draw.text((W // 2, 580), "Giờ phục vụ: 08:00 - 22:00 hằng ngày", font=font("Arial.ttf", 28), fill=(200, 200, 200), anchor="ma")
    photo_effect(img, rotate_deg=3.0, noise_amt=16, blur=0.9, out_path=os.path.join(OUT_DIR, "04_bien_hieu_quay.png"))
    return ("GREENMART VIỆT NAM. QUẦY CHĂM SÓC KHÁCH HÀNG. Đổi trả — Bảo hành — Khiếu nại — Tư vấn. "
            "Giờ phục vụ: 08:00 - 22:00 hằng ngày")


# =====================================================================================
# 5) PHIEU BAO HANH (the vat ly da dien thong tin, co hieu ung anh chup)
# =====================================================================================
def phieu_bao_hanh():
    W, H = 1200, 800
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, H], outline=(31, 111, 67), width=6)
    draw.rectangle([0, 0, W, 100], fill=(31, 111, 67))
    draw.text((30, 50), "PHIẾU BẢO HÀNH SẢN PHẨM", font=font("Arial Bold.ttf", 40), fill=(255, 255, 255), anchor="lm")
    draw.text((W - 30, 50), "GreenMart Việt Nam", font=font("Arial Bold.ttf", 26), fill=(255, 232, 79), anchor="rm")

    fields = [
        ("Số phiếu bảo hành:", "BH-2026-081745"),
        ("Tên sản phẩm:", "Tủ lạnh Inverter 300L - CoolMax"),
        ("Số serial:", "CM300-VN-2026-77821"),
        ("Ngày mua:", "05/08/2026"),
        ("Cửa hàng mua:", "GM-HN01 — Cầu Giấy, Hà Nội"),
        ("Thời hạn bảo hành:", "24 tháng (đến hết ngày 05/08/2028)"),
        ("Khách hàng:", "Phạm Văn Đức — SĐT: 0987654321"),
    ]
    y = 150
    f_label = font("Arial Bold.ttf", 27)
    f_value = font("Arial.ttf", 27)
    for label, value in fields:
        draw.text((40, y), label, font=f_label, fill=(31, 111, 67))
        draw.text((420, y), value, font=f_value, fill=(30, 30, 30))
        y += 58

    y += 20
    draw.line([(40, y), (W - 40, y)], fill=(200, 200, 200), width=2)
    y += 30
    note = "Vui lòng giữ phiếu này để được hỗ trợ bảo hành. Mất phiếu vẫn có thể tra cứu qua số serial trên hệ thống."
    draw.text((40, y), note, font=font("Arial Italic.ttf", 20), fill=(90, 90, 90))
    y += 60
    draw.text((40, y), "Xác nhận cửa hàng:", font=f_label, fill=(31, 111, 67))
    draw.text((W - 300, y), "(Đã đóng dấu)", font=font("Arial Italic.ttf", 22), fill=(90, 90, 90))

    photo_effect(img, rotate_deg=2.0, noise_amt=12, blur=0.6, out_path=os.path.join(OUT_DIR, "05_phieu_bao_hanh.png"))
    lines = ["PHIẾU BẢO HÀNH SẢN PHẨM — GreenMart Việt Nam"] + [f"{l} {v}" for l, v in fields] + [note]
    return "\n".join(lines)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}
    results["01_poster_khuyen_mai.png"] = poster_khuyen_mai()
    results["02_bang_gia_viet_tay.png"] = bang_gia_viet_tay()
    results["03_anh_chup_man_hinh_pos.png"] = anh_chup_man_hinh_pos()
    results["04_bien_hieu_quay.png"] = bien_hieu_quay()
    results["05_phieu_bao_hanh.png"] = phieu_bao_hanh()
    for fn, text in results.items():
        print(f"  {fn}: {len(text)} ký tự reference_text")
    print("Done: 5 png files ->", os.path.abspath(OUT_DIR))
