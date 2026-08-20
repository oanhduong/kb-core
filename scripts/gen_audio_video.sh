#!/bin/bash
# Sinh 3 file audio (giong doc that qua macOS `say`, giong Linh vi_VN) va 2 file video (ffmpeg)
# mo phong: ghi am training nhan vien, ghi am tong dai, video huong dan quay thu ngan.
set -euo pipefail
cd "$(dirname "$0")/.."

AUDIO_DIR="data/raw/audio"
VIDEO_DIR="data/raw/video"
TMP_DIR=$(mktemp -d)
VOICE="Linh"

mkdir -p "$AUDIO_DIR" "$VIDEO_DIR"

say_to_mp3() {
  local text="$1"
  local out="$2"
  local aiff="$TMP_DIR/$(basename "$out" .mp3).aiff"
  say -v "$VOICE" -o "$aiff" "$text"
  ffmpeg -y -loglevel error -i "$aiff" -codec:a libmp3lame -qscale:a 4 "$out"
}

# --- 1) Training nhan vien: Quy trinh chao don khach hang ---
TEXT1="Xin chào các bạn nhân viên mới của GreenMart Việt Nam. Hôm nay chúng ta sẽ học quy trình chào đón khách hàng tại cửa hàng. Bước một, khi khách hàng bước vào cửa, nhân viên đứng gần cửa cần mỉm cười và nói xin chào quý khách, rất vui được đón tiếp quý khách tại GreenMart. Bước hai, quan sát nhu cầu của khách hàng, nếu khách hàng có vẻ đang tìm kiếm sản phẩm, hãy chủ động hỏi thăm quý khách cần tìm sản phẩm gì để em hỗ trợ ạ. Bước ba, luôn giữ thái độ niềm nở, không được tỏ ra khó chịu ngay cả khi cửa hàng đông khách. Bước bốn, khi khách hàng rời đi, hãy cảm ơn và chào tạm biệt, hẹn gặp lại quý khách lần sau. Đây là quy trình bắt buộc áp dụng cho tất cả nhân viên bán hàng tại toàn hệ thống."
say_to_mp3 "$TEXT1" "$AUDIO_DIR/01_training_chao_don_khach_hang.mp3"

# --- 2) Training nhan vien: Xu ly tinh huong doi tra tai quay ---
TEXT2="Bài học hôm nay là xử lý tình huống đổi trả hàng tại quầy thu ngân. Khi khách hàng mang sản phẩm đến yêu cầu đổi trả, nhân viên cần thực hiện các bước sau. Thứ nhất, kiểm tra hóa đơn mua hàng và tình trạng sản phẩm, hàng còn tem nhãn và chưa qua sử dụng. Thứ hai, đối chiếu với chính sách đổi trả theo ngành hàng, ví dụ hàng điện máy được đổi mới trong bảy ngày nếu lỗi nhà sản xuất. Thứ ba, nếu đủ điều kiện, lập phiếu đổi trả trên hệ thống point of sale và chọn lý do phù hợp. Thứ tư, nếu khách hàng không đồng ý với quyết định, hãy mời quản lý ca trực ra hỗ trợ, tuyệt đối không tranh cãi trực tiếp với khách hàng. Ghi nhớ, thái độ nhã nhặn luôn là ưu tiên hàng đầu."
say_to_mp3 "$TEXT2" "$AUDIO_DIR/02_training_xu_ly_doi_tra_tai_quay.mp3"

# --- 3) Call center: Khach hang hoi ve bao hanh san pham ---
TEXT3="Tổng đài viên: Xin chào, tổng đài chăm sóc khách hàng GreenMart Việt Nam xin nghe ạ. Khách hàng: Chào bạn, tôi mua cái tủ lạnh ở chi nhánh Cầu Giấy được ba tháng rồi mà bây giờ nó kêu to quá, tôi muốn hỏi về bảo hành. Tổng đài viên: Dạ em hiểu rồi ạ, anh chị vui lòng cho em xin số điện thoại hoặc số hóa đơn mua hàng để em kiểm tra thông tin bảo hành ạ. Khách hàng: Số điện thoại của tôi là không chín không, một hai ba, bốn năm sáu bảy. Tổng đài viên: Dạ em đã kiểm tra thấy sản phẩm còn trong thời hạn bảo hành hai mươi bốn tháng ạ. Em sẽ tạo phiếu tiếp nhận bảo hành và kỹ thuật viên sẽ liên hệ anh chị trong vòng hai mươi bốn giờ để hẹn lịch kiểm tra tại nhà hoặc mang ra chi nhánh gần nhất ạ. Khách hàng: Vậy cảm ơn bạn nhiều. Tổng đài viên: Dạ không có gì ạ, cảm ơn anh chị đã tin tưởng GreenMart, chúc anh chị một ngày tốt lành ạ."
say_to_mp3 "$TEXT3" "$AUDIO_DIR/03_callcenter_hoi_bao_hanh_san_pham.mp3"

