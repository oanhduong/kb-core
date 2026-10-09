"""Unit test dựng lại bảng PDF không kẻ khung từ toạ độ chữ (ingest/parsers.reconstruct_layout_tables) và chia đôi
window trích thực thể khi output model bị cắt (knowledge/extract)."""
from ingest.parsers import reconstruct_layout_tables

COLS = [94.0, 138.0, 265.0, 396.0, 443.0]


def _row(top, cells, cols=COLS):
    return [(cols[i], top, cols[i] + 40, top - 8, text) for i, text in cells]


def _catalogue(top=664.0, step=18.0):
    boxes = [(115.0, 774.0, 480.0, 757.0, "CATALOGUE SẢN PHẨM - ĐỒ GIA DỤNG"),
             (57.0, 717.0, 529.0, 694.0, "GreenMart Việt Nam - Catalogue tham khảo dành cho nhân viên tư vấn.")]
    boxes += _row(top, enumerate(["Mã SP", "Tên sản phẩm", "Thông số chính", "Bảo hành", "Giá tham khảo"]))
    data = [["HGD-001", "Nồi chiên không dầu 6L", "1700W", "12 tháng", "1.590.000đ"],
            ["HGD-002", "Máy xay sinh tố", "Cối 1.5L", "12 tháng", "890.000đ"],
            ["HGD-003", "Bếp từ đôi", "3000W", "18 tháng", "2.490.000đ"]]
    for n, row in enumerate(data, 1):
        boxes += _row(top - n * step, enumerate(row))
    return boxes


def test_borderless_table_is_rebuilt_row_by_row():
    boxes = _catalogue()
    tables, consumed = reconstruct_layout_tables(boxes)
    assert len(tables) == 1
    rows = tables[0]["rows"]
    assert rows[0] == ["Mã SP", "Tên sản phẩm", "Thông số chính", "Bảo hành", "Giá tham khảo"]
    assert rows[3] == ["HGD-003", "Bếp từ đôi", "3000W", "18 tháng", "2.490.000đ"]
    # tiêu đề và đoạn mô tả phía trên không bị nuốt vào bảng
    assert consumed == set(range(2, len(boxes)))


def test_wrapped_cell_is_joined_into_previous_row():
    boxes = _catalogue()
    boxes += _row(664.0 - 3 * 18 - 9, [(1, "(bản cao cấp)")])  # dòng nối tiếp chỉ ở cột 2
    tables, _ = reconstruct_layout_tables(boxes)
    assert tables[0]["rows"][3][1] == "Bếp từ đôi (bản cao cấp)"


def test_paragraph_after_table_ends_it():
    boxes = _catalogue()
    boxes.append((57.0, 664.0 - 4 * 18, 529.0, 664.0 - 4 * 18 - 8, "Ghi chú: giá chưa gồm VAT, áp dụng tới hết quý."))
    tables, consumed = reconstruct_layout_tables(boxes)
    assert len(tables[0]["rows"]) == 4
    assert len(boxes) - 1 not in consumed


def test_large_vertical_gap_splits_tables():
    boxes = _catalogue()
    boxes += _row(664.0 - 3 * 18 - 200, enumerate(["HGD-099", "Xa khỏi bảng", "x", "x", "1đ"]))
    tables, _ = reconstruct_layout_tables(boxes)
    assert all("HGD-099" not in r for r in tables[0]["rows"])


def test_prose_and_two_column_layouts_are_not_tables():
    prose = [(57.0, 700.0 - 12 * i, 529.0, 692.0 - 12 * i, f"Điều {i}. Nội dung hợp đồng dòng {i}.") for i in range(10)]
    assert reconstruct_layout_tables(prose) == ([], set())
    two_cols = []
    for i in range(6):
        two_cols += _row(700.0 - 14 * i, [(0, f"Bên A {i}"), (3, f"Bên B {i}")])
    assert reconstruct_layout_tables(two_cols)[0] == []  # header chỉ 2 ô < 3 cột


def test_header_only_is_not_a_table():
    boxes = _row(664.0, enumerate(["A", "B", "C"])) + _row(646.0, enumerate(["1", "2", "3"]))
    assert reconstruct_layout_tables(boxes)[0] == []  # cần >= 2 dòng dữ liệu


def _price_list(price_box, top=664.0, step=18.0):
    """Mã | Tên | Bảo hành (căn giữa) | Giá (căn phải, số rộng hơn tiêu đề "Giá")."""
    boxes = [(94.0, top, 130.0, top - 8, "Mã SP"), (160.0, top, 250.0, top - 8, "Tên sản phẩm"),
             (300.0, top, 345.0, top - 8, "Bảo hành"), (452.0, top, 470.0, top - 8, "Giá")]
    data = [("HGD-001", "Nồi chiên không dầu 6L", "12 tháng", "1.590.000đ"), ("HGD-002", "Máy xay", "6 tháng", "890.000đ"),
            ("HGD-003", "Bếp từ đôi", "18 tháng", "12.490.000đ")]
    for n, (code, name, warranty, price) in enumerate(data, 1):
        t = top - n * step
        boxes += [(94.0, t, 136.0, t - 8, code), (160.0, t, 160.0 + 5.2 * len(name), t - 8, name),
                  (322.5 - 2.6 * len(warranty), t, 322.5 + 2.6 * len(warranty), t - 8, warranty), price_box(t, price)]
    return boxes


