"""Mô hình dữ liệu nghiệp vụ: file thô (Excel/CSV) -> bảng trong warehouse -> quan hệ + logic tính (có bằng chứng,
người dùng duyệt bằng ngôn ngữ tự nhiên) -> view tính lại kết quả -> đối chiếu với file gốc -> tài liệu quan hệ.

Tổng quát cho mọi workbook: không có gì gắn với một file/use case cụ thể. Toàn bộ trạng thái nằm trong 1 spec JSON
(modeling/spec.py) — mọi bước (phân tích, người duyệt sửa, build) chỉ đọc/ghi spec đó.
"""
