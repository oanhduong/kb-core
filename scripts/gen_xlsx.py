"""Sinh 5 file .xlsx: bang gia SKU, ton kho theo store, lich khuyen mai (GreenMart Viet Nam)."""
import os
import random
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "xlsx")
random.seed(42)

HEADER_FILL = PatternFill(start_color="1F6F43", end_color="1F6F43", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def style_header(ws, ncols, row=1):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = 20


def sku_price_electronics():
    wb = Workbook()
    ws = wb.active
    ws.title = "BangGiaSKU_DienMay"
    headers = ["Mã SKU", "Tên sản phẩm", "Thương hiệu", "Đơn vị", "Giá niêm yết (VNĐ)", "Giá khuyến mãi (VNĐ)", "VAT (%)", "Ngành hàng"]
    ws.append(headers)
    style_header(ws, len(headers))
    items = [
        ("DM-TV-55UHD", "Tivi 55 inch 4K UHD", "TechVision", "Cái", 12990000, 10990000),
        ("DM-TL-300L", "Tủ lạnh 300L Inverter", "CoolMax", "Cái", 9490000, 8790000),
        ("DM-MG-9KG", "Máy giặt cửa trước 9kg", "WashPro", "Cái", 7290000, 6990000),
        ("DM-DH-9000BTU", "Máy lạnh 1HP Inverter", "AirCool", "Bộ", 8990000, 7990000),
        ("DM-NCD-1.8L", "Nồi cơm điện tử 1.8L", "HomeChef", "Cái", 890000, 749000),
        ("DM-QĐ-BAN", "Quạt đứng cánh 3 tốc độ", "Windy", "Cái", 590000, 490000),
        ("DM-BUI-2000W", "Bàn ủi hơi nước 2000W", "IronMate", "Cái", 450000, 399000),
        ("DM-LOC-NUOC", "Máy lọc nước RO 9 lõi", "PureFlow", "Bộ", 4990000, 4590000),
        ("DM-ĐT-A15", "Điện thoại thông minh dòng A15", "NovaPhone", "Cái", 5990000, 5490000),
        ("DM-LAP-14", "Laptop văn phòng 14 inch", "SwiftBook", "Cái", 13990000, 12490000),
    ]
    for sku, name, brand, unit, price, promo in items:
        ws.append([sku, name, brand, unit, price, promo, 8, "Điện máy - Gia dụng"])
    wb.save(os.path.join(OUT_DIR, "01_bang_gia_sku_dien_may.xlsx"))


def sku_price_fmcg():
    wb = Workbook()
    ws = wb.active
    ws.title = "BangGiaSKU_TieuDungNhanh"
    headers = ["Mã SKU", "Tên sản phẩm", "Thương hiệu", "Quy cách", "Giá niêm yết (VNĐ)", "Giá khuyến mãi (VNĐ)", "VAT (%)", "Ngành hàng"]
    ws.append(headers)
    style_header(ws, len(headers))
    items = [
        ("FMCG-GAO-5KG", "Gạo thơm ST đặc sản 5kg", "GoldRice", "Túi 5kg", 145000, 129000),
        ("FMCG-DAU-1L", "Dầu ăn hướng dương 1L", "SunOil", "Chai 1L", 62000, 56000),
        ("FMCG-SUA-BOX", "Sữa tươi tiệt trùng thùng 48 hộp", "DairyFarm", "Thùng", 480000, 449000),
        ("FMCG-MI-THUNG", "Mì ăn liền thùng 30 gói", "QuickMeal", "Thùng", 165000, 149000),
        ("FMCG-NUOC-RUA", "Nước rửa chén hương chanh 750ml", "CleanHome", "Chai", 39000, 35000),
        ("FMCG-BOT-GIAT", "Bột giặt hương hoa 3kg", "FreshWash", "Túi 3kg", 115000, 99000),
        ("FMCG-CAFE-HOA-TAN", "Cà phê hòa tan 3in1 hộp 20 gói", "MorningCup", "Hộp", 58000, 52000),
        ("FMCG-NUOC-SUOI", "Nước suối đóng chai thùng 24 chai", "AquaPure", "Thùng", 92000, 85000),
        ("FMCG-BANH-QUY", "Bánh quy bơ hộp thiếc 300g", "GoldenCrisp", "Hộp", 79000, 69000),
        ("FMCG-GIAY-VS", "Giấy vệ sinh 2 lớp lốc 10 cuộn", "SoftLeaf", "Lốc", 65000, 59000),
    ]
    for sku, name, brand, unit, price, promo in items:
        ws.append([sku, name, brand, unit, price, promo, 8, "Tiêu dùng nhanh (FMCG)"])
    wb.save(os.path.join(OUT_DIR, "02_bang_gia_sku_tieu_dung_nhanh.xlsx"))


STORES_NORTH = ["GM-HN01 (Cầu Giấy)", "GM-HN02 (Long Biên)", "GM-HP01 (Hải Phòng)", "GM-NB01 (Ninh Bình)"]
STORES_SOUTH = ["GM-HCM01 (Quận 1)", "GM-HCM02 (Thủ Đức)", "GM-CT01 (Cần Thơ)", "GM-BD01 (Bình Dương)"]

SKUS_FOR_STOCK = [
    ("DM-TV-55UHD", "Tivi 55 inch 4K UHD"),
    ("DM-TL-300L", "Tủ lạnh 300L Inverter"),
    ("FMCG-GAO-5KG", "Gạo thơm ST đặc sản 5kg"),
    ("FMCG-SUA-BOX", "Sữa tươi tiệt trùng thùng 48 hộp"),
    ("DM-ĐT-A15", "Điện thoại thông minh dòng A15"),
    ("FMCG-MI-THUNG", "Mì ăn liền thùng 30 gói"),
    ("DM-NCD-1.8L", "Nồi cơm điện tử 1.8L"),
    ("FMCG-NUOC-SUOI", "Nước suối đóng chai thùng 24 chai"),
]


def inventory_sheet(stores, filename, region_label):
    wb = Workbook()
    ws = wb.active
    ws.title = "TonKhoTheoCuaHang"
    headers = ["Mã SKU", "Tên sản phẩm", "Cửa hàng", "Khu vực", "Tồn kho hiện tại", "Tồn kho tối thiểu", "Trạng thái", "Cập nhật lúc"]
    ws.append(headers)
    style_header(ws, len(headers))
    for sku, name in SKUS_FOR_STOCK:
        for store in stores:
            qty = random.randint(0, 120)
            min_qty = random.choice([10, 15, 20, 30])
            status = "Hết hàng" if qty == 0 else ("Sắp hết" if qty < min_qty else "Đủ hàng")
            ws.append([sku, name, store, region_label, qty, min_qty, status, "2026-08-20 08:00"])
    wb.save(os.path.join(OUT_DIR, filename))


def promotion_calendar():
    wb = Workbook()
    ws = wb.active
    ws.title = "LichKhuyenMai_Q3_2026"
    headers = ["Tên chương trình", "Ngành hàng áp dụng", "Ngày bắt đầu", "Ngày kết thúc", "Mức giảm", "Phạm vi áp dụng", "Ghi chú"]
    ws.append(headers)
    style_header(ws, len(headers))
    rows = [
        ("Sale khai trương chi nhánh Thủ Đức", "Toàn ngành hàng", "2026-07-01", "2026-07-07", "Giảm đến 30%", "Chỉ áp dụng cửa hàng GM-HCM02", "Kèm quà tặng khách hàng đầu tiên"),
        ("Tuần lễ điện máy xanh", "Điện máy - Gia dụng", "2026-07-10", "2026-07-20", "Giảm 5-15%", "Toàn hệ thống", "Ưu tiên hàng tồn kho cao"),
        ("Săn sale cuối tuần FMCG", "Tiêu dùng nhanh", "2026-07-25", "2026-07-27", "Giảm 10%", "Toàn hệ thống", "Áp dụng thẻ thành viên"),
        ("Mùa tựu trường - Văn phòng phẩm & Điện tử", "Điện tử, Văn phòng phẩm", "2026-08-15", "2026-08-31", "Giảm đến 20%", "Toàn hệ thống", "Tặng kèm balo cho hóa đơn trên 2 triệu"),
        ("Flash sale giữa tháng 9", "Toàn ngành hàng", "2026-09-15", "2026-09-16", "Giảm sốc 50% (số lượng có hạn)", "Toàn hệ thống + Online", "Giới hạn 2 sản phẩm/khách hàng"),
    ]
    for r in rows:
        ws.append(r)
    wb.save(os.path.join(OUT_DIR, "05_lich_khuyen_mai_q3_2026.xlsx"))


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    sku_price_electronics()
    sku_price_fmcg()
    inventory_sheet(STORES_NORTH, "03_ton_kho_theo_cua_hang_mien_bac.xlsx", "Miền Bắc")
    inventory_sheet(STORES_SOUTH, "04_ton_kho_theo_cua_hang_mien_nam.xlsx", "Miền Nam")
    promotion_calendar()
    print("Done: 5 xlsx files ->", os.path.abspath(OUT_DIR))
