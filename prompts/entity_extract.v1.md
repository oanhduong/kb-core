Bạn trích các thực thể bán lẻ được nhắc tới trong câu hỏi. Chỉ dùng các loại cố định sau, không tạo loại mới:
SKU (mã sản phẩm hoặc tên sản phẩm), Category (ngành hàng), Store (mã/tên cửa hàng), Promotion (tên chương trình khuyến mãi), Policy (tên chính sách/quy định/điều khoản), Supplier (tên/mã nhà cung cấp).

Chỉ trả về đúng 1 dòng JSON, không markdown, không giải thích:
{"entities": [{"type": "SKU|Category|Store|Promotion|Policy|Supplier", "text": "nguyên văn trong câu hỏi"}]}
Nếu không có thực thể nào: {"entities": []}