# --- Video 1: Quy trinh thanh toan tai quay thu ngan ---
VTEXT1="Video hướng dẫn quy trình thanh toán tại quầy thu ngân. Bước một, quét mã vạch từng sản phẩm và kiểm tra số lượng hiển thị trên màn hình. Bước hai, thông báo tổng số tiền cho khách hàng trước khi thanh toán. Bước ba, chọn hình thức thanh toán, tiền mặt, thẻ ngân hàng hoặc ví điện tử. Bước bốn, in hóa đơn và gửi khách hàng kèm lời cảm ơn. Bước năm, nếu khách hàng có thẻ thành viên, quét mã tích điểm trước khi kết thúc giao dịch."
say -v "$VOICE" -o "$TMP_DIR/v1.aiff" "$VTEXT1"
ffmpeg -y -loglevel error -f lavfi -i "color=c=0x1F6F43:s=1280x720:d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TMP_DIR/v1.aiff" 2>/dev/null || echo 30)" \
  -i "$TMP_DIR/v1.aiff" \
  -vf "drawtext=text='GreenMart Viet Nam':fontfile=/System/Library/Fonts/Supplemental/Arial\\ Bold.ttf:fontsize=54:fontcolor=white:x=(w-text_w)/2:y=120,drawtext=text='Quy trinh thanh toan tai quay thu ngan':fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:fontsize=36:fontcolor=white:x=(w-text_w)/2:y=320,drawtext=text='(Video huong dan noi bo - dao tao nhan vien)':fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:fontsize=24:fontcolor=0xEFEFEF:x=(w-text_w)/2:y=380" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$VIDEO_DIR/01_video_quy_trinh_thanh_toan_quay_thu_ngan.mp4"

# --- Video 2: Xu ly khi may POS loi / hoan tien tai quay ---
VTEXT2="Video hướng dẫn xử lý khi máy pos gặp lỗi hoặc cần hoàn tiền tại quầy thu ngân. Bước một, nếu máy pos báo lỗi giao dịch, nhân viên không được tính tiền hai lần, hãy kiểm tra lại trạng thái giao dịch trên hệ thống trước. Bước hai, nếu cần hoàn tiền do đổi trả hàng, chọn chức năng hoàn tiền trên hệ thống, nhập mã hóa đơn gốc và lý do hoàn tiền. Bước ba, xác nhận số tiền hoàn lại với khách hàng trước khi thực hiện. Bước bốn, nếu lỗi hệ thống kéo dài, gọi ngay cho bộ phận kỹ thuật hỗ trợ qua số nội bộ một không không."
say -v "$VOICE" -o "$TMP_DIR/v2.aiff" "$VTEXT2"
ffmpeg -y -loglevel error -f lavfi -i "color=c=0xC81414:s=1280x720:d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TMP_DIR/v2.aiff" 2>/dev/null || echo 30)" \
  -i "$TMP_DIR/v2.aiff" \
  -vf "drawtext=text='GreenMart Viet Nam':fontfile=/System/Library/Fonts/Supplemental/Arial\\ Bold.ttf:fontsize=54:fontcolor=white:x=(w-text_w)/2:y=120,drawtext=text='Xu ly loi may POS va hoan tien tai quay':fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:fontsize=34:fontcolor=white:x=(w-text_w)/2:y=320,drawtext=text='(Video huong dan noi bo - dao tao nhan vien)':fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:fontsize=24:fontcolor=0xEFEFEF:x=(w-text_w)/2:y=380" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$VIDEO_DIR/02_video_xu_ly_loi_pos_hoan_tien.mp4"

rm -rf "$TMP_DIR"
echo "Done: 3 audio files -> $(pwd)/$AUDIO_DIR"
echo "Done: 2 video files -> $(pwd)/$VIDEO_DIR"
