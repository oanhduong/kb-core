"""Sinh 5 file .docx cho test corpus ingest ngan hang retail Viet Nam.

Yeu cau: heading nhieu cap (CHUONG > Dieu N), noi dung tieng Viet thuc te,
moi file 3-5 trang. Tra ve dict metadata (dung cho gen_ground_truth.py):
{filename: {"title": str, "characters": int, "article_count": int}}
"""
import os
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "docx")
BRAND = "GreenMart Việt Nam"
ROMAN = ["I", "II", "III", "IV", "V", "VI"]


def _base_doc(title, subtitle):
    doc = Document()
    h = doc.add_heading(title, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph(subtitle)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.runs[0].italic = True
    meta = doc.add_paragraph(f"Áp dụng toàn hệ thống cửa hàng {BRAND} — Ban hành: 01/01/2026 — Phiên bản 3.2")
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("")
    return doc


def _build(filename, title, subtitle, chapters):
    """chapters: list of (chapter_title, articles)
    articles: list of (article_title, paragraph_text, khoan_list)
    """
    doc = _base_doc(title, subtitle)
    char_count = len(title) + len(subtitle)
    article_num = 0
    for idx, (chapter_title, articles) in enumerate(chapters):
        chapter_head = f"CHƯƠNG {ROMAN[idx]}. {chapter_title}"
        doc.add_heading(chapter_head, level=1)
        char_count += len(chapter_head)
        for art_title, paragraph_text, khoan_list in articles:
            article_num += 1
            head = f"Điều {article_num}. {art_title}"
            doc.add_heading(head, level=2)
            char_count += len(head)
            p = doc.add_paragraph(paragraph_text)
            char_count += len(paragraph_text)
            for i, khoan in enumerate(khoan_list, start=1):
                kp = doc.add_paragraph(f"{i}. {khoan}")
                kp.paragraph_format.left_indent = Inches(0.35)
                char_count += len(khoan) + 3
    doc.save(os.path.join(OUT_DIR, filename))
    return {"title": title, "characters": char_count, "article_count": article_num}


# =====================================================================================
# 1) CHINH SACH DOI TRA HANG HOA
# =====================================================================================
def doc_return_policy():
    chapters = [
        ("QUY ĐỊNH CHUNG", [
            ("Mục đích ban hành",
             "Chính sách này quy định thống nhất về điều kiện, thời hạn và quy trình đổi trả hàng hóa nhằm bảo vệ "
             "quyền lợi chính đáng của khách hàng, đồng thời giúp nhân viên tại cửa hàng có căn cứ rõ ràng để tư "
             "vấn và xử lý các yêu cầu đổi trả một cách nhất quán trên toàn hệ thống. Việc ban hành chính sách "
             "cũng nhằm hạn chế tình trạng lạm dụng chính sách đổi trả gây thiệt hại cho doanh nghiệp.",
             ["Bảo đảm quyền lợi hợp pháp của khách hàng khi mua sắm tại hệ thống.",
              "Thống nhất cách xử lý đổi trả giữa các cửa hàng, tránh mỗi nơi áp dụng một kiểu.",
              "Làm căn cứ đào tạo nhân viên mới và giải quyết tranh chấp phát sinh."]),
            ("Phạm vi và đối tượng áp dụng",
             "Chính sách áp dụng cho tất cả giao dịch mua hàng tại quầy và trên kênh thương mại điện tử của hệ "
             "thống, không phân biệt hình thức thanh toán. Đối tượng áp dụng bao gồm toàn bộ nhân viên bán hàng, "
             "thu ngân, quản lý cửa hàng và bộ phận chăm sóc khách hàng.",
             ["Áp dụng cho khách hàng cá nhân và khách hàng doanh nghiệp mua với số lượng lớn.",
              "Không áp dụng cho hàng hóa mua tại các điểm bán hàng flash sale lưu động, trừ khi có thông báo riêng."]),
            ("Giải thích thuật ngữ",
             "Trong chính sách này, \"đổi hàng\" được hiểu là việc thay thế sản phẩm đã mua bằng một sản phẩm khác "
             "cùng hoặc khác giá trị; \"trả hàng\" là việc khách hàng hoàn lại sản phẩm và nhận lại tiền theo hình "
             "thức thanh toán ban đầu; \"hàng lỗi nhà sản xuất\" là lỗi phát sinh không do tác động của người sử dụng.",
             []),
        ]),
        ("ĐIỀU KIỆN VÀ THỜI HẠN ĐỔI TRẢ", [
            ("Điều kiện đổi trả chung",
             "Để được chấp nhận đổi trả, sản phẩm phải còn nguyên tem, nhãn mác, bao bì gốc và chưa qua sử dụng; "
             "khách hàng phải xuất trình hóa đơn mua hàng hợp lệ hoặc mã đơn hàng để nhân viên tra cứu trên hệ "
             "thống POS. Trường hợp mất hóa đơn giấy, nhân viên có thể tra cứu bằng số điện thoại đăng ký thành "
             "viên tại thời điểm mua hàng.",
             ["Sản phẩm còn nguyên vẹn, không trầy xước, không có mùi lạ hoặc dấu hiệu đã qua sử dụng.",
              "Có chứng từ mua hàng hợp lệ (hóa đơn giấy, hóa đơn điện tử hoặc tra cứu được trên hệ thống).",
              "Yêu cầu đổi trả được thực hiện trong thời hạn quy định tại Điều 5."]),
            ("Thời hạn đổi trả theo ngành hàng",
             "Thời hạn đổi trả được quy định khác nhau theo từng ngành hàng nhằm phù hợp với đặc tính sản phẩm. "
             "Ngành hàng tiêu dùng nhanh có thời hạn ngắn hơn do đặc thù dễ hư hỏng, trong khi ngành hàng điện "
             "máy có thời hạn dài hơn để phù hợp với chu kỳ sử dụng và phát hiện lỗi.",
             ["Hàng tiêu dùng nhanh, thực phẩm đóng gói: trong vòng 07 ngày kể từ ngày mua.",
              "Hàng điện máy, gia dụng: trong vòng 30 ngày kể từ ngày mua.",
              "Hàng thời trang, phụ kiện: trong vòng 15 ngày kể từ ngày mua."]),
            ("Các trường hợp không áp dụng đổi trả",
             "Một số trường hợp sẽ không được chấp nhận đổi trả nhằm tránh lạm dụng chính sách và bảo đảm vệ sinh "
             "an toàn cho các sản phẩm đặc thù. Nhân viên cần giải thích rõ lý do từ chối cho khách hàng một cách "
             "lịch sự và có thể đề xuất phương án khác nếu phù hợp.",
             ["Hàng thực phẩm tươi sống, hàng đông lạnh đã rã đông.",
              "Hàng khuyến mãi thanh lý có ghi chú \"không đổi trả\" trên tem giá.",
              "Sản phẩm đã qua sử dụng, hư hỏng do lỗi của người dùng hoặc quá thời hạn quy định."]),
        ]),
        ("QUY TRÌNH THỰC HIỆN", [
            ("Quy trình tiếp nhận yêu cầu đổi trả tại quầy",
             "Khi khách hàng mang sản phẩm đến quầy yêu cầu đổi trả, nhân viên thu ngân thực hiện tuần tự các "
             "bước kiểm tra và xử lý trên hệ thống POS, bảo đảm mọi giao dịch đổi trả đều được ghi nhận đầy đủ để "
             "phục vụ đối soát kho và kế toán cuối ngày.",
             ["Kiểm tra tình trạng sản phẩm và đối chiếu hóa đơn hoặc mã đơn hàng.",
              "Xác nhận sản phẩm còn trong thời hạn và đủ điều kiện đổi trả theo Điều 4 và Điều 5.",
              "Lập phiếu đổi trả trên hệ thống, chọn lý do đổi trả tương ứng và in phiếu xác nhận.",
              "Hoàn tiền theo hình thức thanh toán gốc hoặc thực hiện đổi sang sản phẩm khác."]),
            ("Quy trình đổi trả đối với đơn hàng online",
             "Đối với đơn hàng đặt qua kênh thương mại điện tử, khách hàng gửi yêu cầu đổi trả qua ứng dụng hoặc "
             "hotline chăm sóc khách hàng kèm hình ảnh sản phẩm. Bộ phận CSKH xác nhận đủ điều kiện trước khi "
             "điều phối đơn vị vận chuyển đến lấy hàng.",
             ["Khách hàng gửi yêu cầu kèm ảnh chụp sản phẩm và mã đơn hàng qua ứng dụng hoặc hotline.",
              "Bộ phận CSKH xác nhận trong vòng 24 giờ làm việc và cấp mã lấy hàng hoàn trả.",
              "Hoàn tiền trong vòng 3-5 ngày làm việc sau khi kho xác nhận đã nhận lại hàng."]),
            ("Xử lý hàng hóa sau khi thu hồi",
             "Hàng hóa sau khi được đổi trả sẽ được phân loại tại kho: hàng còn nguyên vẹn được nhập lại kho bán, "
             "hàng lỗi được chuyển về nhà cung cấp theo điều khoản hợp đồng, hàng không thể bán lại được xử lý "
             "thanh lý hoặc tiêu hủy theo quy định nội bộ.",
             []),
        ]),
        ("TÌNH HUỐNG THAM KHẢO", [
            ("Ví dụ xử lý đổi trả hàng điện máy",
             "Khách hàng mua một chiếc nồi chiên không dầu tại chi nhánh Cầu Giấy ngày 05/08/2026, đến ngày "
             "20/08/2026 phát hiện mâm chiên bị nứt trong quá trình sử dụng bình thường, không có dấu hiệu va đập. "
             "Khách hàng mang sản phẩm cùng hóa đơn đến quầy yêu cầu đổi mới. Nhân viên kiểm tra thấy sản phẩm còn "
             "trong thời hạn 30 ngày theo Điều 5, vết nứt không do tác động ngoại lực nên được xác định là lỗi "
             "nhà sản xuất. Nhân viên lập phiếu đổi trả, đổi sang sản phẩm cùng model còn hàng trong kho và cập "
             "nhật lý do \"lỗi nhà sản xuất - phát hiện trong quá trình sử dụng thông thường\" trên hệ thống.",
             []),
            ("Ví dụ từ chối đổi trả đúng quy định",
             "Một khách hàng mang một hộp mỹ phẩm đã bóc seal, sử dụng khoảng một nửa, đến quầy yêu cầu trả hàng "
             "vì \"không hợp da\", quá 10 ngày kể từ ngày mua và không thuộc trường hợp lỗi nhà sản xuất. Theo Điều "
             "6, sản phẩm mỹ phẩm đã bóc tem vì lý do vệ sinh không thuộc diện được đổi trả trừ khi lỗi do nhà sản "
             "xuất. Nhân viên giải thích rõ căn cứ chính sách, xin lỗi vì sự bất tiện và đề xuất khách hàng tham "
             "khảo dòng sản phẩm khác phù hợp hơn với loại da, đồng thời ghi nhận phản hồi để báo cáo ngành hàng "
             "xem xét bổ sung thông tin tư vấn khi bán sản phẩm mỹ phẩm mới.",
             []),
        ]),
        ("TRÁCH NHIỆM VÀ ĐIỀU KHOẢN THI HÀNH", [
            ("Trách nhiệm của nhân viên cửa hàng",
             "Nhân viên trực tiếp xử lý đổi trả có trách nhiệm nắm vững chính sách, kiểm tra đúng điều kiện trước "
             "khi ra quyết định, và không được tự ý áp dụng ngoại lệ ngoài thẩm quyền được giao.",
             []),
            ("Trách nhiệm của quản lý ca trực",
             "Quản lý ca trực chịu trách nhiệm phê duyệt các trường hợp đổi trả ngoại lệ, giám sát việc tuân thủ "
             "chính sách và tổng hợp báo cáo đổi trả hằng ngày gửi về bộ phận vận hành.",
             []),
            ("Điều khoản thi hành",
             "Chính sách này có hiệu lực kể từ ngày ban hành và thay thế các quy định đổi trả trước đó. Mọi sửa "
             "đổi, bổ sung phải được Ban Giám đốc phê duyệt bằng văn bản và thông báo đến toàn hệ thống trước khi "
             "áp dụng.",
             []),
        ]),
        ("PHỤ LỤC - CÂU HỎI THƯỜNG GẶP", [
            ("Khách hàng mất hóa đơn giấy có được đổi trả không?",
             "Có. Nhân viên có thể tra cứu giao dịch trên hệ thống POS bằng số điện thoại đăng ký thành viên, mã "
             "đơn hàng hoặc thời gian và địa điểm mua ước tính. Nếu tra cứu được đầy đủ thông tin khớp với sản "
             "phẩm khách hàng mang đến, yêu cầu đổi trả vẫn được xử lý bình thường theo Điều 4.",
             []),
            ("Hàng mua trong chương trình flash sale có được đổi trả như bình thường không?",
             "Tùy chương trình. Nếu tem giá hoặc thông báo chương trình có ghi rõ \"không đổi trả\" thì áp dụng "
             "theo Điều 6. Nếu chương trình không có ghi chú đặc biệt, sản phẩm vẫn được áp dụng thời hạn và điều "
             "kiện đổi trả thông thường theo ngành hàng quy định tại Điều 5.",
             []),
            ("Có thể đổi trả tại cửa hàng khác với nơi đã mua không?",
             "Có, do hệ thống dùng chung dữ liệu tồn kho và hóa đơn trên toàn quốc. Nhân viên tại cửa hàng tiếp "
             "nhận tra cứu giao dịch gốc trên hệ thống trung tâm, kiểm tra điều kiện như quy trình thông thường "
             "tại Điều 7 trước khi xử lý.",
             []),
            ("Nhân viên có được tự quyết định ngoại lệ ngoài chính sách không?",
             "Không. Theo Điều 10, nhân viên chỉ được xử lý trong đúng thẩm quyền và điều kiện đã quy định. Mọi "
             "trường hợp ngoại lệ phải được quản lý ca trực xem xét, phê duyệt theo Điều 11 trước khi thực hiện.",
             []),
        ]),
    ]
    return _build("01_chinh_sach_doi_tra_hang_hoa.docx", "CHÍNH SÁCH ĐỔI TRẢ HÀNG HÓA",
                   f"{BRAND} — Bộ phận Vận hành Cửa hàng", chapters)


# =====================================================================================
# 2) CHINH SACH BAO HANH SAN PHAM
# =====================================================================================
def doc_warranty_policy():
    chapters = [
        ("QUY ĐỊNH CHUNG", [
            ("Mục đích",
             "Chính sách bảo hành được ban hành nhằm cam kết chất lượng sản phẩm với khách hàng, quy định rõ "
             "trách nhiệm bảo hành giữa cửa hàng, hãng sản xuất và trung tâm bảo hành ủy quyền, tránh gây nhầm "
             "lẫn hoặc kéo dài thời gian xử lý cho khách hàng.",
             []),
            ("Phạm vi áp dụng",
             "Chính sách áp dụng cho toàn bộ sản phẩm điện máy, điện tử, gia dụng có phiếu bảo hành hoặc hóa đơn "
             "điện tử hợp lệ, bán ra từ hệ thống cửa hàng hoặc kênh trực tuyến chính thức.",
             ["Áp dụng cho sản phẩm mới 100%, chưa qua sửa chữa ngoài hệ thống ủy quyền.",
              "Không áp dụng cho sản phẩm trưng bày, hàng thanh lý đã ghi rõ \"không bảo hành\"."]),
            ("Giải thích thuật ngữ",
             "\"Bảo hành tại chỗ\" là hình thức sửa chữa ngay tại cửa hàng đối với lỗi đơn giản; \"bảo hành hãng\" "
             "là hình thức gửi sản phẩm về trung tâm bảo hành ủy quyền của nhà sản xuất; \"lỗi kỹ thuật\" là hư "
             "hỏng phát sinh trong điều kiện sử dụng bình thường, không do tác động bên ngoài.",
             []),
        ]),
        ("THỜI HẠN VÀ ĐIỀU KIỆN BẢO HÀNH", [
            ("Thời hạn bảo hành theo nhóm sản phẩm",
             "Thời hạn bảo hành được quy định riêng cho từng nhóm sản phẩm căn cứ theo chính sách của nhà sản "
             "xuất và cam kết bổ sung của hệ thống, áp dụng kể từ ngày ghi trên hóa đơn mua hàng.",
             ["Tivi, tủ lạnh, máy giặt: 24 tháng.",
              "Điện thoại, máy tính bảng: 12 tháng, riêng pin và phụ kiện đi kèm là 06 tháng.",
              "Đồ gia dụng nhỏ (nồi cơm điện, quạt, bàn ủi...): 12 tháng.",
              "Công lắp đặt do kỹ thuật viên hệ thống thực hiện: 06 tháng."]),
            ("Điều kiện được bảo hành",
             "Sản phẩm chỉ được tiếp nhận bảo hành khi còn phiếu bảo hành hoặc hóa đơn điện tử hợp lệ, tem bảo "
             "hành nguyên vẹn, và lỗi phát sinh không thuộc các trường hợp loại trừ quy định tại Điều 6.",
             ["Còn phiếu bảo hành hoặc tra cứu được hóa đơn điện tử trên hệ thống.",
              "Số serial trên sản phẩm khớp với thông tin trên phiếu bảo hành.",
              "Sản phẩm chưa từng được can thiệp sửa chữa bởi đơn vị ngoài hệ thống ủy quyền."]),
            ("Trường hợp từ chối bảo hành",
             "Cửa hàng có quyền từ chối tiếp nhận bảo hành đối với các trường hợp hư hỏng do nguyên nhân khách "
             "quan ngoài lỗi nhà sản xuất, nhằm bảo đảm công bằng cho các khách hàng khác sử dụng đúng cách.",
             ["Sản phẩm bị vào nước, cháy nổ do sử dụng sai điện áp hoặc nguồn điện không ổn định.",
              "Tem bảo hành bị rách, tẩy xóa hoặc số serial không khớp hệ thống.",
              "Hư hỏng do thiên tai, hỏa hoạn, va đập mạnh hoặc tác động cố ý."]),
        ]),
        ("QUY TRÌNH TIẾP NHẬN VÀ XỬ LÝ", [
            ("Quy trình tiếp nhận tại cửa hàng",
             "Nhân viên kỹ thuật tại cửa hàng kiểm tra sơ bộ lỗi sản phẩm, xác nhận còn trong thời hạn bảo hành "
             "và lập phiếu tiếp nhận ghi rõ mô tả tình trạng sản phẩm tại thời điểm khách hàng mang đến.",
             ["Kiểm tra lỗi sơ bộ và đối chiếu điều kiện bảo hành.",
              "Lập phiếu tiếp nhận, ghi nhận đầy đủ mô tả lỗi và tình trạng ngoại quan sản phẩm.",
              "Thông báo cho khách hàng thời gian dự kiến hoàn trả sản phẩm."]),
            ("Quy trình gửi bảo hành hãng hoặc trung tâm ủy quyền",
             "Đối với các lỗi vượt quá khả năng xử lý tại chỗ, sản phẩm được đóng gói và gửi về trung tâm bảo "
             "hành ủy quyền kèm phiếu tiếp nhận, đồng thời cập nhật trạng thái trên hệ thống để khách hàng có "
             "thể tra cứu tiến độ.",
             []),
            ("Thời gian xử lý và thông báo khách hàng",
             "Thời gian xử lý tiêu chuẩn là 7-10 ngày làm việc kể từ ngày tiếp nhận. Cửa hàng có trách nhiệm chủ "
             "động thông báo cho khách hàng qua điện thoại hoặc tin nhắn khi sản phẩm được sửa chữa xong hoặc khi "
             "phát sinh chậm trễ ngoài dự kiến.",
             []),
        ]),
        ("TÌNH HUỐNG THAM KHẢO", [
            ("Ví dụ xử lý bảo hành tại chỗ",
             "Khách hàng mang một chiếc quạt đứng mua được 4 tháng đến quầy vì quạt không lên nguồn. Kỹ thuật viên "
             "kiểm tra sơ bộ, xác định lỗi do dây nguồn bị đứt ngầm, không liên quan đến bo mạch. Đây là lỗi đơn "
             "giản, kỹ thuật viên thay dây nguồn chính hãng ngay tại quầy trong 20 phút, không cần gửi về trung "
             "tâm bảo hành, và cập nhật vào hệ thống là \"bảo hành tại chỗ\" theo định nghĩa tại Điều 3.",
             []),
            ("Ví dụ từ chối bảo hành do tem bị rách",
             "Một khách hàng mang tủ lạnh đến yêu cầu bảo hành nhưng tem bảo hành trên sản phẩm đã bị rách một "
             "phần và số serial không đọc được rõ ràng, đồng thời khách hàng cho biết đã từng nhờ thợ điện lạnh "
             "bên ngoài kiểm tra trước đó. Theo Điều 5 và Điều 6, trường hợp này không đủ điều kiện bảo hành do "
             "sản phẩm đã bị can thiệp bởi đơn vị ngoài hệ thống ủy quyền. Nhân viên giải thích rõ với khách hàng "
             "và tư vấn dịch vụ sửa chữa có tính phí nếu khách hàng đồng ý.",
             []),
        ]),
        ("ĐIỀU KHOẢN THI HÀNH", [
            ("Trách nhiệm nhân viên kỹ thuật",
             "Nhân viên kỹ thuật chịu trách nhiệm đánh giá lỗi chính xác, trung thực trong việc xác định nguyên "
             "nhân hư hỏng và không được từ chối bảo hành trái với quy định tại chính sách này.",
             []),
            ("Xử lý khiếu nại liên quan bảo hành",
             "Trường hợp khách hàng không đồng ý với kết luận về tình trạng bảo hành, yêu cầu sẽ được chuyển lên "
             "quản lý cửa hàng hoặc bộ phận kỹ thuật khu vực để thẩm định lại theo Quy trình xử lý khiếu nại "
             "khách hàng.",
             []),
            ("Hiệu lực thi hành",
             "Chính sách có hiệu lực từ ngày ban hành, áp dụng thống nhất trên toàn hệ thống và được rà soát "
             "định kỳ hằng năm để cập nhật theo chính sách mới nhất của các nhà sản xuất.",
             []),
        ]),
        ("PHỤ LỤC - CÂU HỎI THƯỜNG GẶP", [
            ("Sản phẩm mua online có được bảo hành như mua tại cửa hàng không?",
             "Có. Hóa đơn điện tử của đơn hàng online có giá trị tương đương hóa đơn giấy khi tra cứu trên hệ "
             "thống, thời hạn và điều kiện bảo hành áp dụng như nhau theo Điều 4 và Điều 5, không phân biệt kênh "
             "mua hàng.",
             []),
            ("Sản phẩm hết hạn bảo hành nhưng lỗi rất nhỏ thì xử lý thế nào?",
             "Sản phẩm ngoài thời hạn bảo hành không thuộc phạm vi chính sách này. Nhân viên có thể giới thiệu "
             "dịch vụ sửa chữa có tính phí tại trung tâm bảo hành ủy quyền, báo giá cụ thể trước khi khách hàng "
             "quyết định.",
             []),
            ("Ai chịu chi phí vận chuyển khi gửi sản phẩm về trung tâm bảo hành hãng?",
             "Đối với lỗi thuộc diện bảo hành theo Điều 5, hệ thống chịu chi phí vận chuyển hai chiều giữa cửa "
             "hàng và trung tâm bảo hành ủy quyền theo Điều 8, khách hàng không phải trả thêm bất kỳ khoản phí "
             "vận chuyển nào.",
             []),
            ("Nếu quá thời gian xử lý tiêu chuẩn 7-10 ngày mà chưa có kết quả thì sao?",
             "Nhân viên phụ trách phải chủ động thông báo cho khách hàng lý do chậm trễ theo Điều 9 và cập nhật "
             "thời gian dự kiến mới, không được để khách hàng tự liên hệ hỏi thăm mà không có phản hồi.",
             []),
        ]),
    ]
    return _build("02_chinh_sach_bao_hanh_san_pham.docx", "CHÍNH SÁCH BẢO HÀNH SẢN PHẨM",
                   f"{BRAND} — Ngành hàng Điện máy - Điện tử - Gia dụng", chapters)


# =====================================================================================
# 3) QUY TRINH XU LY KHIEU NAI KHACH HANG
# =====================================================================================
def doc_complaint_process():
    chapters = [
        ("QUY ĐỊNH CHUNG", [
            ("Mục đích",
             "Quy trình này thống nhất cách tiếp nhận, phân loại và xử lý khiếu nại của khách hàng nhằm bảo đảm "
             "mọi phản ánh đều được ghi nhận, xử lý kịp thời và phản hồi đầy đủ, góp phần nâng cao trải nghiệm "
             "và giữ chân khách hàng.",
             []),
            ("Phạm vi áp dụng",
             "Áp dụng cho toàn bộ khiếu nại liên quan đến sản phẩm, dịch vụ, thái độ nhân viên hoặc quy trình bán "
             "hàng phát sinh tại cửa hàng hoặc qua các kênh trực tuyến của hệ thống.",
             []),
            ("Các kênh tiếp nhận khiếu nại",
             "Khách hàng có thể gửi khiếu nại qua nhiều kênh khác nhau tùy theo mức độ thuận tiện, mọi khiếu nại "
             "dù qua kênh nào cũng phải được ghi nhận vào hệ thống CRM trong ngày làm việc.",
             ["Trực tiếp tại quầy Chăm sóc khách hàng của cửa hàng.",
              "Tổng đài chăm sóc khách hàng (hotline nội bộ).",
              "Email chăm sóc khách hàng và biểu mẫu trên website.",
              "Fanpage mạng xã hội chính thức của hệ thống."]),
        ]),
        ("PHÂN LOẠI VÀ THẨM QUYỀN XỬ LÝ", [
            ("Phân loại mức độ khiếu nại",
             "Khiếu nại được phân thành ba mức độ dựa trên tính chất và mức độ ảnh hưởng, làm căn cứ xác định "
             "thời hạn xử lý và cấp thẩm quyền phê duyệt phương án giải quyết.",
             ["Mức 1 - Đơn giản: sai giá niêm yết, thiếu hàng khuyến mãi — xử lý ngay tại quầy trong 30 phút.",
              "Mức 2 - Trung bình: cần xác minh với kho hoặc nhà cung cấp — xử lý trong 24-48 giờ.",
              "Mức 3 - Nghiêm trọng: liên quan an toàn thực phẩm, chất lượng sản phẩm — xử lý trong 72 giờ."]),
            ("Thẩm quyền xử lý theo cấp bậc",
             "Nhân viên tại quầy chỉ được xử lý khiếu nại Mức 1 trong hạn mức bồi thường quy định; khiếu nại Mức "
             "2 do quản lý ca trực phê duyệt; khiếu nại Mức 3 bắt buộc phải báo cáo Ban Giám đốc trước khi phản "
             "hồi khách hàng.",
             []),
            ("Thời hạn phản hồi",
             "Thời hạn phản hồi ban đầu cho khách hàng không được vượt quá 02 giờ làm việc kể từ khi tiếp nhận, "
             "kể cả trong trường hợp chưa có phương án xử lý cuối cùng, nhằm bảo đảm khách hàng luôn được cập "
             "nhật tiến độ.",
             []),
        ]),
        ("QUY TRÌNH XỬ LÝ", [
            ("Các bước xử lý chuẩn",
             "Quy trình xử lý khiếu nại được thực hiện theo trình tự thống nhất nhằm bảo đảm không bỏ sót thông "
             "tin và có hồ sơ đầy đủ phục vụ tra cứu về sau.",
             ["Tiếp nhận và ghi nhận đầy đủ thông tin khách hàng, nội dung khiếu nại vào hệ thống CRM.",
              "Xin lỗi khách hàng và cam kết thời gian phản hồi cụ thể theo mức độ phân loại.",
              "Xác minh thông tin với bộ phận liên quan (kho, ngành hàng, kế toán).",
              "Đề xuất phương án xử lý và phản hồi kết quả, sau đó đóng hồ sơ trên hệ thống."]),
            ("Nguyên tắc ứng xử với khách hàng",
             "Nhân viên tiếp nhận khiếu nại phải luôn giữ thái độ lắng nghe, không tranh cãi hoặc đổ lỗi cho "
             "khách hàng, và không được tự ý hứa hẹn vượt quá thẩm quyền được giao.",
             []),
            ("Escalation - chuyển cấp xử lý",
             "Khi khách hàng không hài lòng với phương án đề xuất ở cấp xử lý hiện tại, hoặc khiếu nại thuộc Mức "
             "2, Mức 3, nhân viên phải chuyển ngay cho cấp trên trực tiếp và không được tự xử lý ngoài thẩm "
             "quyền.",
             []),
        ]),
        ("TÌNH HUỐNG THAM KHẢO", [
            ("Ví dụ khiếu nại Mức 1 xử lý tại quầy",
             "Khách hàng phát hiện giá thanh toán tại quầy cao hơn giá niêm yết trên kệ đối với một sản phẩm sữa "
             "tắm đang khuyến mãi. Nhân viên thu ngân kiểm tra thấy tem giá khuyến mãi trên kệ chưa được gỡ dù "
             "chương trình đã kết thúc từ hôm trước. Đây là khiếu nại Mức 1 theo Điều 4, nhân viên xin lỗi khách "
             "hàng, điều chỉnh giá theo đúng mức khuyến mãi trên kệ ngay tại quầy và báo bộ phận trưng bày gỡ tem "
             "giá cũ trong ca làm việc.",
             []),
            ("Ví dụ khiếu nại Mức 3 phải escalate ngay",
             "Một khách hàng phản ánh phát hiện dị vật trong gói thực phẩm đóng gói vừa mua. Đây là khiếu nại "
             "liên quan an toàn thực phẩm, được phân loại Mức 3 theo Điều 4. Nhân viên tại quầy không tự xử lý mà "
             "lập tức báo cáo quản lý cửa hàng, thu hồi sản phẩm cùng lô để kiểm tra, đồng thời chuyển thông tin "
             "cho Ban Giám đốc và bộ phận an toàn thực phẩm xử lý trong vòng 72 giờ theo đúng thẩm quyền quy định "
             "tại Điều 5.",
             []),
        ]),
        ("GIÁM SÁT VÀ ĐIỀU KHOẢN THI HÀNH", [
            ("Theo dõi và báo cáo khiếu nại",
             "Bộ phận Chăm sóc khách hàng tổng hợp báo cáo khiếu nại hằng tuần, phân tích nguyên nhân lặp lại để "
             "đề xuất cải tiến quy trình hoặc sản phẩm, gửi báo cáo về Ban Giám đốc mỗi tháng.",
             []),
            ("Xử lý vi phạm quy trình",
             "Nhân viên không tuân thủ quy trình, gây chậm trễ hoặc thái độ không đúng mực khi xử lý khiếu nại sẽ "
             "bị nhắc nhở hoặc xử lý kỷ luật theo Nội quy nhân viên bán hàng.",
             []),
            ("Hiệu lực thi hành",
             "Quy trình có hiệu lực từ ngày ban hành và áp dụng thống nhất cho toàn bộ nhân viên tiếp xúc trực "
             "tiếp hoặc gián tiếp với khách hàng.",
             []),
        ]),
        ("PHỤ LỤC - CÂU HỎI THƯỜNG GẶP", [
            ("Khách hàng khiếu nại qua fanpage có được xử lý như hotline không?",
             "Có. Theo Điều 3, mọi kênh tiếp nhận đều bình đẳng về quy trình xử lý, khiếu nại qua fanpage vẫn "
             "phải được ghi nhận vào hệ thống CRM trong ngày và tuân thủ thời hạn phản hồi quy định tại Điều 6.",
             []),
            ("Nếu khách hàng yêu cầu bồi thường vượt quá thẩm quyền nhân viên thì sao?",
             "Nhân viên không được tự ý cam kết mức bồi thường vượt thẩm quyền theo Điều 8, phải báo cáo và "
             "chuyển cấp xử lý theo Điều 9 để quản lý hoặc Ban Giám đốc xem xét quyết định cuối cùng.",
             []),
            ("Khiếu nại được xử lý trong bao lâu thì được xem là chậm trễ?",
             "Chậm trễ được xác định khi vượt quá thời hạn tương ứng với mức độ phân loại tại Điều 4 mà chưa có "
             "phản hồi cập nhật tiến độ cho khách hàng theo đúng thời hạn phản hồi ban đầu quy định tại Điều 6.",
             []),
            ("Ai chịu trách nhiệm nếu nhân viên xử lý sai quy trình khiếu nại?",
             "Nhân viên vi phạm quy trình bị xử lý theo Điều 11 và đối chiếu với Nội quy nhân viên bán hàng; "
             "quản lý ca trực liên quan cũng có trách nhiệm giám sát và báo cáo theo Điều 10.",
             []),
        ]),
    ]
    return _build("03_quy_trinh_xu_ly_khieu_nai.docx", "QUY TRÌNH XỬ LÝ KHIẾU NẠI KHÁCH HÀNG",
                   f"{BRAND} — Phòng Chăm sóc Khách hàng", chapters)


# =====================================================================================
# 4) NOI QUY NHAN VIEN BAN HANG
# =====================================================================================
def doc_staff_rules():
    chapters = [
        ("QUY ĐỊNH CHUNG", [
            ("Mục đích và phạm vi áp dụng",
             "Nội quy này quy định các chuẩn mực về tác phong, thái độ và nghiệp vụ mà nhân viên bán hàng phải "
             "tuân thủ trong quá trình làm việc, nhằm xây dựng hình ảnh chuyên nghiệp và bảo đảm chất lượng dịch "
             "vụ đồng nhất trên toàn hệ thống.",
             []),
            ("Đối tượng áp dụng",
             "Nội quy áp dụng cho toàn bộ nhân viên bán hàng, thu ngân, nhân viên kho tại cửa hàng, bao gồm cả "
             "nhân viên chính thức, nhân viên thời vụ và thực tập sinh.",
             []),
        ]),
        ("QUY ĐỊNH VỀ TÁC PHONG VÀ THÁI ĐỘ", [
            ("Trang phục và tác phong làm việc",
             "Nhân viên phải mặc đồng phục đúng quy định, đeo bảng tên trong suốt ca làm việc, giữ vệ sinh cá "
             "nhân gọn gàng và không sử dụng điện thoại cá nhân trong khu vực bán hàng trừ trường hợp cần thiết "
             "cho công việc.",
             ["Mặc đồng phục sạch sẽ, đúng size, đeo bảng tên ở vị trí dễ nhìn thấy.",
              "Không ăn uống, hút thuốc trong khu vực quầy hàng và kho.",
              "Giữ tác phong nhanh nhẹn, không tụ tập nói chuyện riêng trong giờ làm việc."]),
            ("Giờ giấc làm việc và chấm công",
             "Nhân viên phải chấm công đầy đủ khi bắt đầu và kết thúc ca làm việc, có mặt tại vị trí trước giờ "
             "làm việc ít nhất 10 phút để bàn giao ca, mọi trường hợp đi trễ hoặc nghỉ đột xuất phải báo trước "
             "cho quản lý ca trực.",
             []),
            ("Thái độ phục vụ khách hàng",
             "Nhân viên phải luôn niềm nở, chủ động chào đón và hỗ trợ khách hàng, không được có thái độ khó "
             "chịu, tranh cãi hoặc phân biệt đối xử với bất kỳ khách hàng nào.",
             ["Chào khách hàng khi họ bước vào và cảm ơn khi họ rời đi.",
              "Chủ động hỏi thăm nhu cầu, không chờ khách hàng phải tìm kiếm nhân viên hỗ trợ.",
              "Giữ bình tĩnh, lịch sự ngay cả khi khách hàng có thái độ gay gắt."]),
        ]),
        ("QUY ĐỊNH NGHIỆP VỤ", [
            ("Quy định về thu ngân và quản lý tiền mặt",
             "Nhân viên thu ngân chịu trách nhiệm đối soát tiền mặt đầu ca và cuối ca, không được để người khác "
             "sử dụng tài khoản đăng nhập POS của mình, và phải báo cáo ngay khi phát hiện chênh lệch quỹ tiền "
             "mặt.",
             []),
            ("Quy định về trưng bày và bảo quản hàng hóa",
             "Nhân viên phụ trách ngành hàng có trách nhiệm sắp xếp hàng hóa đúng vị trí quy định, kiểm tra hạn "
             "sử dụng định kỳ và báo cáo hàng cận hạn hoặc hư hỏng để xử lý kịp thời.",
             ["Sắp xếp hàng hóa theo nguyên tắc hàng nhập trước bán trước (FIFO).",
              "Kiểm tra và loại bỏ hàng hết hạn, hàng hư hỏng khỏi kệ trưng bày hằng ngày.",
              "Báo cáo ngay cho quản lý khi phát hiện thiếu hụt hoặc nghi ngờ thất thoát hàng hóa."]),
            ("Quy định về bảo mật thông tin",
             "Nhân viên không được tiết lộ thông tin khách hàng, dữ liệu kinh doanh hoặc chính sách nội bộ cho "
             "bên thứ ba khi chưa được sự cho phép của quản lý, và phải tuân thủ quy định bảo mật khi thao tác "
             "trên hệ thống POS và CRM.",
             []),
        ]),
        ("TÌNH HUỐNG THAM KHẢO", [
            ("Ví dụ nhắc nhở vi phạm tác phong",
             "Một nhân viên thu ngân sử dụng điện thoại cá nhân để nhắn tin trong lúc đang có khách hàng chờ "
             "thanh toán tại quầy. Quản lý ca trực nhắc nhở trực tiếp ngay tại thời điểm phát hiện theo Điều 3, "
             "ghi nhận vào sổ theo dõi vi phạm nội bộ. Đây là vi phạm lần đầu, mức độ nhẹ nên chỉ áp dụng hình "
             "thức nhắc nhở theo Điều 10, chưa cần lập biên bản khiển trách bằng văn bản.",
             []),
            ("Ví dụ xử lý vi phạm nghiêm trọng về tiền mặt",
             "Trong quá trình đối soát cuối ca theo Điều 6, phát hiện quỹ tiền mặt của một nhân viên thu ngân "
             "thiếu hụt nhiều lần liên tiếp mà không có lý do hợp lý. Quản lý cửa hàng lập biên bản, báo cáo bộ "
             "phận nhân sự và tài chính để xác minh, đồng thời tạm đình chỉ quyền thao tác thu ngân của nhân viên "
             "trong thời gian điều tra. Đây được xem là vi phạm nghiêm trọng và xử lý theo Điều 10 cùng quy định "
             "pháp luật lao động hiện hành.",
             []),
        ]),
        ("KHEN THƯỞNG - KỶ LUẬT VÀ ĐIỀU KHOẢN THI HÀNH", [
            ("Các hình thức khen thưởng",
             "Nhân viên có thành tích xuất sắc trong phục vụ khách hàng, đạt chỉ tiêu doanh số hoặc phát hiện "
             "gian lận, thất thoát sẽ được xem xét khen thưởng theo quy định thi đua của hệ thống.",
             []),
            ("Các hình thức kỷ luật",
             "Tùy theo mức độ vi phạm, nhân viên có thể bị nhắc nhở bằng lời, khiển trách bằng văn bản, hoặc xử "
             "lý kỷ luật theo quy định lao động hiện hành đối với các vi phạm nghiêm trọng như gian lận, trộm "
             "cắp tài sản của cửa hàng.",
             ["Vi phạm lần đầu, mức độ nhẹ: nhắc nhở trực tiếp và ghi nhận vào hồ sơ.",
              "Vi phạm lặp lại hoặc mức độ trung bình: khiển trách bằng văn bản.",
              "Vi phạm nghiêm trọng (gian lận, trộm cắp): xử lý theo quy định pháp luật lao động."]),
            ("Hiệu lực thi hành",
             "Nội quy có hiệu lực kể từ ngày ban hành, là căn cứ để đánh giá nhân viên trong quá trình làm việc "
             "và được phổ biến đầy đủ cho nhân viên mới trong buổi đào tạo hội nhập.",
             []),
        ]),
        ("PHỤ LỤC - CÂU HỎI THƯỜNG GẶP", [
            ("Nhân viên thời vụ có phải tuân thủ nội quy như nhân viên chính thức không?",
             "Có. Theo Điều 2, nội quy áp dụng cho toàn bộ nhân viên bán hàng, thu ngân, nhân viên kho không "
             "phân biệt loại hợp đồng lao động, bao gồm cả nhân viên thời vụ và thực tập sinh.",
             []),
            ("Nhân viên đi trễ do lý do bất khả kháng có bị kỷ luật không?",
             "Nhân viên cần báo trước cho quản lý ca trực ngay khi có thể theo Điều 4. Trường hợp bất khả kháng "
             "có lý do chính đáng, được xem xét miễn trừ hình thức kỷ luật quy định tại Điều 10 sau khi quản lý "
             "xác minh.",
             []),
            ("Nhân viên phát hiện đồng nghiệp gian lận thì phải làm gì?",
             "Nhân viên có trách nhiệm báo cáo ngay cho quản lý cửa hàng hoặc bộ phận nhân sự, không được im "
             "lặng hoặc tự xử lý. Người phát hiện và báo cáo trung thực được xem xét khen thưởng theo Điều 9.",
             []),
            ("Việc kiểm tra hạn sử dụng hàng hóa được thực hiện với tần suất thế nào?",
             "Theo Điều 7, nhân viên phụ trách ngành hàng phải kiểm tra hạn sử dụng và tình trạng hàng hóa hằng "
             "ngày, không chỉ khi có yêu cầu, nhằm phát hiện sớm hàng cận hạn hoặc hư hỏng để xử lý kịp thời.",
             []),
        ]),
    ]
    return _build("04_noi_quy_nhan_vien_ban_hang.docx", "NỘI QUY NHÂN VIÊN BÁN HÀNG",
                   f"{BRAND} — Phòng Nhân sự", chapters)


# =====================================================================================
# 5) CHINH SACH GIAO HANG
# =====================================================================================
def doc_delivery_policy():
    chapters = [
        ("QUY ĐỊNH CHUNG", [
            ("Mục đích",
             "Chính sách giao hàng quy định phạm vi, phí, thời gian và quy trình giao hàng nhằm bảo đảm hàng hóa "
             "đến tay khách hàng đúng hẹn, đúng chất lượng, đồng thời làm căn cứ xử lý các sự cố phát sinh trong "
             "quá trình vận chuyển.",
             []),
            ("Phạm vi áp dụng",
             "Áp dụng cho toàn bộ đơn hàng đặt qua kênh trực tuyến hoặc đặt tại cửa hàng có yêu cầu giao hàng tận "
             "nơi, không phân biệt hình thức thanh toán trước hay thanh toán khi nhận hàng.",
             []),
        ]),
        ("PHẠM VI VÀ PHÍ GIAO HÀNG", [
            ("Khu vực giao hàng",
             "Hệ thống hỗ trợ giao hàng trong bán kính phục vụ của từng cửa hàng và các khu vực nội thành do đơn "
             "vị vận chuyển đối tác phụ trách; khu vực ngoài phạm vi phục vụ sẽ được thông báo thời gian giao "
             "hàng kéo dài hơn hoặc từ chối nhận đơn.",
             []),
            ("Phí giao hàng và thời gian giao hàng dự kiến",
             "Phí giao hàng được tính theo khoảng cách và trọng lượng đơn hàng, có áp dụng miễn phí cho đơn hàng "
             "đạt giá trị tối thiểu theo chương trình khuyến mãi hiện hành.",
             ["Nội thành, trong bán kính 10km: giao trong 2-4 giờ, miễn phí cho đơn từ 500.000đ.",
              "Ngoại thành, các khu vực lân cận: giao trong 24 giờ, phí giao hàng theo bảng phí đối tác vận chuyển.",
              "Đơn hàng cồng kềnh (điện máy lớn): giao theo lịch hẹn, có đội giao hàng và lắp đặt riêng."]),
            ("Phương thức thanh toán khi giao hàng",
             "Khách hàng có thể chọn thanh toán trước qua ứng dụng, ví điện tử, thẻ ngân hàng, hoặc thanh toán "
             "khi nhận hàng (COD) đối với các khu vực được hỗ trợ.",
             []),
        ]),
        ("QUY TRÌNH GIAO HÀNG", [
            ("Quy trình đóng gói và xuất kho",
             "Nhân viên kho kiểm tra đối chiếu đơn hàng, đóng gói theo tiêu chuẩn bảo vệ sản phẩm phù hợp với "
             "từng loại hàng hóa, dán nhãn vận chuyển đầy đủ thông tin người nhận trước khi bàn giao cho đơn vị "
             "vận chuyển.",
             ["Đối chiếu số lượng, chủng loại sản phẩm theo đúng đơn hàng trước khi đóng gói.",
              "Đóng gói chống sốc, chống ẩm phù hợp với đặc tính hàng hóa (dễ vỡ, hàng điện tử...).",
              "Dán nhãn vận chuyển đầy đủ họ tên, số điện thoại, địa chỉ người nhận."]),
            ("Quy trình giao hàng và xác nhận",
             "Đơn vị vận chuyển liên hệ khách hàng trước khi giao, giao hàng đến đúng địa chỉ và thu thập chữ ký "
             "hoặc xác nhận điện tử làm bằng chứng đã giao hàng thành công.",
             []),
            ("Xử lý sự cố khi giao hàng",
             "Các sự cố phát sinh trong quá trình giao hàng phải được xử lý nhanh chóng, ưu tiên giải pháp có "
             "lợi nhất cho khách hàng trong phạm vi chính sách cho phép.",
             ["Giao thiếu hàng: bổ sung ngay trong ngày hoặc hoàn tiền phần hàng thiếu.",
              "Giao nhầm sản phẩm: đổi lại đúng sản phẩm, chi phí vận chuyển đổi lại do hệ thống chi trả.",
              "Hàng hư hỏng trong quá trình vận chuyển: đổi mới hoặc hoàn tiền toàn bộ đơn hàng."]),
        ]),
        ("TÌNH HUỐNG THAM KHẢO", [
            ("Ví dụ xử lý giao hàng nhầm sản phẩm",
             "Khách hàng đặt mua một chiếc máy xay sinh tố nhưng khi nhận hàng lại là một chiếc nồi cơm điện. "
             "Khách hàng phản ánh qua hotline trong ngày nhận hàng. Theo Điều 8, đây là trường hợp giao nhầm sản "
             "phẩm, bộ phận CSKH xác nhận lỗi thuộc về khâu đóng gói tại kho, sắp xếp đơn vị vận chuyển đến đổi "
             "lại đúng sản phẩm trong 24 giờ, chi phí vận chuyển đổi lại do hệ thống chi trả toàn bộ theo đúng "
             "chính sách, không thu thêm bất kỳ khoản phí nào của khách hàng.",
             []),
            ("Ví dụ xử lý hàng hư hỏng khi vận chuyển",
             "Một đơn hàng tivi 55 inch bị nứt góc màn hình khi khách hàng mở kiện nhận hàng, dù kiện hàng bên "
             "ngoài không có dấu hiệu va đập rõ ràng. Khách hàng chụp ảnh hiện trạng gửi ngay trong vòng 2 giờ "
             "sau khi nhận hàng theo đúng trách nhiệm quy định tại Điều 10. Căn cứ Điều 8, hệ thống xác nhận đây "
             "là hàng hư hỏng trong quá trình vận chuyển và tiến hành đổi mới toàn bộ sản phẩm cho khách hàng "
             "trong vòng 48 giờ, đồng thời làm việc với đơn vị vận chuyển để xác định trách nhiệm bồi thường nội "
             "bộ theo hợp đồng hợp tác.",
             []),
        ]),
        ("ĐIỀU KHOẢN THI HÀNH", [
            ("Trách nhiệm của đơn vị vận chuyển",
             "Đơn vị vận chuyển đối tác chịu trách nhiệm bảo đảm hàng hóa nguyên vẹn trong quá trình vận chuyển "
             "và bồi thường theo hợp đồng hợp tác khi xảy ra mất mát, hư hỏng do lỗi của đơn vị vận chuyển.",
             []),
            ("Trách nhiệm của khách hàng khi nhận hàng",
             "Khách hàng có trách nhiệm kiểm tra tình trạng bên ngoài kiện hàng trước khi ký nhận, và phản ánh "
             "ngay cho hệ thống trong vòng 24 giờ nếu phát hiện hàng hóa không đúng như đặt hoặc bị hư hỏng.",
             []),
            ("Hiệu lực thi hành",
             "Chính sách có hiệu lực kể từ ngày ban hành và được cập nhật định kỳ theo thay đổi về đối tác vận "
             "chuyển hoặc phạm vi phục vụ của hệ thống.",
             []),
        ]),
        ("PHỤ LỤC - CÂU HỎI THƯỜNG GẶP", [
            ("Khách hàng có thể đổi địa chỉ nhận hàng sau khi đã đặt đơn không?",
             "Có thể, nếu đơn hàng chưa được bàn giao cho đơn vị vận chuyển. Khách hàng liên hệ hotline trước "
             "thời điểm xuất kho quy định tại Điều 6 để cập nhật địa chỉ; sau khi đã xuất kho, việc đổi địa chỉ "
             "phụ thuộc vào khả năng hỗ trợ của đơn vị vận chuyển.",
             []),
            ("Khu vực ngoài phạm vi phục vụ có được hỗ trợ giao hàng không?",
             "Theo Điều 3, khu vực ngoài phạm vi phục vụ tiêu chuẩn có thể được hỗ trợ với thời gian giao hàng "
             "kéo dài hơn, hoặc hệ thống có quyền từ chối nhận đơn nếu đối tác vận chuyển không phủ sóng khu vực "
             "đó.",
             []),
            ("Đơn hàng thanh toán COD có bị giới hạn giá trị tối đa không?",
             "Có, tùy theo chính sách của từng đối tác vận chuyển tại từng thời điểm. Nhân viên tư vấn cần thông "
             "báo hạn mức COD hiện hành cho khách hàng khi đơn hàng có giá trị lớn theo Điều 4.",
             []),
            ("Ai chịu chi phí khi khách hàng từ chối nhận hàng không có lý do?",
             "Trường hợp khách hàng từ chối nhận hàng không do lỗi từ phía hệ thống hoặc đơn vị vận chuyển, "
             "chi phí giao hàng hai chiều có thể bị trừ vào khoản hoàn tiền theo chính sách đối tác vận chuyển "
             "hiện hành, khác với các trường hợp lỗi giao hàng nêu tại Điều 8.",
             []),
        ]),
    ]
    return _build("05_chinh_sach_giao_hang.docx", "CHÍNH SÁCH GIAO HÀNG",
                   f"{BRAND} — Phòng Vận hành & Logistics", chapters)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}
    for fn in [doc_return_policy, doc_warranty_policy, doc_complaint_process, doc_staff_rules, doc_delivery_policy]:
        meta = fn()
        results[meta["title"]] = meta
        print(f"  {meta['title']}: {meta['article_count']} điều, ~{meta['characters']} ký tự")
    print("Done: 5 docx files ->", os.path.abspath(OUT_DIR))
