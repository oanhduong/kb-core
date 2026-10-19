# Landing Hỏi Số: trang chủ là một màn game

Cả trang là một màn chơi toàn màn hình. Qua màn chơi, người xem thấy toàn bộ tính năng, rồi bấm **Vào phần mềm** để vào app thật (KB Explorer).

| Màn | Người chơi làm gì | Game đáp lại | Tính năng được thấy |
|---|---|---|---|
| Tiêu đề | Bấm **Chơi ngay** | Thế giới bầu trời, 8 file mẫu bay quanh kho | |
| **1 · Nạp kho** | Kéo file vào kho, bấm **Hút hết**, hoặc thả file của mình | Tay chỉ minh hoạ thao tác, điểm và combo, túi đồ ghi "đã đọc được gì". Nút **Qua màn sau** mở khoá khi đã nạp ít nhất 1 file; nạp đủ 8 file thì tự qua màn | Đọc Excel, CSV, PDF, ảnh scan, Word, ghi âm, slide |
| **2 · Hỏi kho** | Đánh 6 thẻ câu hỏi vào quả cầu, hoặc gõ câu hỏi tự do | Mỗi thẻ một hiệu ứng, xong là sao, xu và thông báo thành tựu | Theo thứ tự thẻ: Hỏi đáp, Tạo chart, Báo cáo, Bản đồ tri thức, Mô hình dữ liệu, Đối soát hợp đồng |
| Phá đảo | Đủ 6 sao | Pháo giấy, cúp, 6 huy hiệu | **Vào phần mềm**, Chơi lại |

Câu gõ tự do được ghép với thẻ gần nghĩa nhất bằng từ khoá. Câu không khớp được mời hỏi thẳng trong phần mềm.

Nút **Vào phần mềm** (góc trên, màn phá đảo, câu hỏi không khớp) mở web app KB Explorer tại `/app/` trên cùng site. Chưa đăng nhập thì app hiện màn đăng nhập, có link sang đăng ký (tạo tài khoản kèm workspace đầu tiên); đã đăng nhập thì vào thẳng workspace.

Không dùng thư viện ngoài: thế giới 3D giả lập bằng Canvas 2D, âm thanh tổng hợp bằng WebAudio (chỉ bật sau khi bấm Chơi, có nút tắt trên máy tính). Font Baloo 2 và Nunito, đều có bộ chữ tiếng Việt. Tôn trọng `prefers-reduced-motion`.

## Cấu hình và triển khai

Vercel (Root Directory là `landing`) build hai phần vào `dist/`:

| Đường dẫn | Build bởi | Là gì |
|---|---|---|
| `/` | `build-standalone.sh` | Game (file `index.html`, thêm doctype và meta) |
| `/app/` | `build-app.sh` | Web app trong thư mục `web/`, build với `VITE_BASE=/app/`; mọi đường dẫn `/app/*` trả về app (vercel.json) |

Link cũ `/wow` và `/chat` chuyển về `/`. Vercel cần bật **Include files outside the root directory** (mặc định đã bật) để `build-app.sh` đọc được thư mục `web/`.

**Backend:** web app gọi API FastAPI. Backend cần Postgres, worker và model nên không chạy trên Vercel; dựng bằng `docker compose` trên một máy chủ (xem README gốc). Khi có backend:

| Biến | Đặt ở đâu | Ý nghĩa |
|---|---|---|
| `API_URL` | Vercel → Settings → Environment Variables | Địa chỉ API, ví dụ `https://api.ten-mien.vn`. Bỏ trống thì app gọi `/api` cùng site (chưa có backend thì đăng nhập báo lỗi) |
| `CORS_ORIGINS` | `.env` của backend | Thêm domain Vercel, ví dụ `https://kb-core-landing.vercel.app` |
| `APP_URL` | Vercel (tuỳ chọn) | Chỉ đặt khi muốn nút dẫn sang app ở domain khác thay vì `/app` |
| `CONFIG.ENDPOINT` | đầu script trong `index.html` | URL Google Apps Script (`apps-script.gs`) để ghi sự kiện vào Google Sheet |

Đổi biến trên Vercel xong phải **Redeploy**.

```bash
sh landing/build-standalone.sh && API_URL=https://api.ten-mien.vn sh landing/build-app.sh   # -> landing/dist
```

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
