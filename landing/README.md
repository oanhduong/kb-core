# Landing Hỏi Số: trang chủ là một màn game

Cả trang là một màn chơi toàn màn hình. Qua màn chơi, người xem thấy toàn bộ tính năng, rồi bấm **Vào phần mềm** để vào app thật (KB Explorer).

| Màn | Người chơi làm gì | Game đáp lại | Tính năng được thấy |
|---|---|---|---|
| Tiêu đề | Bấm **Chơi ngay** | Thế giới bầu trời, 8 file mẫu bay quanh kho | |
| **1 · Nạp kho** | Kéo file vào kho, bấm **Hút hết**, hoặc thả file của mình | Tay chỉ minh hoạ thao tác, điểm và combo, túi đồ ghi "đã đọc được gì". Nút **Qua màn sau** mở khoá khi đã nạp ít nhất 1 file; nạp đủ 8 file thì tự qua màn | Đọc Excel, CSV, PDF, ảnh scan, Word, ghi âm, slide |
| **2 · Hỏi kho** | Đánh 6 thẻ câu hỏi vào quả cầu, hoặc gõ câu hỏi tự do | Mỗi thẻ một hiệu ứng, xong là sao, xu và thông báo thành tựu | Theo thứ tự thẻ: Hỏi đáp, Tạo chart, Báo cáo, Bản đồ tri thức, Mô hình dữ liệu, Đối soát hợp đồng |
| Phá đảo | Đủ 6 sao | Pháo giấy, cúp, 6 huy hiệu | **Vào phần mềm**, Chơi lại |

Câu gõ tự do được ghép với thẻ gần nghĩa nhất bằng từ khoá. Câu không khớp được mời hỏi thẳng trong phần mềm.

Nút **Vào phần mềm** (góc trên, màn phá đảo, câu hỏi không khớp) dẫn tới `APP_URL/signup?utm_source=game&utm_content=<vị trí nút>`. Màn `/signup` của web app tạo tài khoản kèm workspace đầu tiên.

Không dùng thư viện ngoài: thế giới 3D giả lập bằng Canvas 2D, âm thanh tổng hợp bằng WebAudio (chỉ bật sau khi bấm Chơi, có nút tắt trên máy tính). Font Baloo 2 và Nunito, đều có bộ chữ tiếng Việt. Tôn trọng `prefers-reduced-motion`.

## Cấu hình và triển khai

| Thứ cần đặt | Ở đâu | Ý nghĩa |
|---|---|---|
| `APP_URL` | Vercel → Project → Settings → Environment Variables | Địa chỉ web app, ví dụ `https://app.ten-mien.vn`. Bỏ trống thì nút dẫn tới `http://localhost:3000` (app chạy bằng `make up`) |
| `CONFIG.ENDPOINT` | đầu script trong `index.html` | URL Google Apps Script (`apps-script.gs`) để ghi sự kiện vào Google Sheet. Bỏ trống thì chỉ đẩy `dataLayer` và lưu `localStorage` |
| `CONFIG.BRAND` | đầu script trong `index.html` | Tên hiển thị |

Vercel chọn Root Directory là `landing`; `vercel.json` build bằng `build-standalone.sh` (điền `APP_URL`, thêm doctype và meta) ra `dist/`. Link cũ `/wow` và `/chat` chuyển về `/`.

```bash
APP_URL=https://app.ten-mien.vn sh landing/build-standalone.sh   # -> landing/dist/index.html
```

Đổi `APP_URL` trên Vercel xong phải **Redeploy** để build lại.

## Sự kiện được đo

Gửi tới `CONFIG.ENDPOINT`, đẩy `dataLayer` tên `game_*`, lưu bản sao trong `localStorage` khoá `hs_game_events`.

| Sự kiện | Ý nghĩa |
|---|---|
| `view`, `play` | Lượt xem, bấm Chơi (hoặc vào thẳng bằng kéo file) |
| `file_in`, `magnet`, `own_files` | Nạp từng file, dùng Hút hết, thả file của chính mình |
| `next`, `level1_clear`, `level2_start` | Qua màn bằng nút (kèm số file đã nạp) hay nạp đủ kho |
| `card`, `star` | Đánh thẻ nào, bằng thẻ hay gõ chữ; nhận sao |
| `typed` | Câu tự gõ và thẻ khớp (trống nếu không khớp) |
| `all_clear` | Phá đảo |
| `app_open` | Bấm Vào phần mềm, kèm vị trí nút, màn, số sao, điểm |
| `cite_open`, `replay` | Bấm trích dẫn, chơi lại |

Phễu chính: `view → play → next|level1_clear → star ≥ 3 → all_clear → app_open`.
