"""Sinh 5 file .docx: chinh sach doi tra, bao hanh, quy trinh khieu nai (du lieu gia lap cho GreenMart Viet Nam)."""
import os
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "docx")
BRAND = "GreenMart Việt Nam"


def base_doc(title, subtitle):
    doc = Document()
    h = doc.add_heading(title, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph(subtitle)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.runs[0].italic = True
    doc.add_paragraph(f"Áp dụng toàn hệ thống cửa hàng {BRAND} — Ban hành: 01/01/2026 — Phiên bản 3.2")
    doc.add_paragraph("")
    return doc


def add_section(doc, heading, paragraphs=None, bullets=None):
    doc.add_heading(heading, level=1)
    for p in (paragraphs or []):
        doc.add_paragraph(p)
    for b in (bullets or []):
        doc.add_paragraph(b, style="List Bullet")


def doc_return_policy():
    doc = base_doc("CHÍNH SÁCH ĐỔI TRẢ HÀNG HÓA", f"{BRAND} — Bộ phận Vận hành Cửa hàng")
    add_section(doc, "1. Mục đích và phạm vi áp dụng", [
        "Chính sách này quy định điều kiện, thời hạn và quy trình đổi trả hàng hóa áp dụng cho toàn bộ hệ thống "
        "cửa hàng và kênh bán hàng trực tuyến của " + BRAND + ". Nhân viên bán hàng và thu ngân phải nắm rõ "
        "chính sách này để tư vấn chính xác cho khách hàng."
    ])
    add_section(doc, "2. Điều kiện đổi trả chung", bullets=[
        "Hàng hóa còn nguyên tem, nhãn mác, bao bì gốc và chưa qua sử dụng.",
        "Có hóa đơn mua hàng hoặc phiếu xuất kho hợp lệ (bản giấy hoặc điện tử tra cứu qua mã hóa đơn).",
        "Thời hạn đổi trả trong vòng 07 ngày kể từ ngày mua đối với hàng tiêu dùng nhanh.",
        "Thời hạn đổi trả trong vòng 30 ngày kể từ ngày mua đối với hàng điện máy, gia dụng.",
        "Không áp dụng đổi trả với hàng thực phẩm tươi sống, hàng khuyến mãi thanh lý (có ghi chú trên tem giá).",
    ])
    add_section(doc, "3. Quy trình xử lý tại quầy", [
        "Bước 1: Nhân viên thu ngân kiểm tra hóa đơn và tình trạng sản phẩm.",
        "Bước 2: Đối chiếu điều kiện đổi trả theo ngành hàng (xem Mục 2 và phụ lục theo ngành hàng).",
        "Bước 3: Lập phiếu đổi trả trên hệ thống POS, chọn lý do đổi trả tương ứng.",
        "Bước 4: Hoàn tiền qua hình thức thanh toán gốc hoặc đổi sản phẩm cùng giá trị.",
        "Bước 5: Lưu phiếu đổi trả và chuyển hàng lỗi (nếu có) về kho theo quy trình QC.",
    ])
    add_section(doc, "4. Trường hợp từ chối đổi trả", bullets=[
        "Sản phẩm đã qua sử dụng, hư hỏng do lỗi người dùng.",
        "Quá thời hạn quy định tại Mục 2.",
        "Không có chứng từ mua hàng hợp lệ và không tra cứu được trên hệ thống.",
    ])
    doc.save(os.path.join(OUT_DIR, "01_chinh_sach_doi_tra_hang_hoa.docx"))


def doc_warranty_electronics():
    doc = base_doc("CHÍNH SÁCH BẢO HÀNH SẢN PHẨM ĐIỆN MÁY", f"{BRAND} — Ngành hàng Điện máy - Gia dụng")
    add_section(doc, "1. Thời hạn bảo hành theo nhóm sản phẩm", bullets=[
        "Tivi, tủ lạnh, máy giặt: 24 tháng kể từ ngày mua (theo phiếu bảo hành của hãng).",
        "Điện thoại, máy tính bảng: 12 tháng, riêng pin và phụ kiện là 06 tháng.",
        "Đồ gia dụng nhỏ (nồi cơm điện, quạt, bàn ủi): 12 tháng.",
        "Linh kiện thay thế chính hãng do kỹ thuật viên lắp đặt: bảo hành 06 tháng cho phần công lắp đặt.",
    ])
    add_section(doc, "2. Điều kiện được bảo hành", bullets=[
        "Còn phiếu bảo hành hoặc hóa đơn điện tử còn hiệu lực, tem bảo hành chưa bị rách/mất.",
        "Lỗi phát sinh do nhà sản xuất, không do tác động ngoại lực hoặc sử dụng sai hướng dẫn.",
        "Sản phẩm chưa từng được sửa chữa tại đơn vị ngoài hệ thống bảo hành ủy quyền.",
    ])
    add_section(doc, "3. Quy trình tiếp nhận bảo hành tại cửa hàng", [
        "Bước 1: Nhân viên kỹ thuật kiểm tra lỗi sơ bộ và xác nhận còn trong thời hạn bảo hành.",
        "Bước 2: Lập phiếu tiếp nhận bảo hành, ghi rõ mô tả lỗi và tình trạng sản phẩm.",
        "Bước 3: Gửi sản phẩm về trung tâm bảo hành ủy quyền hoặc xử lý tại chỗ nếu đủ điều kiện.",
        "Bước 4: Thời gian xử lý tiêu chuẩn: 7-10 ngày làm việc, thông báo khách hàng qua điện thoại/SMS.",
        "Bước 5: Bàn giao sản phẩm đã sửa chữa, khách hàng ký xác nhận vào phiếu bảo hành.",
    ])
    add_section(doc, "4. Trường hợp không được bảo hành", bullets=[
        "Sản phẩm bị vào nước, cháy nổ do sử dụng sai điện áp.",
        "Tem bảo hành bị rách, tẩy xóa hoặc số serial không khớp hệ thống.",
        "Hư hỏng do thiên tai, hỏa hoạn hoặc tác động cố ý.",
    ])
    doc.save(os.path.join(OUT_DIR, "02_chinh_sach_bao_hanh_dien_may.docx"))


def doc_complaint_process():
    doc = base_doc("QUY TRÌNH TIẾP NHẬN VÀ XỬ LÝ KHIẾU NẠI KHÁCH HÀNG", f"{BRAND} — Phòng Chăm sóc Khách hàng")
    add_section(doc, "1. Các kênh tiếp nhận khiếu nại", bullets=[
        "Trực tiếp tại quầy Chăm sóc khách hàng của cửa hàng.",
        "Tổng đài chăm sóc khách hàng (hotline nội bộ).",
        "Email chăm sóc khách hàng và biểu mẫu trên website.",
        "Fanpage mạng xã hội chính thức của hệ thống.",
    ])
    add_section(doc, "2. Phân loại mức độ khiếu nại", bullets=[
        "Mức 1 - Đơn giản: xử lý ngay tại quầy trong vòng 30 phút (VD: sai giá niêm yết, thiếu hàng khuyến mãi).",
        "Mức 2 - Trung bình: cần xác minh với kho/nhà cung cấp, xử lý trong 24-48 giờ.",
        "Mức 3 - Nghiêm trọng: liên quan an toàn thực phẩm, chất lượng sản phẩm, chuyển Ban Giám đốc xử lý trong 72 giờ.",
    ])
    add_section(doc, "3. Các bước xử lý chuẩn", [
        "Bước 1: Tiếp nhận và ghi nhận đầy đủ thông tin khách hàng, nội dung khiếu nại vào hệ thống CRM.",
        "Bước 2: Xin lỗi khách hàng và cam kết thời gian phản hồi cụ thể theo mức độ phân loại.",
        "Bước 3: Xác minh thông tin với bộ phận liên quan (kho, ngành hàng, kế toán).",
        "Bước 4: Đề xuất phương án xử lý (hoàn tiền, đổi hàng, tặng voucher, xin lỗi bằng văn bản).",
        "Bước 5: Phản hồi kết quả cho khách hàng và đóng hồ sơ khiếu nại trên hệ thống.",
    ])
    add_section(doc, "4. Nguyên tắc ứng xử với khách hàng", bullets=[
        "Luôn lắng nghe, không tranh cãi hoặc đổ lỗi cho khách hàng.",
        "Không tự ý hứa hẹn vượt thẩm quyền (mức bồi thường, thời gian xử lý).",
        "Báo cáo ngay cho quản lý ca trực đối với khiếu nại Mức 2, Mức 3.",
    ])
    doc.save(os.path.join(OUT_DIR, "03_quy_trinh_xu_ly_khieu_nai.docx"))


def doc_return_by_category():
    doc = base_doc("PHỤ LỤC ĐỔI TRẢ THEO NGÀNH HÀNG", f"{BRAND} — Áp dụng kèm Chính sách đổi trả hàng hóa")
    add_section(doc, "1. Ngành hàng Thực phẩm", bullets=[
        "Thực phẩm khô, đóng gói: đổi trả trong 03 ngày nếu còn hạn sử dụng và bao bì nguyên vẹn.",
        "Thực phẩm tươi sống: chỉ đổi trả ngay tại quầy khi phát hiện lỗi, không hỗ trợ trả sau khi rời cửa hàng.",
    ])
    add_section(doc, "2. Ngành hàng Mỹ phẩm - Chăm sóc cá nhân", bullets=[
        "Đổi trả trong 07 ngày nếu chưa mở seal, còn nguyên hộp.",
        "Không áp dụng đổi trả với mỹ phẩm đã bóc tem vì lý do vệ sinh, trừ trường hợp lỗi nhà sản xuất.",
    ])
    add_section(doc, "3. Ngành hàng Điện tử - Điện máy", bullets=[
        "Đổi mới trong 07 ngày đầu nếu lỗi do nhà sản xuất (áp dụng '7 ngày đổi mới' song song với bảo hành).",
        "Sau 07 ngày, sản phẩm lỗi được xử lý theo Chính sách bảo hành sản phẩm điện máy.",
    ])
    add_section(doc, "4. Ngành hàng Thời trang - Gia dụng mềm", bullets=[
        "Đổi size/mẫu trong 15 ngày, còn tem mác, chưa giặt/sử dụng.",
        "Không hỗ trợ hoàn tiền, chỉ hỗ trợ đổi sang sản phẩm khác cùng hoặc chênh lệch giá trị.",
    ])
    doc.save(os.path.join(OUT_DIR, "04_phu_luc_doi_tra_theo_nganh_hang.docx"))


def doc_hotline_complaint():
    doc = base_doc("HƯỚNG DẪN XỬ LÝ KHIẾU NẠI QUA TỔNG ĐÀI / EMAIL", f"{BRAND} — Trung tâm Chăm sóc Khách hàng")
    add_section(doc, "1. Kịch bản tiếp nhận cuộc gọi", [
        "Chào khách hàng, xác nhận họ tên và mã cửa hàng/mã hóa đơn liên quan (nếu có).",
        "Lắng nghe toàn bộ nội dung trước khi phản hồi, không ngắt lời khách hàng.",
        "Tóm tắt lại vấn đề để xác nhận đã hiểu đúng trước khi đề xuất hướng xử lý.",
    ])
    add_section(doc, "2. Chuẩn phản hồi email/website", bullets=[
        "Phản hồi xác nhận đã tiếp nhận trong vòng 02 giờ làm việc.",
        "Cung cấp mã ticket để khách hàng theo dõi tiến độ xử lý.",
        "Đính kèm chính sách liên quan (đổi trả/bảo hành) nếu cần thiết để khách hàng tham khảo.",
    ])
    add_section(doc, "3. Các câu hỏi thường gặp (FAQ) tổng đài viên cần nắm", bullets=[
        "Hỏi về tình trạng đơn hàng/giao hàng chậm.",
        "Hỏi về chính sách đổi trả, bảo hành sản phẩm cụ thể.",
        "Hỏi về chương trình khuyến mãi đang áp dụng tại cửa hàng gần nhất.",
        "Khiếu nại về thái độ phục vụ của nhân viên tại cửa hàng.",
    ])
    add_section(doc, "4. Chuyển cấp xử lý (escalation)", [
        "Nếu khách hàng không hài lòng với phương án đề xuất, chuyển ngay cho Trưởng nhóm CSKH.",
        "Khiếu nại liên quan an toàn thực phẩm phải escalate ngay lập tức, không tự xử lý ở cấp tổng đài viên.",
    ])
    doc.save(os.path.join(OUT_DIR, "05_huong_dan_khieu_nai_tong_dai.docx"))


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    doc_return_policy()
    doc_warranty_electronics()
    doc_complaint_process()
    doc_return_by_category()
    doc_hotline_complaint()
    print("Done: 5 docx files ->", os.path.abspath(OUT_DIR))
