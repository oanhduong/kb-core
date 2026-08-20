"""Sinh 5 file .xlsx cho test corpus ingest nganh retail Viet Nam:
bang gia SKU (300 dong), ton kho theo store (nhieu sheet/store), lich khuyen mai
(merged cell header), bang chiet khau theo bac, danh muc nha cung cap.

Tra ve dict metadata (khong bat buoc dung, gen_ground_truth.py se tu doc lai file that)."""
import os
import random
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "xlsx")
random.seed(42)

HEADER_FILL = PatternFill(start_color="1F6F43", end_color="1F6F43", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def style_header(ws, ncols, row=1, widths=None):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = (widths[c - 1] if widths else 20)


# =====================================================================================
# 1) BANG GIA SKU - 300 DONG (sku / ten / gia / category)
# =====================================================================================
# Moi template co dai gia tri {n} VA dai gia rieng, phu hop don vi/tam gia cua chinh no
# (tranh "Binh nong lanh 10000L" hay "Am sieu toc" gia ngang tivi).
CATEGORY_TEMPLATES = {
    "Điện máy": {
        "products": [
            ("Tivi LED {n} inch", [32, 40, 43, 50, 55, 65, 75], 3_500_000, 15_000_000),
            ("Tủ lạnh Inverter {n}L", [90, 150, 180, 220, 260, 300, 350, 450], 4_000_000, 13_000_000),
            ("Máy giặt cửa {door} {n}kg", [7, 7.5, 8, 8.5, 9, 9.5, 10, 11], 4_500_000, 9_500_000),
            ("Máy lạnh {n}HP Inverter", [1, 1.5, 2, 2.5], 6_000_000, 14_000_000),
            ("Lò vi sóng {n}L", [20, 23, 25, 28, 30], 1_200_000, 2_800_000),
            ("Máy sấy quần áo {n}kg", [7, 8, 9], 5_000_000, 9_000_000),
            ("Bình nóng lạnh {n}L", [15, 20, 30, 50], 1_500_000, 4_000_000),
            ("Quạt điều hòa hơi nước {n}L", [4, 5, 6, 8], 2_500_000, 5_500_000),
        ],
        "brands": ["TechVision", "CoolMax", "WashPro", "AirCool", "HomeChef", "DryFast", "HeatSure", "BreezeCo"],
    },
    "Điện tử": {
        "products": [
            ("Tai nghe bluetooth chống ồn thế hệ {n}", [1, 2, 3, 4], 390_000, 1_890_000),
            ("Loa bluetooth di động {n}W", [10, 15, 20, 30, 40], 350_000, 1_590_000),
            ("Sạc dự phòng {n}mAh", [10000, 20000, 30000], 250_000, 690_000),
            ("Đồng hồ thông minh series {n}", [3, 4, 5, 6], 990_000, 3_490_000),
            ("Webcam họp trực tuyến {n}p", [720, 1080], 450_000, 990_000),
            ("Ổ cứng di động {n}GB", [500, 1000, 2000], 890_000, 2_390_000),
            ("Chuột không dây văn phòng model {n}", [1, 2, 3], 150_000, 450_000),
            ("Bàn phím cơ {n} phím", [61, 68, 87, 104], 590_000, 1_890_000),
        ],
        "brands": ["SoundMax", "NovaPhone", "SwiftBook", "PureFlow", "ClickPro", "KeyForce", "LinkOne", "ByteHub"],
    },
    "Gia dụng": {
        "products": [
            ("Nồi chiên không dầu {n}L", [3, 4, 5, 6, 8], 890_000, 2_590_000),
            ("Máy xay sinh tố {n}W", [300, 450, 600, 800], 390_000, 990_000),
            ("Bếp từ {n} vùng nấu", [1, 2, 3], 990_000, 3_490_000),
            ("Ấm siêu tốc {n}L", [1.2, 1.5, 1.8, 2.0], 250_000, 590_000),
            ("Nồi cơm điện {n}L", [1.0, 1.5, 1.8, 2.2], 450_000, 1_590_000),
            ("Bàn ủi hơi nước {n}W", [1200, 1600, 2000], 390_000, 990_000),
            ("Máy hút bụi cầm tay {n}W", [400, 600, 800], 590_000, 1_590_000),
            ("Máy lọc không khí {n}m2", [20, 30, 40, 60], 1_990_000, 4_990_000),
        ],
        "brands": ["HomeChef", "IronMate", "PureFlow", "CleanHome", "FreshAir", "CookWell", "TidyHouse", "AeroPure"],
    },
    "Thực phẩm": {
        "products": [
            ("Gạo thơm đặc sản {n}kg", [2, 5, 10, 25], 55_000, 350_000),
            ("Dầu ăn hướng dương {n}L", [1, 2, 5], 35_000, 165_000),
            ("Sữa tươi tiệt trùng lốc {n} hộp", [4, 6, 12, 48], 35_000, 480_000),
            ("Mì ăn liền thùng {n} gói", [24, 30, 40], 120_000, 210_000),
            ("Nước rửa chén {n}ml", [500, 750, 1000], 25_000, 55_000),
            ("Bột giặt {n}kg", [1.5, 2.7, 3, 6], 55_000, 165_000),
            ("Cà phê hòa tan hộp {n} gói", [10, 20, 30], 35_000, 95_000),
            ("Nước suối thùng {n} chai", [12, 20, 24], 55_000, 95_000),
        ],
        "brands": ["GoldRice", "SunOil", "DairyFarm", "QuickMeal", "CleanHome", "FreshWash", "MorningCup", "AquaPure"],
    },
    "Mỹ phẩm": {
        "products": [
            ("Sữa rửa mặt {n}ml", [50, 100, 150, 200], 65_000, 250_000),
            ("Kem chống nắng SPF{n}", [30, 50], 120_000, 350_000),
            ("Dầu gội {n}ml", [180, 340, 650], 65_000, 220_000),
            ("Sữa tắm {n}ml", [200, 400, 800], 75_000, 250_000),
            ("Son dưỡng môi mã {n}", [1, 2, 3, 4], 39_000, 120_000),
            ("Nước tẩy trang {n}ml", [100, 200, 500], 65_000, 190_000),
            ("Kem dưỡng ẩm {n}ml", [30, 50, 100], 99_000, 450_000),
            ("Xịt khoáng {n}ml", [50, 100, 150], 89_000, 250_000),
        ],
        "brands": ["PureSkin", "SunGuard", "SilkHair", "FreshBody", "LipCare", "CleanFace", "HydraPlus", "MistCo"],
    },
    "Thời trang": {
        "products": [
            ("Áo thun cổ tròn size {n}", ["S", "M", "L", "XL"], 99_000, 250_000),
            ("Quần jean size {n}", [28, 29, 30, 31, 32], 250_000, 590_000),
            ("Áo sơ mi công sở size {n}", ["S", "M", "L", "XL"], 199_000, 450_000),
            ("Giày thể thao size {n}", [38, 39, 40, 41, 42], 390_000, 890_000),
            ("Túi xách công sở mã {n}", [1, 2, 3, 4, 5], 350_000, 890_000),
            ("Nón bucket mã {n}", [1, 2, 3], 99_000, 220_000),
            ("Thắt lưng da mã {n}", [1, 2, 3], 199_000, 450_000),
            ("Vớ cotton bộ {n} đôi", [3, 5, 10], 49_000, 150_000),
        ],
        "brands": ["UrbanWear", "DenimCo", "OfficeLine", "StepFast", "CarryOn", "SunCap", "LeatherPro", "SoftStep"],
    },
    "Văn phòng phẩm": {
        "products": [
            ("Bút bi hộp {n} cây", [10, 12, 20, 50], 15_000, 45_000),
            ("Vở học sinh {n} trang", [72, 96, 120, 200], 8_000, 15_000),
            ("Giấy in A4 {n} ràm/thùng", [1, 5, 10], 75_000, 650_000),
            ("Balo laptop {n} lít", [15, 20, 25, 30], 250_000, 750_000),
            ("Máy tính cầm tay {n} số", [8, 10, 12], 55_000, 250_000),
            ("Kẹp file hộp {n} cái", [12, 24, 50], 25_000, 65_000),
            ("Bút highlight bộ {n} màu", [4, 6, 8], 25_000, 55_000),
            ("Sổ tay bìa da {n} trang", [100, 150, 200], 65_000, 250_000),
        ],
        "brands": ["WriteWell", "StudyPlus", "PrintSure", "CarryOn", "CalcFast", "ClipIt", "MarkBright", "NoteCraft"],
    },
    "Đồ chơi trẻ em": {
        "products": [
            ("Bộ xếp hình {n} mảnh", [50, 100, 200, 500], 99_000, 450_000),
            ("Xe điều khiển từ xa tỉ lệ 1:{n}", [10, 16, 24], 250_000, 890_000),
            ("Búp bê thời trang bộ {n} món", [3, 5, 8], 150_000, 450_000),
            ("Bộ đồ chơi bác sĩ {n} món", [10, 15, 20], 99_000, 250_000),
            ("Xe đạp trẻ em size {n} inch", [12, 16, 20], 990_000, 2_200_000),
            ("Bộ Lego mini {n} mảnh", [150, 300, 500], 250_000, 890_000),
            ("Thú nhồi bông cao {n}cm", [20, 30, 40, 60], 99_000, 350_000),
            ("Bảng vẽ điện tử {n} inch", [8.5, 10, 12], 250_000, 650_000),
        ],
        "brands": ["FunBlock", "SpeedyKid", "StyleDoll", "LittleDoc", "PedalGo", "BrickJoy", "SoftHug", "DrawPad"],
    },
}


def _gen_sku_rows(target_total=300):
    rows = []
    categories = list(CATEGORY_TEMPLATES.keys())
    per_cat = target_total // len(categories)
    remainder = target_total - per_cat * len(categories)
    counters = {cat: 0 for cat in categories}
    for ci, cat in enumerate(categories):
        tmpl = CATEGORY_TEMPLATES[cat]
        count = per_cat + (1 if ci < remainder else 0)
        prefix = "".join(w[0] for w in cat.upper().split())[:3]
        for i in range(count):
            product_tmpl, n_choices, price_lo, price_hi = tmpl["products"][i % len(tmpl["products"])]
            brand = tmpl["brands"][i % len(tmpl["brands"])]
            n = random.choice(n_choices)
            name = product_tmpl.format(n=n, door=random.choice(["trước", "trên"]))
            counters[cat] += 1
            sku = f"{prefix}-{counters[cat]:03d}"
            price = random.randint(price_lo // 1000, price_hi // 1000) * 1000
            rows.append([sku, f"{name} - {brand}", price, cat])
    random.shuffle(rows)
    return rows[:target_total]


def bang_gia_sku():
    wb = Workbook()
    ws = wb.active
    ws.title = "BangGiaSKU"
    headers = ["sku", "ten_san_pham", "gia", "category"]
    ws.append(headers)
    style_header(ws, len(headers), widths=[14, 46, 14, 18])
    rows = _gen_sku_rows(300)
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    wb.save(os.path.join(OUT_DIR, "01_bang_gia_sku.xlsx"))
    return {"rows": len(rows)}


# =====================================================================================
# 2) TON KHO THEO STORE - NHIEU SHEET, MOI SHEET MOT STORE
# =====================================================================================
STORES = [
    ("GM-HN01", "Cầu Giấy, Hà Nội"), ("GM-HN02", "Long Biên, Hà Nội"),
    ("GM-HP01", "Hải Phòng"), ("GM-HCM01", "Quận 1, TP.HCM"),
    ("GM-HCM02", "Thủ Đức, TP.HCM"), ("GM-CT01", "Cần Thơ"),
    ("GM-DN01", "Đà Nẵng"), ("GM-BD01", "Bình Dương"),
]

STOCK_SKUS = [
    ("DM-001", "Tivi LED 55 inch - TechVision", "Điện máy"),
    ("DM-002", "Tủ lạnh Inverter 300L - CoolMax", "Điện máy"),
    ("DT-001", "Tai nghe bluetooth 5 - SoundMax", "Điện tử"),
    ("GD-001", "Nồi chiên không dầu 6L - HomeChef", "Gia dụng"),
    ("TP-001", "Gạo thơm đặc sản 5kg - GoldRice", "Thực phẩm"),
    ("TP-002", "Sữa tươi tiệt trùng lốc 48 hộp - DairyFarm", "Thực phẩm"),
    ("MP-001", "Sữa rửa mặt 100ml - PureSkin", "Mỹ phẩm"),
    ("TT-001", "Áo thun cổ tròn size L - UrbanWear", "Thời trang"),
    ("VP-001", "Giấy in A4 5 ràm/thùng - PrintSure", "Văn phòng phẩm"),
    ("DC-001", "Bộ Lego mini 300 mảnh - BrickJoy", "Đồ chơi trẻ em"),
]


def ton_kho_theo_store():
    wb = Workbook()
    wb.remove(wb.active)
    headers = ["sku", "ten_san_pham", "category", "ton_kho", "ton_kho_toi_thieu", "trang_thai", "cap_nhat_luc"]
    for code, address in STORES:
        ws = wb.create_sheet(title=code)
        ws.append(headers)
        style_header(ws, len(headers), widths=[12, 42, 16, 12, 16, 14, 18])
        ws.append([f"Cửa hàng {code} — {address}"] + [""] * (len(headers) - 1))
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
        ws.cell(row=2, column=1).font = Font(italic=True, color="1F6F43")
        for sku, name, cat in STOCK_SKUS:
            qty = random.randint(0, 120)
            min_qty = random.choice([10, 15, 20, 30])
            status = "Hết hàng" if qty == 0 else ("Sắp hết" if qty < min_qty else "Đủ hàng")
            ws.append([sku, name, cat, qty, min_qty, status, "2026-08-20 08:00"])
    wb.save(os.path.join(OUT_DIR, "02_ton_kho_theo_store.xlsx"))
    return {"sheets": len(STORES)}


# =====================================================================================
# 3) LICH KHUYEN MAI - CO MERGED CELL O HEADER
# =====================================================================================
def lich_khuyen_mai():
    wb = Workbook()
    ws = wb.active
    ws.title = "LichKhuyenMai_Q3_2026"

    # Header 2 dong, co merged cell nhom cot: "Thong tin chuong trinh" (A-C) va "Thoi gian ap dung" (D-E)
    ws.merge_cells("A1:C1")
    ws["A1"] = "THÔNG TIN CHƯƠNG TRÌNH"
    ws.merge_cells("D1:E1")
    ws["D1"] = "THỜI GIAN ÁP DỤNG"
    ws.merge_cells("F1:G1")
    ws["F1"] = "MỨC ƯU ĐÃI"
    for cell_ref in ["A1", "D1", "F1"]:
        ws[cell_ref].fill = HEADER_FILL
        ws[cell_ref].font = HEADER_FONT
        ws[cell_ref].alignment = Alignment(horizontal="center", vertical="center")

    headers2 = ["Tên chương trình", "Ngành hàng áp dụng", "Phạm vi cửa hàng",
                "Ngày bắt đầu", "Ngày kết thúc", "Mức giảm", "Ghi chú"]
    for c, h in enumerate(headers2, start=1):
        cell = ws.cell(row=2, column=c, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = 24

    rows = [
        ("Sale khai trương chi nhánh Thủ Đức", "Toàn ngành hàng", "Chỉ GM-HCM02", "2026-07-01", "2026-07-07", "Giảm đến 30%", "Kèm quà tặng khách hàng đầu tiên"),
        ("Tuần lễ điện máy xanh", "Điện máy - Gia dụng", "Toàn hệ thống", "2026-07-10", "2026-07-20", "Giảm 5-15%", "Ưu tiên hàng tồn kho cao"),
        ("Săn sale cuối tuần FMCG", "Thực phẩm", "Toàn hệ thống", "2026-07-25", "2026-07-27", "Giảm 10%", "Áp dụng thẻ thành viên"),
        ("Mùa tựu trường", "Điện tử, Văn phòng phẩm", "Toàn hệ thống", "2026-08-15", "2026-08-31", "Giảm đến 20%", "Tặng balo cho hóa đơn trên 2 triệu"),
        ("Flash sale giữa tháng 9", "Toàn ngành hàng", "Toàn hệ thống + Online", "2026-09-15", "2026-09-16", "Giảm sốc 50%", "Giới hạn 2 sản phẩm/khách hàng"),
        ("Tri ân khách hàng thân thiết", "Toàn ngành hàng", "Toàn hệ thống", "2026-09-20", "2026-09-25", "Giảm thêm 5% cho hạng Vàng/Bạch Kim", "Cộng dồn với voucher sinh nhật"),
    ]
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A3"
    wb.save(os.path.join(OUT_DIR, "03_lich_khuyen_mai.xlsx"))
    return {"rows": len(rows)}


# =====================================================================================
# 4) BANG CHIET KHAU THEO BAC
# =====================================================================================
def bang_chiet_khau_theo_bac():
    wb = Workbook()
    ws = wb.active
    ws.title = "ChietKhauTheoBac"
    headers = ["Ngành hàng", "Bậc", "Giá trị đơn hàng từ (VNĐ)", "Giá trị đơn hàng đến (VNĐ)", "% Chiết khấu", "Ghi chú"]
    ws.append(headers)
    style_header(ws, len(headers), widths=[20, 10, 24, 24, 14, 30])

    tiers_by_cat = {
        "Điện máy - Gia dụng": [
            (1, 0, 4_999_999, "0%", "Không áp dụng chiết khấu"),
            (2, 5_000_000, 14_999_999, "3%", "Áp dụng khi mua từ 1 sản phẩm giá trị cao"),
            (3, 15_000_000, 29_999_999, "5%", "Cộng dồn với voucher khuyến mãi đang chạy"),
            (4, 30_000_000, None, "8%", "Cần quản lý cửa hàng phê duyệt trực tiếp"),
        ],
        "Thực phẩm - Tiêu dùng nhanh": [
            (1, 0, 999_999, "0%", "Không áp dụng chiết khấu"),
            (2, 1_000_000, 2_999_999, "2%", "Áp dụng cho khách hàng thành viên"),
            (3, 3_000_000, 6_999_999, "4%", "Áp dụng cho đơn hàng doanh nghiệp nhỏ"),
            (4, 7_000_000, None, "6%", "Áp dụng hợp đồng mua sỉ định kỳ"),
        ],
        "Thời trang - Phụ kiện": [
            (1, 0, 1_499_999, "0%", "Không áp dụng chiết khấu"),
            (2, 1_500_000, 3_999_999, "5%", "Áp dụng cuối mùa hoặc theo bộ sưu tập"),
            (3, 4_000_000, None, "10%", "Áp dụng khách hàng hạng Vàng trở lên"),
        ],
    }
    for cat, tiers in tiers_by_cat.items():
        for bac, tu, den, pct, note in tiers:
            den_str = f"{den:,}".replace(",", ".") if den is not None else "Không giới hạn"
            ws.append([cat, bac, f"{tu:,}".replace(",", "."), den_str, pct, note])
    wb.save(os.path.join(OUT_DIR, "04_bang_chiet_khau_theo_bac.xlsx"))
    return {"rows": sum(len(v) for v in tiers_by_cat.values())}


# =====================================================================================
# 5) DANH MUC NHA CUNG CAP
# =====================================================================================
def danh_muc_nha_cung_cap():
    wb = Workbook()
    ws = wb.active
    ws.title = "DanhMucNhaCungCap"
    headers = ["Mã NCC", "Tên nhà cung cấp", "Ngành hàng cung cấp", "Người liên hệ", "SĐT", "Email",
               "Địa chỉ", "Mã số thuế", "Ngày hợp tác"]
    ws.append(headers)
    style_header(ws, len(headers), widths=[10, 34, 20, 18, 14, 26, 34, 16, 14])

    suppliers = [
        ("NCC-001", "Công ty TNHH Thương mại Kỹ nghệ Phương Nam", "Điện máy", "Nguyễn Văn Hùng", "0901234567", "hung.nguyen@phuongnam.vn", "12 Nguyễn Văn Linh, Q.7, TP.HCM", "0301234567", "2021-03-15"),
        ("NCC-002", "Công ty Cổ phần Thực phẩm Sạch Miền Tây", "Thực phẩm", "Trần Thị Lan", "0912345678", "lan.tran@mientay.vn", "45 Trần Hưng Đạo, Cần Thơ", "0302345678", "2020-06-01"),
        ("NCC-003", "Công ty TNHH Điện tử Việt Á", "Điện tử", "Lê Minh Tuấn", "0923456789", "tuan.le@vieta.vn", "88 Láng Hạ, Đống Đa, Hà Nội", "0303456789", "2022-01-10"),
        ("NCC-004", "Công ty TNHH Mỹ phẩm An Nhiên", "Mỹ phẩm", "Phạm Thu Hà", "0934567890", "ha.pham@annhien.vn", "23 Nguyễn Trãi, Thanh Xuân, Hà Nội", "0304567890", "2021-09-20"),
        ("NCC-005", "Công ty Cổ phần Thời trang Sài Gòn Mới", "Thời trang", "Đỗ Quang Huy", "0945678901", "huy.do@saigonmoi.vn", "156 Lê Văn Sỹ, Q.3, TP.HCM", "0305678901", "2019-11-05"),
        ("NCC-006", "Công ty TNHH Văn phòng phẩm Hồng Phát", "Văn phòng phẩm", "Vũ Thị Mai", "0956789012", "mai.vu@hongphat.vn", "67 Cách Mạng Tháng 8, Q.10, TP.HCM", "0306789012", "2020-02-14"),
        ("NCC-007", "Công ty Cổ phần Đồ chơi Việt Nhật", "Đồ chơi trẻ em", "Hoàng Anh Dũng", "0967890123", "dung.hoang@vietnhat.vn", "34 Nguyễn Chí Thanh, Ba Đình, Hà Nội", "0307890123", "2022-05-30"),
        ("NCC-008", "Công ty TNHH Gia dụng Đức Thịnh", "Gia dụng", "Ngô Thị Bích", "0978901234", "bich.ngo@ducthinh.vn", "9 Điện Biên Phủ, Hải Châu, Đà Nẵng", "0308901234", "2021-07-18"),
    ]
    for r in suppliers:
        ws.append(r)
    wb.save(os.path.join(OUT_DIR, "05_danh_muc_nha_cung_cap.xlsx"))
    return {"rows": len(suppliers)}


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    print("  bang_gia_sku:", bang_gia_sku())
    print("  ton_kho_theo_store:", ton_kho_theo_store())
    print("  lich_khuyen_mai:", lich_khuyen_mai())
    print("  bang_chiet_khau_theo_bac:", bang_chiet_khau_theo_bac())
    print("  danh_muc_nha_cung_cap:", danh_muc_nha_cung_cap())
    print("Done: 5 xlsx files ->", os.path.abspath(OUT_DIR))
