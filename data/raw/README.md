# Dữ liệu đầu vào mẫu — RetailKB Copilot

Bộ dữ liệu thô giả lập (synthetic) mô phỏng file khách hàng retail gửi cho hệ thống, dùng để phát triển
và đo đạc pipeline ingestion → knowledge base ở Phase 1 trở đi. Thương hiệu trong toàn bộ dữ liệu là
**"GreenMart Việt Nam"** — hư cấu, chỉ dùng cho mục đích test.

Sinh bằng script trong `../../scripts/` (`gen_docx.py`, `gen_xlsx.py`, `gen_pdf.py`, `gen_images.py`,
`gen_audio_video.sh`). Audio dùng giọng đọc tiếng Việt thật (macOS `say`, giọng "Linh"); video dựng bằng
`ffmpeg` từ audio + caption. Chạy lại: `source .venv/bin/activate && python3 scripts/gen_xxx.py`.

| Thư mục   | Số file | Nội dung |
|-----------|---------|----------|
| `docx/`   | 5       | Chính sách đổi trả, bảo hành điện máy, quy trình khiếu nại, phụ lục đổi trả theo ngành hàng, hướng dẫn khiếu nại qua tổng đài |
| `xlsx/`   | 5       | Bảng giá SKU (điện máy, FMCG), tồn kho theo cửa hàng (miền Bắc, miền Nam), lịch khuyến mãi Q3/2026 |
| `pdf/`    | 5       | Catalogue sản phẩm có bảng (gia dụng, điện tử, thực phẩm) + 2 hợp đồng nhà cung cấp dạng "bản scan" (ảnh có nhiễu/nghiêng, dùng để test OCR) |
| `image/`  | 5       | 3 poster khuyến mãi + 2 ảnh bảng giá viết tay (mô phỏng ảnh chụp điện thoại: nhiễu, hơi nghiêng, nén JPEG) |
| `audio/`  | 3       | 2 bài training nhân viên + 1 cuộc gọi tổng đài mẫu (giọng đọc tiếng Việt thật, MP3) |
| `video/`  | 2       | Video hướng dẫn quy trình quầy thu ngân (thanh toán; xử lý lỗi POS/hoàn tiền) — MP4, có audio tiếng Việt + caption |

**Lưu ý:** toàn bộ nội dung số liệu, tên nhà cung cấp, thông tin liên hệ đều là dữ liệu giả lập, không
thuộc về tổ chức/công ty có thật nào.
