# Landing Hỏi Số: game ở trang chủ, bản thử Excel ở /chat

| Đường dẫn | File nguồn | Là gì |
|---|---|---|
| `/` | `index.html` | **Game Hỏi Số**: trang chủ là một màn chơi, qua đó người chơi thấy toàn bộ tính năng |
| `/chat` | `chat/index.html` | Bản thử với file Excel thật của người dùng, hai biến thể A/B (mô tả bên dưới) |
| `/wow` | | Chuyển hướng về `/` cho các link cũ |

## Game ở trang chủ

Cả trang là một màn chơi toàn màn hình, không có đoạn giải thích. Hướng dẫn bằng tay chỉ, icon và nhãn hai ba chữ.

| Màn | Người chơi làm gì | Game đáp lại | Tính năng sản phẩm được thấy |
|---|---|---|---|
| Tiêu đề | Bấm **Chơi ngay** | Thế giới bầu trời với 8 file mẫu bay quanh kho | |
| **1 · Nạp kho** | Kéo file vào kho, bấm **Hút hết**, hoặc thả file của mình | Tay chỉ minh hoạ thao tác, file bay vào kho, điểm và combo, mỗi file rơi vào túi đồ kèm nhãn "đã đọc được gì" | Đọc mọi loại file: Excel, CSV, PDF, ảnh scan, Word, ghi âm, slide |
| **2 · Hỏi kho** | Đánh 6 thẻ câu hỏi vào quả cầu, hoặc gõ câu hỏi tự do | Mỗi thẻ bay vào cầu rồi diễn một hiệu ứng riêng, xong là sao, xu và thông báo thành tựu | Hỏi đáp có nguồn, SQL ra biểu đồ, bản đồ tri thức, báo cáo xuất PDF/Word, mô hình dữ liệu Excel, từ chối đoán bừa |
| Phá đảo | Đủ 6 sao | Pháo giấy, cúp, 6 huy hiệu | CTA giữ chỗ, link sang bản thử Excel thật, chơi lại |

Câu gõ tự do được ghép với thẻ gần nghĩa nhất bằng từ khoá. Câu không khớp được trả lời "để bản thật trả lời" kèm nút giữ chỗ, và câu đó được ghi lại làm dữ liệu nhu cầu.

Không dùng thư viện ngoài: thế giới 3D giả lập bằng Canvas 2D, âm thanh tổng hợp bằng WebAudio (chỉ bật sau khi bấm Chơi, có nút tắt). Font Baloo 2 và Nunito, đều có bộ chữ tiếng Việt. Tôn trọng `prefers-reduced-motion`.

Sự kiện được đo (gửi `CONFIG.ENDPOINT`, đẩy `dataLayer` tên `game_*`, lưu bản sao trong `localStorage` khoá `hs_game_events`):

| Sự kiện | Ý nghĩa |
|---|---|
| `view`, `play` | Lượt xem, bấm Chơi (hoặc vào thẳng bằng kéo file) |
| `file_in`, `magnet`, `own_files` | Nạp từng file, dùng nút Hút hết, thả file của chính mình |
| `level1_clear`, `skip`, `level2_start` | Qua màn 1 hay bỏ qua |
| `card`, `star` | Đánh thẻ nào, bằng thẻ hay gõ chữ; nhận sao |
| `typed` | Câu tự gõ và thẻ khớp (trống nếu không khớp) |
| `all_clear` | Phá đảo |
| `signup_open`, `signup_submit` | Mở form giữ chỗ từ đâu (`hud`, `final`, `typed_miss`, `drop`), nhóm khách, mức sẵn sàng trả |
| `try_file`, `info_open`, `cite_open`, `ref_copy`, `replay` | Sang bản thử Excel, xem thông tin, bấm trích dẫn, chép link giới thiệu, chơi lại |

Phễu chính: `view → play → level1_clear → star ≥ 3 → all_clear → signup_submit`.

---

# Bản thử Excel (`/chat`): A/B thông điệp + waitlist

Một file `chat/index.html` chứa **hai biến thể** cùng cấu trúc, chỉ khác thông điệp và cách demo:

