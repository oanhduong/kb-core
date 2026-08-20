"""Sinh 5 file anh: 3 poster khuyen mai + 2 anh bang gia viet tay (mo phong anh chup dien thoai)."""
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


def photo_effect(img, rotate_deg=1.5, noise_amt=10, blur=0.5, jpeg_quality=72, out_path=None):
    arr = np.array(img.convert("RGB")).astype(np.int16)
    noise = np.random.randint(-noise_amt, noise_amt, arr.shape[:2])
    for c in range(3):
        arr[:, :, c] = np.clip(arr[:, :, c] + noise, 0, 255)
    img2 = Image.fromarray(arr.astype(np.uint8))
    img2 = img2.rotate(random.uniform(-rotate_deg, rotate_deg), expand=True, fillcolor=(30, 30, 30))
    img2 = img2.filter(ImageFilter.GaussianBlur(radius=blur))
    img2 = ImageEnhance.Brightness(img2).enhance(random.uniform(0.92, 1.05))
    img2 = ImageEnhance.Contrast(img2).enhance(random.uniform(0.95, 1.08))
    img2.save(out_path, "JPEG", quality=jpeg_quality)


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


def poster_grand_opening():
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
    photo_effect(img, out_path=os.path.join(OUT_DIR, "01_poster_khai_truong_thu_duc.jpg"))


