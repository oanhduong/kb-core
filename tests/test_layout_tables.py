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
