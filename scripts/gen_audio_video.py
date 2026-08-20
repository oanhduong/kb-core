"""Sinh 3 file audio (.mp3, TTS that qua gTTS, kich ban training + call center, 1-3 phut) va
2 file video (.mp4, 2-5 phut, co audio huong dan quy trinh quay thu ngan) cho test corpus.

Neu gTTS loi (mang, rate-limit...) se thu lai pyttsx3 (offline, giong he thong) va IN RO ra
console viec da fallback - khong duoc am tham bo qua theo yeu cau cua de bai.
"""
import os
import subprocess
import time

OUT_AUDIO = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "audio")
OUT_VIDEO = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "video")
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# ffmpeg thuong (brew "ffmpeg") khong bien dich freetype -> thieu drawtext.
# Dung ffmpeg-full (co drawtext) qua duong dan tuyet doi, khong dong PATH he thong.
FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFMPEG = FFMPEG_FULL if os.path.exists(FFMPEG_FULL) else "ffmpeg"
FFPROBE_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
FFPROBE = FFPROBE_FULL if os.path.exists(FFPROBE_FULL) else "ffprobe"


def _tts_to_mp3(text, out_path, retries=3):
    """Uu tien gTTS (that, tu nhien). Neu that bai het retries -> fallback pyttsx3 va IN RO canh bao."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            from gtts import gTTS
            gTTS(text=text, lang="vi").save(out_path)
            return "gTTS"
        except Exception as e:
            last_err = e
            print(f"  [CẢNH BÁO] gTTS lỗi (lần {attempt}/{retries}): {e}")
            time.sleep(2)
    print(f"  [CẢNH BÁO] gTTS thất bại sau {retries} lần thử -> fallback sang pyttsx3 (giọng hệ thống, offline).")
    try:
        import pyttsx3
        engine = pyttsx3.init()
        for v in engine.getProperty("voices"):
            if "vi_VN" in v.id or "vi-VN" in v.id:
                engine.setProperty("voice", v.id)
                break
        aiff = out_path.replace(".mp3", "_tmp.aiff")
        engine.save_to_file(text, aiff)
        engine.runAndWait()
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", aiff, "-codec:a", "libmp3lame",
                         "-qscale:a", "4", out_path], check=True)
        os.remove(aiff)
        return "pyttsx3"
    except Exception as e2:
        raise RuntimeError(
            f"CẢ gTTS lẫn pyttsx3 đều thất bại. gTTS: {last_err} | pyttsx3: {e2}. "
            "Không thể sinh file audio này — dừng lại thay vì bỏ qua âm thầm."
        )


def _ffprobe_duration(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# =====================================================================================
# 3 FILE AUDIO: 2 training nhan vien + 1 call center, 1-3 phut, TTS that
# =====================================================================================
AUDIO_SCRIPTS = {
    "01_training_chao_don_khach_hang.mp3": (
        "Xin chào các bạn nhân viên mới của GreenMart Việt Nam. Bài đào tạo hôm nay là quy trình chào đón "
        "khách hàng tại cửa hàng, một trong những kỹ năng quan trọng nhất mà mọi nhân viên bán hàng cần nắm "
        "vững. Bước một, khi khách hàng bước vào cửa, nhân viên đứng gần khu vực cửa ra vào cần chủ động mỉm "
        "cười và nói xin chào quý khách, rất vui được đón tiếp quý khách tại GreenMart. Lời chào cần được nói "
        "rõ ràng, thân thiện, không quá lớn tiếng nhưng đủ để khách hàng nghe thấy. Bước hai, quan sát nhu cầu "
        "của khách hàng. Nếu khách hàng có vẻ đang tìm kiếm một sản phẩm cụ thể, hãy chủ động lại gần và hỏi "
        "thăm quý khách cần tìm sản phẩm gì để em hỗ trợ ạ. Nếu khách hàng chỉ đang tham quan, nhân viên không "
        "nên bám theo quá sát mà chỉ cần đứng ở khoảng cách vừa phải, sẵn sàng hỗ trợ khi được yêu cầu. Bước "
        "ba, trong suốt quá trình khách hàng mua sắm, nhân viên luôn giữ thái độ niềm nở, kiên nhẫn trả lời "
        "mọi câu hỏi về sản phẩm, giá cả hoặc chương trình khuyến mãi đang áp dụng. Tuyệt đối không được tỏ ra "
        "khó chịu ngay cả khi cửa hàng đông khách hoặc khách hàng hỏi nhiều lần về cùng một vấn đề. Bước bốn, "
        "khi khách hàng tiến đến quầy thanh toán, nhân viên thu ngân tiếp tục duy trì thái độ niềm nở, thao "
        "tác nhanh gọn và chính xác. Bước năm, khi khách hàng rời đi, dù có mua hàng hay không, nhân viên đều "
        "cần cảm ơn và chào tạm biệt, hẹn gặp lại quý khách lần sau. Đây là quy trình bắt buộc áp dụng cho tất "
        "cả nhân viên bán hàng tại toàn bộ hệ thống cửa hàng, được xem là một tiêu chí quan trọng trong đánh "
        "giá chất lượng phục vụ hằng tháng."
    ),
    "02_training_xu_ly_doi_tra_tai_quay.mp3": (
        "Bài đào tạo hôm nay là xử lý tình huống đổi trả hàng hóa tại quầy thu ngân, một tình huống mà hầu "
        "như ngày nào cửa hàng cũng gặp phải. Khi khách hàng mang sản phẩm đến quầy yêu cầu đổi trả, nhân "
        "viên cần thực hiện tuần tự các bước sau đây. Bước một, kiểm tra hóa đơn mua hàng hoặc tra cứu mã đơn "
        "hàng trên hệ thống, đồng thời kiểm tra tình trạng thực tế của sản phẩm, xem còn nguyên tem nhãn, bao "
        "bì gốc và chưa qua sử dụng hay không. Bước hai, đối chiếu với chính sách đổi trả theo từng ngành "
        "hàng. Ví dụ, hàng điện máy được đổi mới trong vòng ba mươi ngày nếu là lỗi nhà sản xuất, trong khi "
        "hàng tiêu dùng nhanh chỉ có bảy ngày. Nhân viên cần nhớ chính xác các mốc thời gian này để tránh xử "
        "lý sai. Bước ba, nếu sản phẩm đủ điều kiện, nhân viên lập phiếu đổi trả trên hệ thống point of sale, "
        "chọn đúng lý do đổi trả tương ứng, sau đó tiến hành hoàn tiền theo hình thức thanh toán ban đầu hoặc "
        "đổi sang sản phẩm khác theo yêu cầu của khách hàng. Bước bốn, nếu sản phẩm không đủ điều kiện đổi "
        "trả, nhân viên cần giải thích rõ ràng, lịch sự lý do từ chối, dựa trên chính sách cụ thể, không được "
        "nói chung chung kiểu công ty không cho phép. Bước năm, nếu khách hàng không đồng ý với quyết định và "
        "tỏ ra bức xúc, nhân viên tuyệt đối không tranh cãi trực tiếp mà cần mời quản lý ca trực ra hỗ trợ "
        "ngay lập tức. Quản lý ca trực có thẩm quyền xem xét các trường hợp ngoại lệ nằm ngoài quy định thông "
        "thường. Cuối cùng, xin nhắc lại nguyên tắc quan trọng nhất: thái độ nhã nhặn, kiên nhẫn và tôn trọng "
        "khách hàng luôn phải được đặt lên hàng đầu, ngay cả trong những tình huống căng thẳng nhất."
    ),
    "03_callcenter_hoi_bao_hanh_san_pham.mp3": (
        "Tổng đài viên: Xin chào, tổng đài chăm sóc khách hàng GreenMart Việt Nam xin nghe ạ, em có thể hỗ "
        "trợ gì cho anh chị ạ. Khách hàng: Chào bạn, tôi mua cái tủ lạnh ở chi nhánh Cầu Giấy được ba tháng "
        "rồi mà bây giờ nó kêu to quá, tôi muốn hỏi về bảo hành. Tổng đài viên: Dạ em hiểu rồi ạ, anh chị vui "
        "lòng cho em xin số điện thoại hoặc số hóa đơn mua hàng để em kiểm tra thông tin bảo hành ạ. Khách "
        "hàng: Số điện thoại của tôi là không chín không, một hai ba, bốn năm sáu bảy. Tổng đài viên: Dạ em "
        "xin phép kiểm tra trên hệ thống một chút ạ... Dạ em đã kiểm tra thấy sản phẩm là tủ lạnh Inverter ba "
        "trăm lít, mua ngày mười lăm tháng năm, còn trong thời hạn bảo hành hai mươi bốn tháng ạ. Anh chị mô "
        "tả kỹ hơn giúp em là tiếng kêu phát ra từ vị trí nào và kêu liên tục hay chỉ kêu vào một số thời "
        "điểm trong ngày ạ. Khách hàng: Nó kêu ở phía sau lưng tủ, kêu liên tục cả ngày, hơi ồn, ảnh hưởng đến "
        "sinh hoạt gia đình. Tổng đài viên: Dạ em ghi nhận thông tin rồi ạ. Đây có thể là lỗi liên quan đến "
        "quạt gió hoặc máy nén, cần kỹ thuật viên đến kiểm tra trực tiếp ạ. Em sẽ tạo phiếu tiếp nhận bảo hành "
        "ngay bây giờ, kỹ thuật viên sẽ liên hệ anh chị trong vòng hai mươi bốn giờ để hẹn lịch kiểm tra tại "
        "nhà ạ. Khách hàng: Vậy có mất phí gì không bạn. Tổng đài viên: Dạ sản phẩm còn trong thời hạn bảo "
        "hành và lỗi được xác định là lỗi kỹ thuật thông thường thì hoàn toàn miễn phí kiểm tra và sửa chữa ạ, "
        "trừ trường hợp phát hiện lỗi do tác động bên ngoài thì sẽ báo giá trước khi sửa. Khách hàng: Vậy cảm "
        "ơn bạn nhiều nhé. Tổng đài viên: Dạ không có gì ạ, cảm ơn anh chị đã tin tưởng và sử dụng sản phẩm "
        "của GreenMart, chúc anh chị một ngày tốt lành ạ, tạm biệt anh chị."
    ),
}


# =====================================================================================
# 2 FILE VIDEO: huong dan quy trinh quay thu ngan, 2-5 phut, co audio
# =====================================================================================
VIDEO_SCRIPTS = {
    "01_video_quy_trinh_thanh_toan_quay_thu_ngan.mp4": {
        "bg_color": "0x1F6F43",
        "narration": (
            "Video hướng dẫn nội bộ: quy trình thanh toán tại quầy thu ngân, dành cho nhân viên mới tại hệ "
            "thống GreenMart Việt Nam. Đây là quy trình bắt buộc, áp dụng thống nhất tại tất cả các chi nhánh. "
            "Bước một, chào đón khách hàng. Khi khách hàng đặt sản phẩm lên quầy, nhân viên thu ngân chào "
            "khách hàng bằng nụ cười và một câu chào thân thiện trước khi bắt đầu thao tác. "
            "Bước hai, quét mã vạch từng sản phẩm. Nhân viên quét lần lượt từng sản phẩm, kiểm tra kỹ số "
            "lượng và tên sản phẩm hiển thị trên màn hình có khớp với hàng thực tế hay không, tránh quét "
            "nhầm hoặc quét thiếu. "
            "Bước ba, thông báo tổng số tiền. Sau khi quét xong toàn bộ sản phẩm, nhân viên đọc rõ tổng số "
            "tiền cho khách hàng nghe trước khi tiến hành thanh toán, đồng thời hỏi khách hàng có sử dụng thẻ "
            "thành viên hoặc mã giảm giá hay không. "
            "Bước bốn, lựa chọn hình thức thanh toán. Khách hàng có thể thanh toán bằng tiền mặt, thẻ ngân "
            "hàng hoặc ví điện tử. Với thanh toán tiền mặt, nhân viên phải đếm tiền thối lại trước mặt khách "
            "hàng để tránh nhầm lẫn. Với thanh toán thẻ, chờ máy quẹt thẻ báo giao dịch thành công mới được "
            "giao hàng cho khách. "
            "Bước năm, in hóa đơn và tích điểm. Nhân viên in hóa đơn, quét mã tích điểm nếu khách hàng có thẻ "
            "thành viên, sau đó gửi hóa đơn kèm lời cảm ơn cho khách hàng. "
            "Bước sáu, kết thúc giao dịch. Nhân viên chào tạm biệt khách hàng, sắp xếp lại khu vực quầy gọn "
            "gàng để sẵn sàng phục vụ khách hàng tiếp theo. "
            "Lưu ý quan trọng: trong suốt quá trình thanh toán, nhân viên tuyệt đối không được rời khỏi quầy "
            "khi giao dịch chưa hoàn tất, và luôn giữ thái độ chuyên nghiệp, nhanh nhẹn để giảm thời gian chờ "
            "đợi cho khách hàng phía sau. "
            "Một số tình huống thường gặp cần lưu ý thêm. Thứ nhất, nếu khách hàng thanh toán bằng tiền mặt "
            "với mệnh giá lớn, nhân viên nên kiểm tra tiền giả bằng đèn chiếu chuyên dụng trước khi cất vào "
            "ngăn kéo, đặc biệt với các tờ tiền mệnh giá từ năm trăm nghìn đồng trở lên. Thứ hai, nếu khách "
            "hàng có nhiều mã giảm giá cùng lúc, nhân viên cần kiểm tra kỹ điều kiện áp dụng của từng mã trên "
            "hệ thống, tránh áp dụng chồng chéo sai quy định gây thất thoát cho cửa hàng. Thứ ba, nếu khách "
            "hàng mua số lượng lớn hàng hóa cồng kềnh, nhân viên nên chủ động gọi hỗ trợ từ nhân viên đóng gói "
            "hoặc nhân viên kho để hỗ trợ đóng gói và vận chuyển ra xe cho khách hàng. Cuối cùng, vào những "
            "khung giờ cao điểm khi quầy thu ngân đông khách xếp hàng, quản lý ca trực có trách nhiệm điều "
            "phối mở thêm quầy dự phòng để giảm thời gian chờ đợi, bảo đảm trải nghiệm mua sắm tốt nhất cho "
            "khách hàng. Toàn bộ quy trình này sẽ được kiểm tra định kỳ hằng tháng thông qua hình thức khách "
            "hàng bí mật để đánh giá chất lượng phục vụ của từng cửa hàng trong hệ thống."
        ),
        "steps": [
            "Bước 1: Chào đón khách hàng tại quầy",
            "Bước 2: Quét mã vạch từng sản phẩm",
            "Bước 3: Thông báo tổng số tiền",
            "Bước 4: Chọn hình thức thanh toán",
            "Bước 5: In hóa đơn và tích điểm",
            "Bước 6: Kết thúc giao dịch, chào tạm biệt",
        ],
    },
    "02_video_xu_ly_loi_pos_hoan_tien.mp4": {
        "bg_color": "0xC81414",
        "narration": (
            "Video hướng dẫn nội bộ: xử lý lỗi máy POS và quy trình hoàn tiền tại quầy thu ngân, dành cho "
            "nhân viên thu ngân hệ thống GreenMart Việt Nam. "
            "Phần một, xử lý khi máy POS báo lỗi giao dịch. Nếu máy POS báo lỗi ngay sau khi quẹt thẻ hoặc "
            "chuyển khoản, nhân viên tuyệt đối không được tính tiền lần thứ hai ngay lập tức. Trước tiên, "
            "kiểm tra lại trạng thái giao dịch trên hệ thống quản lý để xác định giao dịch đã thành công hay "
            "chưa, tránh trường hợp khách hàng bị trừ tiền hai lần. Nếu không chắc chắn, gọi ngay cho bộ phận "
            "kỹ thuật hỗ trợ qua số nội bộ một không không để được xác nhận trước khi thực hiện lại giao dịch. "
            "Phần hai, quy trình hoàn tiền khi khách hàng đổi trả hàng. Khi khách hàng mang hàng đến đổi trả "
            "và cần hoàn tiền, nhân viên chọn chức năng hoàn tiền trên hệ thống, nhập chính xác mã hóa đơn "
            "gốc, sau đó chọn lý do hoàn tiền tương ứng, ví dụ hàng lỗi nhà sản xuất hoặc khách hàng đổi ý "
            "trong thời hạn cho phép. "
            "Phần ba, xác nhận số tiền hoàn lại. Trước khi bấm xác nhận hoàn tiền, nhân viên phải đọc to số "
            "tiền sẽ hoàn lại cho khách hàng nghe và xác nhận lại một lần nữa, đặc biệt với hình thức hoàn "
            "tiền qua thẻ ngân hàng cần thông báo rõ thời gian tiền về tài khoản có thể mất từ ba đến năm "
            "ngày làm việc. "
            "Phần bốn, xử lý khi hệ thống gặp sự cố kéo dài. Nếu máy POS hoặc hệ thống mạng gặp sự cố kéo dài "
            "hơn mười lăm phút, nhân viên thông báo ngay cho quản lý ca trực, đồng thời hướng dẫn khách hàng "
            "chuyển sang quầy khác đang hoạt động bình thường nếu cửa hàng có nhiều quầy thu ngân. "
            "Ghi nhớ nguyên tắc cuối cùng: mọi sai sót liên quan đến tiền bạc đều phải được ghi chép lại đầy "
            "đủ vào sổ theo dõi sự cố cuối ca, làm cơ sở đối chiếu và báo cáo cho quản lý cửa hàng. "
            "Ngoài các tình huống trên, nhân viên cần lưu ý thêm một số trường hợp đặc biệt. Nếu khách hàng "
            "yêu cầu hoàn tiền cho đơn hàng thanh toán bằng ví điện tử, nhân viên cần thao tác hoàn tiền ngay "
            "trên chính kênh thanh toán gốc đó, không được quy đổi sang tiền mặt trừ khi có sự phê duyệt đặc "
            "biệt từ quản lý khu vực. Nếu hệ thống báo lỗi không thể xác định được giao dịch gốc do sự cố "
            "đồng bộ dữ liệu, nhân viên tuyệt đối không tự ý ước lượng số tiền hoàn lại mà phải yêu cầu khách "
            "hàng cung cấp thêm bằng chứng như tin nhắn xác nhận giao dịch hoặc sao kê ngân hàng để đối chiếu "
            "chính xác trước khi xử lý. Đối với các giao dịch có giá trị hoàn tiền lớn, cụ thể là từ năm triệu "
            "đồng trở lên, quy trình bắt buộc phải có xác nhận bằng chữ ký của quản lý ca trực trên phiếu hoàn "
            "tiền trước khi giao dịch được xử lý trên hệ thống, nhằm hạn chế tối đa rủi ro gian lận nội bộ. "
            "Toàn bộ quy trình xử lý lỗi và hoàn tiền này sẽ được đào tạo lại định kỳ mỗi quý để cập nhật theo "
            "những thay đổi mới nhất của hệ thống point of sale và chính sách công ty."
        ),
        "steps": [
            "Phần 1: Xử lý khi máy POS báo lỗi giao dịch",
            "Phần 2: Quy trình hoàn tiền khi đổi trả hàng",
            "Phần 3: Xác nhận số tiền hoàn lại với khách",
            "Phần 4: Xử lý khi hệ thống gặp sự cố kéo dài",
        ],
    },
}


def gen_audio():
    os.makedirs(OUT_AUDIO, exist_ok=True)
    results = {}
    for filename, text in AUDIO_SCRIPTS.items():
        out_path = os.path.join(OUT_AUDIO, filename)
        engine = _tts_to_mp3(text, out_path)
        duration = _ffprobe_duration(out_path)
        results[filename] = {"engine": engine, "duration_seconds": duration, "script_text": text}
        print(f"  {filename}: {duration:.1f}s ({duration/60:.1f} phút) qua {engine}")
    return results


def _escape_drawtext(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def gen_video():
    os.makedirs(OUT_VIDEO, exist_ok=True)
    results = {}
    for filename, spec in VIDEO_SCRIPTS.items():
        narration_mp3 = os.path.join(OUT_VIDEO, f"_narration_{filename}.mp3")
        engine = _tts_to_mp3(spec["narration"], narration_mp3)
        duration = _ffprobe_duration(narration_mp3)

        steps = spec["steps"]
        seg = duration / len(steps)
        filters = [
            "drawtext=text='GreenMart Việt Nam':fontfile=" + FONT.replace(" ", "\\ ") +
            ":fontsize=50:fontcolor=white:x=(w-text_w)/2:y=90"
        ]
        for i, step in enumerate(steps):
            t0, t1 = i * seg, (i + 1) * seg
            txt = _escape_drawtext(step)
            filters.append(
                f"drawtext=text='{txt}':fontfile={FONT.replace(' ', chr(92)+' ')}:fontsize=38:fontcolor=white:"
                f"x=(w-text_w)/2:y=340:enable='between(t,{t0:.2f},{t1:.2f})'"
            )
        vf = ",".join(filters)
        out_path = os.path.join(OUT_VIDEO, filename)
        subprocess.run([
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={spec['bg_color']}:s=1280x720:d={duration:.2f}",
            "-i", narration_mp3,
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out_path,
        ], check=True)
        os.remove(narration_mp3)

        real_duration = _ffprobe_duration(out_path)
        results[filename] = {"engine": engine, "duration_seconds": real_duration, "script_text": spec["narration"]}
        print(f"  {filename}: {real_duration:.1f}s ({real_duration/60:.1f} phút) qua {engine}")
    return results


if __name__ == "__main__":
    print("Sinh 3 file audio (gTTS)...")
    audio_meta = gen_audio()
    print("Sinh 2 file video (gTTS narration + ffmpeg)...")
    video_meta = gen_video()
    print("Done.")