def poster_weekend_sale():
    W, H = 1200, 1500
    img = gradient_bg(W, H, (36, 110, 74), (18, 60, 42))
    draw = ImageDraw.Draw(img)
    draw.text((W // 2, 120), "GREENMART", font=font("Arial Black.ttf", 70), fill=(255, 232, 79), anchor="ma")
    draw.rectangle([80, 260, W - 80, 560], outline=(255, 255, 255), width=6)
    draw.text((W // 2, 300), "SĂN SALE", font=font("Arial Bold.ttf", 90), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 420), "CUỐI TUẦN", font=font("Arial Bold.ttf", 90), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 650), "GIẢM 10%", font=font("Arial Bold.ttf", 130), fill=(255, 122, 89), anchor="ma", stroke_width=3, stroke_fill=(255, 255, 255))
    draw.text((W // 2, 790), "cho toàn bộ ngành hàng tiêu dùng nhanh", font=font("Arial.ttf", 34), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 900), "Áp dụng: 25/07 - 27/07/2026", font=font("Arial Bold.ttf", 36), fill=(255, 232, 79), anchor="ma")
    draw.text((W // 2, 970), "Ưu đãi thêm cho khách hàng có thẻ thành viên", font=font("Arial.ttf", 30), fill=(220, 220, 220), anchor="ma")
    for i in range(6):
        x = 150 + i * 170
        draw.ellipse([x, 1150, x + 60, 1210], fill=(255, 232, 79))
    draw.text((W // 2, 1300), "Hệ thống siêu thị GreenMart toàn quốc", font=font("Arial.ttf", 30), fill=(255, 255, 255), anchor="ma")
    photo_effect(img, out_path=os.path.join(OUT_DIR, "02_poster_sale_cuoi_tuan.jpg"))


def poster_back_to_school():
    W, H = 1200, 1500
    img = gradient_bg(W, H, (74, 144, 226), (34, 88, 168))
    draw = ImageDraw.Draw(img)
    draw.text((W // 2, 110), "GREENMART", font=font("Arial Black.ttf", 68), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 260), "MÙA TỰU TRƯỜNG", font=font("Arial Bold.ttf", 74), fill=(255, 232, 79), anchor="ma")
    draw.text((W // 2, 380), "Văn phòng phẩm & Điện tử", font=font("Arial Bold.ttf", 42), fill=(255, 255, 255), anchor="ma")
    draw.rounded_rectangle([150, 500, W - 150, 900], radius=30, fill=(255, 255, 255))
    draw.text((W // 2, 560), "GIẢM ĐẾN 20%", font=font("Arial Bold.ttf", 80), fill=(200, 20, 20), anchor="ma")
    draw.text((W // 2, 680), "+ Tặng balo cho hóa đơn", font=font("Arial Bold.ttf", 34), fill=(30, 30, 30), anchor="ma")
    draw.text((W // 2, 730), "từ 2.000.000đ", font=font("Arial Bold.ttf", 34), fill=(30, 30, 30), anchor="ma")
    draw.text((W // 2, 820), "15/08 - 31/08/2026", font=font("Arial.ttf", 32), fill=(80, 80, 80), anchor="ma")
    draw.text((W // 2, 1050), "Laptop • Balo • Sách vở • Dụng cụ học tập", font=font("Arial.ttf", 32), fill=(255, 255, 255), anchor="ma")
    draw.text((W // 2, 1300), "Hotline tư vấn: 1900 1234 — greenmart.vn", font=font("Arial.ttf", 28), fill=(230, 230, 230), anchor="ma")
    photo_effect(img, out_path=os.path.join(OUT_DIR, "03_poster_mua_tuu_truong.jpg"))


def handwritten_board(filename, title, rows):
    W, H = 1100, 1400
    img = Image.new("RGB", (W, H), (238, 230, 205))
    draw = ImageDraw.Draw(img)
    # ruled lines like a market price board
    for y in range(180, H - 60, 90):
        draw.line([(40, y), (W - 40, y)], fill=(200, 190, 160), width=2)
    draw.rectangle([0, 0, W, 120], fill=(60, 110, 60))
    draw.text((W // 2, 60), title, font=font("Arial Bold.ttf", 48), fill=(255, 255, 255), anchor="mm")

    y = 200
    for name, price in rows:
        jx = random.randint(-6, 6)
        jy = random.randint(-8, 8)
        rot_img = Image.new("RGBA", (900, 90), (0, 0, 0, 0))
        rdraw = ImageDraw.Draw(rot_img)
        rdraw.text((10, 10), name, font=font("Arial Bold.ttf", 40), fill=(30, 30, 30, 255))
        rdraw.text((650, 10), price, font=font("Arial Black.ttf", 44), fill=(160, 20, 20, 255))
        rot_img = rot_img.rotate(random.uniform(-1.5, 1.5), expand=True)
        img.paste(rot_img, (80 + jx, y + jy), rot_img)
        y += 90

    draw.text((W // 2, H - 30), "Giá có thể thay đổi trong ngày — vui lòng hỏi nhân viên quầy", font=font("Arial.ttf", 24), fill=(90, 80, 60), anchor="mm")
    photo_effect(img, rotate_deg=2.5, noise_amt=14, blur=0.8, jpeg_quality=65, out_path=os.path.join(OUT_DIR, filename))


def handwritten_vegetable_board():
    rows = [
        ("Rau muống bó", "8.000đ"),
        ("Cà chua (kg)", "22.000đ"),
        ("Khoai tây (kg)", "18.000đ"),
        ("Bí đỏ (kg)", "15.000đ"),
        ("Dưa leo (kg)", "16.000đ"),
        ("Cải ngọt bó", "9.000đ"),
        ("Hành lá bó", "6.000đ"),
        ("Ớt sừng (kg)", "35.000đ"),
    ]
    handwritten_board("04_bang_gia_viet_tay_quay_rau_cu.jpg", "QUẦY RAU CỦ TƯƠI - GIÁ HÔM NAY", rows)


def handwritten_meat_board():
    rows = [
        ("Thịt ba chỉ (kg)", "145.000đ"),
        ("Thịt nạc vai (kg)", "135.000đ"),
        ("Sườn non (kg)", "165.000đ"),
        ("Cá basa fillet (kg)", "68.000đ"),
        ("Tôm sú (kg)", "220.000đ"),
        ("Cá diêu hồng (kg)", "62.000đ"),
        ("Thịt bò thăn (kg)", "265.000đ"),
        ("Gà ta nguyên con (kg)", "95.000đ"),
    ]
    handwritten_board("05_bang_gia_viet_tay_quay_thit_ca.jpg", "QUẦY THỊT CÁ TƯƠI - GIÁ HÔM NAY", rows)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    poster_grand_opening()
    poster_weekend_sale()
    poster_back_to_school()
    handwritten_vegetable_board()
    handwritten_meat_board()
    print("Done: 5 image files ->", os.path.abspath(OUT_DIR))
