"""core.i18n: ngôn ngữ request (Accept-Language), L(), nhận diện ngôn ngữ nội dung cho prompt AI."""
from core.i18n import L, detect_lang, get_lang, normalize_lang, output_language_rule, use_lang


def test_normalize_and_use_lang():
    assert normalize_lang("en-US,en;q=0.9") == "en" and normalize_lang("vi") == "vi" and normalize_lang(None) == "vi"
    with use_lang("en"):
        assert get_lang() == "en" and L("Có", "Yes") == "Yes"
    assert L("Có", "Yes") == "Có"


def test_detect_lang_from_content():
    assert detect_lang("Site Summary, Country Name, Protocol #, Principal Investigator Name, SDV Backlog") == "en"
    assert detect_lang("Điều 5. Thời hạn đổi trả theo ngành hàng được quy định khác nhau") == "vi"
    assert detect_lang("PRJ7718", default="en") == "en"  # quá ít chữ, không dấu -> mặc định
    assert detect_lang("có giá", default="en") == "vi"  # nhãn ngắn có dấu tiếng Việt
    # dòng Excel tiếng Anh được lưu theo khuôn "Cột là giá trị" -> vẫn là tiếng Anh
    assert detect_lang("Region là Sector A, Country Name là Country 001, Protocol # là PRJ3977, Site # là STE8701") == "en"


def test_output_language_rule_names_the_language():
    assert "write every free-text value in English" in output_language_rule("en")