| Biến thể | Thông điệp | Demo | Rào chuyển đổi (gate) |
|---|---|---|---|
| **A** `#a` | "Thả Excel vào, hỏi bằng tiếng Việt, số liệu tính đúng" | Chip câu hỏi → SQL → biểu đồ → nguồn [1] | Gõ câu hỏi tự do |
| **B** `#b` | "Báo cáo tháng xong trong 2 phút từ file Excel bạn đang có" | Báo cáo 4 mục tự sinh từ file | Xuất PDF / Word / Làm hàng tháng / Sửa |

Người vào không có `#a`/`#b` được chia ngẫu nhiên 50/50 và **dính** (localStorage) để một người luôn thấy cùng một biến thể.
Demo chạy **hoàn toàn trên trình duyệt** (SheetJS đọc file, tính toán bằng JS, Chart.js vẽ): không có backend, file người dùng không rời khỏi máy họ. Đây vừa là lời hứa bảo mật trên trang, vừa là lý do demo "wow" được với file thật của họ.

## Vì sao thiết kế như vậy

1. **Wow phải đến từ dữ liệu của chính họ.** Video demo không thuyết phục bằng việc thả file xuất từ Shopee/KiotViet vào và thấy biểu đồ trong 3 giây. Có 3 bộ dữ liệu mẫu cho người chưa có file trong tay.
2. **Rào chuyển đổi đặt đúng chỗ cầu vượt cung.** Người dùng chỉ bị chặn khi muốn thứ bản thử không làm được (hỏi tự do, xuất file). Lúc đó họ đã thấy giá trị, nên email đổi lấy "trả lời chính câu này khi mở cửa" là giao dịch công bằng. Câu hỏi họ gõ được lưu lại: đó là dữ liệu nhu cầu thật, không phải khảo sát.
3. **Trung thực về bản thử.** Dòng chữ nhỏ nói rõ bản thử dùng công thức cố định, bản chính thức dùng AI viết SQL. Không hứa thứ chưa có.
4. **Thứ tự thuyết phục: xem → thử → đăng ký.** Cửa sổ sản phẩm tự chạy một kịch bản thật khi mở trang (gõ câu hỏi, viết SQL, vẽ biểu đồ, mở nguồn), xong mới mời thả file của mình, và chỉ mời đăng ký khi người dùng muốn thứ bản thử không làm được.
5. **Mọi tương tác đều đo được theo biến thể**, và form waitlist hỏi thêm 2 câu định giá: nhóm công việc và mức sẵn sàng trả.

## Những gì được đo

Mỗi phiên gửi các sự kiện sau (không bao giờ gửi nội dung file, chỉ gửi hình dạng: số dòng, số cột, kiểu cột):

| Sự kiện | Ý nghĩa |
|---|---|
| `view` | Lượt xem, kèm variant, UTM, referrer, độ rộng màn hình |
| `autoplay_done` / `autoplay_skip` | Xem hết demo tự chạy / bỏ qua để tự thử (kèm lý do) |
| `interactive_start` | Lần đầu người dùng tự điều khiển demo, kèm cách vào (cta, skip, overlay, sample, upload) |
| `demo_sample` / `demo_upload` | Dùng dữ liệu mẫu / **tải file của mình** (tín hiệu ý định mạnh nhất) |
| `chip_click` | Bấm câu hỏi mẫu nào, lần thứ mấy |
| `question_typed` | **Câu hỏi tự gõ** (nội dung) |
| `gate` | Đụng rào, loại rào (`ask`, `export_pdf`, `export_docx`, `schedule`, `edit`) |
| `cta_click` | Bấm nút đăng ký ở đâu (top, hero, nudge, final) |
| `waitlist_open` / `waitlist_submit` | Mở form / gửi form, kèm segment, mức trả, có câu hỏi hay không |
| `cite_open`, `faq_open`, `section_view`, `scroll_50`, `scroll_90`, `leave` | Mức đọc, điểm rơi |

Phễu chính để so A với B:

```
view → autoplay_done → demo_engaged → own_upload → gate_hit → waitlist
```

Ngưỡng gợi ý sau 2 tuần chạy traffic lạnh (ads + nhóm cộng đồng):