def test_right_aligned_numbers_and_centered_column_stay_in_their_columns():
    # số căn phải theo mép phải tiêu đề "Giá" (470pt): cạnh trái lệch 24–40pt so với cạnh trái tiêu đề
    boxes = _price_list(lambda t, price: (470.0 - 5.0 * len(price), t, 470.0, t - 8, price))
    tables, consumed = reconstruct_layout_tables(boxes)
    assert len(tables) == 1 and len(consumed) == len(boxes)
    assert tables[0]["rows"][1] == ["HGD-001", "Nồi chiên không dầu 6L", "12 tháng", "1.590.000đ"]
    assert tables[0]["rows"][3] == ["HGD-003", "Bếp từ đôi", "18 tháng", "12.490.000đ"]


def test_long_cell_spilling_past_its_header_stays_in_its_column():
    boxes = _price_list(lambda t, price: (452.0, t, 452.0 + 5.0 * len(price), t - 8, price))
    t = 664.0 - 4 * 18
    boxes += [(94.0, t, 136.0, t - 8, "HGD-004"), (160.0, t, 300.0, t - 8, "Máy lọc không khí cao cấp 3 lớp"),
              (310.0, t, 335.0, t - 8, "24 tháng"), (452.0, t, 500.0, t - 8, "4.990.000đ")]
    tables, _ = reconstruct_layout_tables(boxes)
    assert tables[0]["rows"][4] == ["HGD-004", "Máy lọc không khí cao cấp 3 lớp", "24 tháng", "4.990.000đ"]


def test_paragraph_spanning_columns_still_ends_a_price_list():
    boxes = _price_list(lambda t, price: (470.0 - 5.0 * len(price), t, 470.0, t - 8, price))
    boxes.append((57.0, 664.0 - 4 * 18, 529.0, 664.0 - 4 * 18 - 8, "Giá đã gồm VAT, áp dụng tới hết quý."))
    tables, consumed = reconstruct_layout_tables(boxes)
    assert len(tables[0]["rows"]) == 4 and len(boxes) - 1 not in consumed


# ------------------------------------------------------------------ knowledge window split
def test_split_text_prefers_line_boundary_near_middle():
    from knowledge.extract import split_text

    a, b = split_text("dòng một\ndòng hai\ndòng ba\ndòng bốn")
    assert a == "dòng một\ndòng hai" and b == "dòng ba\ndòng bốn"
    x, y = split_text("x" * 10)
    assert x + y == "x" * 10 and len(x) == 5


def test_truncated_window_is_split_and_retried():
    from core.config import load_config
    from core.llm import LLMOutputInvalid
    from knowledge.extract import ExtractOut, Window, _extract_window

    cfg = load_config()
    calls: list[int] = []

    class FakeLLM:
        def complete_json(self, role, *, system, user, schema, max_tokens, max_retries):
            calls.append(len(user))
            assert max_tokens == cfg.knowledge.extract_max_tokens
            if "PHẦN-1" in user and "PHẦN-2" in user:  # cả window: output bị cắt
                raise LLMOutputInvalid("Expecting ',' delimiter")
            name = "Sản phẩm 1" if "PHẦN-1" in user else "Sản phẩm 2"
            return ExtractOut.model_validate({"entities": [{"name": name, "type": "product"}], "relations": []})

    text = "\n".join([f"PHẦN-1 dòng {i}" for i in range(30)] + [f"PHẦN-2 dòng {i}" for i in range(30)])
    res = _extract_window(cfg, FakeLLM(), "sys", "Catalogue", Window("Trang 1", text, ["r1"]))
    assert res.error is None and len(calls) == 3
    assert sorted(e.name for e in res.entities) == ["Sản phẩm 1", "Sản phẩm 2"]
    assert res.window.text == text  # mention/snippet vẫn tham chiếu window gốc


def test_short_window_failure_is_reported_not_split():
    from core.config import load_config
    from core.llm import LLMOutputInvalid
    from knowledge.extract import Window, _extract_window

    class Broken:
        def complete_json(self, *a, **k):
            raise LLMOutputInvalid("bad json")

    res = _extract_window(load_config(), Broken(), "sys", "Doc", Window("Trang 1", "ngắn", ["r1"]))
    assert res.error and not res.entities


# ------------------------------------------------------------------ invariant PDF với bảng dựng lại
def _pdf_result(table_text, text_chunks=("Tiêu đề",), nonspace=None):
    chunks = [{"text": t, "location": "Trang 1", "kind": "text"} for t in text_chunks]
    chunks.append({"text": table_text, "location": "Trang 1 > Bảng 1", "kind": "table", "layout": True})
    cells = [c for row in table_text.split("\n") for c in row.split(" | ")]
    stats = {"chunk_count": len(chunks), "table_count": 1, "character_count": sum(map(len, text_chunks)) + sum(map(len, cells)),
             "layout_table_chars": sum(map(len, cells)),
             "layout_table_nonspace_chars": nonspace if nonspace is not None else sum(len("".join(c.split())) for c in cells)}
    return {"chunks": chunks, "stats": stats}


def test_pdf_invariant_accepts_layout_table_and_catches_dropped_cell(tmp_path):
    import pytest

    from ingest.invariants import ParserError, _check_pdf

    table = "Mã SP | Giá\nHGD-001 | 1.590.000đ\nHGD-002 | 890.000đ"
    ok = _pdf_result(table)
    _check_pdf("f.pdf", ok["chunks"], ok["stats"])
    bad = _pdf_result(table, nonspace=ok["stats"]["layout_table_nonspace_chars"] + len("HGD-003"))  # 1 ô bị rơi
    with pytest.raises(ParserError):
        _check_pdf("f.pdf", bad["chunks"], bad["stats"])
