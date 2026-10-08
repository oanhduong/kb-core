"""Unit test qa/citation.py (B2): formatter theo loại nguồn, post-check bằng code, gate khi stream."""
from dataclasses import dataclass

import pytest

from qa.citation import StreamingCitationGate, display_title, format_citation, marker_ids, postcheck, split_sentences


@pytest.mark.parametrize("title,stype,loc,expected", [
    ("CHÍNH SÁCH ĐỔI TRẢ", "docx", "CHÍNH SÁCH ĐỔI TRẢ > CHƯƠNG II > Điều 5. Thời hạn đổi trả", "chính sách đổi trả, Điều 5"),
    ("Chính Sách Đổi Trả Hàng Hóa", "docx", "CHƯƠNG II > Điều 5. Thời hạn", "chính sách đổi trả hàng hóa, Điều 5"),
    ("CHÍNH SÁCH ĐỔI TRẢ", "docx", "CHÍNH SÁCH ĐỔI TRẢ > CHƯƠNG II > Điều 5. Thời hạn > Bảng 1", "chính sách đổi trả, Điều 5"),
    ("Bảng giá tháng 8", "xlsx", "HCM01!A42", "bảng giá tháng 8, Sheet HCM01 dòng 42"),
    ("Bảng giá tháng 8", "xlsx", "HCM01!A5:D5", "bảng giá tháng 8, Sheet HCM01 dòng 5"),
    ("Video hướng dẫn thu ngân", "video", "00:02:15-00:02:48", "video hướng dẫn thu ngân, 02:15"),
    ("Training chào khách", "audio", "01:03:05", "training chào khách, 1:03:05"),
    ("Poster khuyến mãi tháng 7", "image", "01_poster.png", "poster khuyến mãi tháng 7"),
    ("Catalogue đồ gia dụng", "pdf", "Trang 3 > Bảng 2", "catalogue đồ gia dụng, trang 3, bảng 2"),
])
def test_format_citation_by_source_type(title, stype, loc, expected):
    assert format_citation(title, stype, loc) == expected


def test_display_title_keeps_proper_case_but_lowers_all_caps():
    assert display_title("CHÍNH SÁCH BẢO HÀNH") == "chính sách bảo hành"
    assert display_title("Bảng giá GreenMart") == "bảng giá GreenMart"


@dataclass
class Src:
    label: str
    chunk_id: str = "c"
    record_id: str = "r"
    document_id: str = "d"
    location: str = "loc"
    text: str = "nội dung"
    effective_date: str | None = None


SOURCES = {"S1": Src("chính sách đổi trả, Điều 5", "c1", "r1"), "S2": Src("bảng giá tháng 8, Sheet HCM01 dòng 3", "c2", "r2")}


def test_marker_ids_handles_grouped_markers():
    assert marker_ids("a [S1] b [S2, S3] c [S4;S5]") == ["S1", "S2", "S3", "S4", "S5"]


def test_split_sentences_keeps_decimal_numbers_and_attaches_trailing_markers():
    sents = split_sentences("Giá là 1.440.000 đồng. [S2]\nThời hạn đổi trả 7 ngày [S1].")
    assert sents == ["Giá là 1.440.000 đồng. [S2]", "Thời hạn đổi trả 7 ngày [S1]."]


def test_postcheck_renumbers_in_order_and_drops_invented_markers():
    ans = "Hàng điện tử được đổi trong 7 ngày [S1]. Giá loa là 1.440.000đ [S2][S9]. Theo quy định [S1]."
    pc = postcheck(ans, SOURCES)
    assert pc.ok
    assert pc.answer == "Hàng điện tử được đổi trong 7 ngày [1]. Giá loa là 1.440.000đ [2]. Theo quy định [1]."
    assert [c.n for c in pc.citations] == [1, 2] and pc.citations[1].label.startswith("bảng giá")
    assert pc.invalid_sids == ["S9"]


def test_postcheck_rejects_answer_without_any_valid_citation():
    pc = postcheck("Thời hạn đổi trả là 30 ngày cho mọi mặt hàng [S7].", SOURCES, insufficient_message="KHÔNG ĐỦ")
    assert not pc.ok and pc.answer == "KHÔNG ĐỦ" and pc.citations == [] and pc.reason == "no_valid_citation"


def test_postcheck_enforces_sentence_coverage_when_configured():
    ans = "Hàng điện tử đổi trong 7 ngày [S1]. Hàng thực phẩm tươi không được đổi trả trong mọi trường hợp."
    assert postcheck(ans, SOURCES, min_coverage=0.0).ok
    pc = postcheck(ans, SOURCES, min_coverage=0.8, insufficient_message="X")
    assert not pc.ok and pc.reason.startswith("coverage_0.50")


def test_postcheck_does_not_count_greetings_as_claims():
    pc = postcheck("Chào bạn!\nThời hạn đổi trả là 7 ngày [S1].", SOURCES, min_coverage=1.0)
    assert pc.ok and pc.claim_count == 1


def test_stream_gate_holds_text_until_first_valid_citation_and_renumbers():
    g = StreamingCitationGate({"S1", "S2"})
    out = [g.feed("Thời hạn đổi "), g.feed("trả là 7 ngày [S"), g.feed("2]. Giá "), g.feed("là 5 [S1]."), g.flush()]
    assert out[0] == "" and out[1] == ""  # chưa có căn cứ -> chưa gửi gì
    assert "".join(out) == "Thời hạn đổi trả là 7 ngày [1]. Giá là 5 [2]."
    # numbering khớp postcheck trên toàn văn
    assert postcheck("Thời hạn đổi trả là 7 ngày [S2]. Giá là 5 [S1].", {"S1": SOURCES["S1"], "S2": SOURCES["S2"]}).answer == g.emitted


def test_stream_gate_never_opens_without_citation():
    g = StreamingCitationGate({"S1"})
    assert g.feed("Câu trả lời không có nguồn nào cả. ") == "" and g.feed("[S5] bịa.") == "" and g.flush() == ""
    assert not g.opened