| Chỉ số | Yếu | Chấp nhận | Tốt |
|---|---|---|---|
| view → demo_engaged | < 20% | 30% | > 40% |
| view → own_upload | < 5% | 10% | > 15% |
| view → waitlist | < 3% | 6% | > 10% |
| Tỉ lệ chọn mức trả ≥ 100k | < 20% | 35% | > 50% |

Để phân biệt hai biến thể chênh nhau khoảng 2 điểm phần trăm ở tỉ lệ waitlist (ví dụ 5% so với 7%), cần xấp xỉ 1.500 lượt xem mỗi biến thể. Dưới 500 lượt mỗi bên thì chỉ nên đọc xu hướng, không kết luận.

Quan trọng hơn cả tỉ lệ: đọc **danh sách câu hỏi người dùng tự gõ** và **câu hỏi họ ghi khi đăng ký**. Đó là backlog tính năng và là từ khoá SEO.

## Nơi dữ liệu được lưu

Trang có 3 kênh ghi, dùng kênh nào tuỳ nơi chạy:

1. **Domain riêng (chạy thật):** `CONFIG.ENDPOINT` trỏ tới Google Apps Script (`apps-script.gs`), ghi vào Google Sheet 2 tab `events` và `waitlist`. Phân tích bằng pivot table. Có thể thêm GA4/Plausible: trang đã push `dataLayer` sự kiện `lp_*` nên gắn GTM là xong.
2. **Artifact trên claude.ai (xem trước, test nội bộ):** ghi vào artifact db. Chỉ người có quyền ghi (chủ artifact, người được mời ở mức Contributor) mới ghi được; khách vãng lai qua link công khai không ghi được. Vì vậy bản artifact dùng để duyệt thiết kế và chạy thử phễu, không dùng để nhận traffic quảng cáo.
3. **localStorage:** luôn ghi, để trang `#results` hiện phễu của chính phiên bạn khi hai kênh trên không có.

Mở `#results` (hoặc `?view=results` trên domain riêng) để xem bảng phễu theo biến thể, nhóm, mức trả, rào nào chuyển đổi, câu hỏi tự gõ, nguồn truy cập.

## Triển khai lên domain riêng

Trang là tĩnh, không cần server. File `index.html` viết theo chuẩn artifact (không có thẻ `<html>`/`<head>`), nên khi tự host hãy đóng gói trước:

```bash
sh landing/build-standalone.sh        # game  -> landing/dist/index.html
sh landing/build-standalone.sh chat   # bản thử Excel -> landing/dist/chat/index.html
# Cloudflare Pages / Netlify / Vercel: kéo thả thư mục landing/ hoặc trỏ repo vào thư mục này
# Trước đó sửa CONFIG trong index.html (game) và chat/index.html:
#   BRAND      tên thương hiệu đã chốt
#   ENDPOINT   URL Apps Script
#   SPLIT_A    0.5
```

Gắn UTM cho từng kênh để `#results`/Sheet tách được nguồn, ví dụ:

```
https://domain.vn/?utm_source=facebook&utm_medium=group&utm_campaign=seller-shopee#a
https://domain.vn/?utm_source=tiktok&utm_medium=video&utm_campaign=bao-cao-thang#b
```

Hậu tố `#a`/`#b` ép biến thể cho chiến dịch nào muốn thông điệp cố định; bỏ hậu tố để trang tự chia 50/50.

Link giới thiệu sau khi đăng ký có dạng `?ref=<mã>`; `ref` được ghi cùng phiên và waitlist để đo vòng lặp giới thiệu.

## Checklist trước khi chạy quảng cáo

- [ ] Chốt tên thương hiệu, thay `CONFIG.BRAND`, `<title>`, email liên hệ ở footer
- [ ] Trang Điều khoản + Chính sách bảo mật (Nghị định 13/2023), link ở footer
- [ ] Apps Script đã triển khai, test một submit thật thấy dòng trong Sheet
- [ ] Favicon, ảnh OG (1200×630) cho khi chia sẻ lên Facebook/Zalo
- [ ] Thử trên điện thoại: thả file từ Zalo/Drive vào được
- [ ] Ngân sách thử: 2 tuần, 2 kênh, mỗi biến thể tối thiểu 1.000 lượt xem
