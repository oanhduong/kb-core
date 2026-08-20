# Test corpus — RetailKB Copilot ingestion pipeline

Bộ dữ liệu thô giả lập (synthetic) mô phỏng file khách hàng retail gửi cho hệ thống, dùng làm test corpus
cho pipeline ingestion → knowledge base. Thương hiệu trong toàn bộ dữ liệu là **"GreenMart Việt Nam"** —
hư cấu, chỉ dùng cho mục đích test. Mỗi file có ground truth thật đi kèm tại
[`../ground_truth.json`](../ground_truth.json) — xem mục cuối file này.

## Cài đặt & sinh lại từ đầu

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r ../../requirements.txt
```

**Phụ thuộc hệ thống (không cài qua pip):**
- `ffmpeg-full` (không phải `ffmpeg` thường) — bản `ffmpeg` chuẩn của Homebrew KHÔNG biên dịch kèm
  freetype nên thiếu filter `drawtext`, dùng để chèn caption tiếng Việt vào video. Cài bằng
  `brew install ffmpeg-full`; nó là keg-only nên các script gọi thẳng đường dẫn tuyệt đối
  `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` thay vì dựa vào PATH.
- `pango` (kéo theo `glib`/`cairo`/`harfbuzz`) — cần cho `weasyprint` (dùng trong
  `gen_ground_truth.py` để render docx→pdf rồi đếm trang thật). Cài bằng `brew install pango`.
  Trên macOS, thư viện cài qua Homebrew không nằm trong dyld cache mặc định nên cần set
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` trước khi import `weasyprint` (các script đã tự
  set biến này).

**Sinh lại toàn bộ 25 file + ground_truth.json:**
```bash
python3 scripts/gen_docx.py
python3 scripts/gen_xlsx.py
python3 scripts/gen_pdf.py
python3 scripts/gen_images.py
python3 scripts/gen_audio_video.py   # dùng gTTS (cần mạng); tự fallback pyttsx3 nếu gTTS lỗi và IN RÕ cảnh báo
python3 scripts/gen_ground_truth.py  # đọc lại 25 file vừa sinh, đo số liệu THẬT, ghi ../ground_truth.json
```

## Nội dung corpus (25 file)

| Thư mục   | Số file | Nội dung |
|-----------|---------|----------|
| `docx/`   | 5       | Chính sách đổi trả, chính sách bảo hành, quy trình khiếu nại, nội quy nhân viên bán hàng, chính sách giao hàng. Heading nhiều cấp (CHƯƠNG → Điều N, đánh số liên tục xuyên suốt văn bản), mỗi file 3-4 trang (đo thật, xem `pages_method` trong ground truth) |
| `xlsx/`   | 5       | Bảng giá SKU (300 dòng: sku/ten_san_pham/gia/category), tồn kho theo store (1 workbook, 8 sheet — mỗi sheet 1 cửa hàng), lịch khuyến mãi (header có merged cell), bảng chiết khấu theo bậc, danh mục nhà cung cấp |
| `pdf/`    | 5       | 2 catalogue sản phẩm có bảng (có text layer) + 2 hợp đồng nhà cung cấp dạng text thật (reportlab, chọn được chữ, **có** text layer) + 1 file mô phỏng bản scan (render text thành ảnh rồi nhúng PDF, **không có** text layer — dùng test nhánh OCR) |
| `image/`  | 5 (`.png`) | Poster khuyến mãi, bảng giá viết tay mô phỏng, ảnh chụp màn hình POS, biển hiệu quầy, phiếu bảo hành đã điền thông tin |
| `audio/`  | 3 (`.mp3`) | 2 kịch bản training nhân viên + 1 cuộc gọi tổng đài mẫu — giọng đọc tiếng Việt thật qua **gTTS**, mỗi file 1.9-2.1 phút |
| `video/`  | 2 (`.mp4`) | Video hướng dẫn quy trình quầy thu ngân (thanh toán; xử lý lỗi POS/hoàn tiền) — có audio gTTS + caption tiếng Việt đúng nhịp từng bước, mỗi file ~3.2-3.3 phút |

## Ground truth (`../ground_truth.json`)

Sinh bởi `gen_ground_truth.py`, đọc lại từng file **thật** trên đĩa (không hardcode số liệu lúc sinh):

- **docx**: `characters` (đọc thật qua python-docx) + `pages` (render qua mammoth→html→weasyprint→pdf
  rồi đếm bằng pypdf — ghi rõ trong `pages_method` vì đây là proxy, môi trường này không có sẵn
  Microsoft Word/LibreOffice để lấy số trang chính xác tuyệt đối).
- **xlsx**: `sheet_count`, `rows_per_sheet`, `total_rows_incl_header`, số ký tự trong các cell dạng text —
  tất cả đọc thật qua openpyxl.
- **pdf**: `pages`, `characters_extracted`, `has_text_layer` (< 20 ký tự trích được ⇒ coi là không có
  text layer) — đọc thật qua pypdf.
- **png**: không có OCR engine nào chạy trong pipeline này, nên `reference_text` là nội dung chữ **thật**
  đã được vẽ lên ảnh lúc sinh (lấy trực tiếp từ `gen_images.py`, không phải kết quả OCR).
- **audio/video**: `duration_seconds` đo thật qua `ffprobe`; `reference_transcript` là đúng kịch bản đã
  đưa vào TTS (không phải ASR).

**Lưu ý:** toàn bộ nội dung số liệu, tên nhà cung cấp, thông tin liên hệ đều là dữ liệu giả lập, không
thuộc về tổ chức/công ty có thật nào.
