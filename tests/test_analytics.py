"""Unit test analytics: trích bảng từ xlsx mẫu, ép kiểu số kiểu Việt/phần trăm/ngày, DuckDB sandbox + validate SQL,
chart heuristic. Không cần DB Postgres/LLM."""
import os

import pytest
from openpyxl import Workbook

from analytics.charts import build_chart, choose_encoding
from analytics.engine import QueryResult, SQLNotAllowed, TableInfo, column_stats, run_query, validate_sql
from analytics.tables import RawTable, frame_from_raw, parse_number, raw_tables_from_grid_records, raw_tables_from_xlsx, sql_identifier
from core.config import get_config
from core.errors import ValidationFailed

CFG = get_config()


@pytest.mark.parametrize("raw,expected", [
    ("1.440.000", 1440000.0), ("1.234,56", 1234.56), ("1,440,000", 1440000.0), ("1,234.56", 1234.56), ("590000", 590000.0),
    ("15%", 0.15), ("12,5 %", 0.125), ("1.440.000 đ", 1440000.0), ("abc", None), ("", None), (None, None), (True, None),
])
def test_parse_number_vietnamese_and_english_formats(raw, expected):
    assert parse_number(raw) == expected


def test_sql_identifier_is_ascii_snake_and_unique():
    used: set[str] = set()
    assert sql_identifier("Tên sản phẩm", used) == "ten_san_pham"
    assert sql_identifier("Tên sản phẩm", used) == "ten_san_pham_2"
    assert sql_identifier("2024 Doanh thu", used) == "c_2024_doanh_thu"
    assert sql_identifier("%", used) == "col"


def test_frame_from_raw_coerces_types_and_keeps_original_names():
    raw = RawTable("t", "Sheet t", ["Mã", "Giá (VNĐ)", "Chiết khấu", "Ngày", "Ghi chú"],
                   [["A1", "1.440.000", "10%", "01/08/2026", "ok"], ["A2", "590.000", "5%", "15/08/2026", None], ["A3", "1.000", "", "", "x"]])
    # cột toàn số nhưng có 1 ô rỗng -> vẫn số; cột có >10% giá trị không phải số (vd "n/a") sẽ giữ text (xem _coerce)
    df, cols = frame_from_raw(raw)
    by = {c["name"]: c for c in cols}
    assert by["ma"]["original"] == "Mã" and by["ma"]["type"] == "text"
    assert by["gia_vnd"]["type"] == "integer" and df["gia_vnd"].tolist()[:2] == [1440000, 590000]
    assert by["chiet_khau"]["type"] == "percent" and df["chiet_khau"].tolist()[0] == pytest.approx(0.10)
    assert by["ngay"]["type"] == "date" and str(df["ngay"].iloc[0])[:10] == "2026-08-01"
    assert by["ghi_chu"]["type"] == "text"


def test_xlsx_sample_price_list_extracts_300_rows_with_numeric_price():
    [raw] = raw_tables_from_xlsx("data/raw/xlsx/01_bang_gia_sku.xlsx")
    df, cols = frame_from_raw(raw)
    assert len(df) == 300 and {c["name"]: c["type"] for c in cols}["gia"] == "integer"


def test_xlsx_multi_row_header_and_title_rows_are_handled():
    raws = raw_tables_from_xlsx("data/raw/xlsx/03_lich_khuyen_mai.xlsx")
    df, cols = frame_from_raw(raws[0])
    names = [c["name"] for c in cols]
    assert "thong_tin_chuong_trinh_ten_chuong_trinh" in names and len(df) == 6
    inv = raw_tables_from_xlsx("data/raw/xlsx/02_ton_kho_theo_store.xlsx")
    assert len(inv) == 8 and all(len(r.rows) == 10 for r in inv)  # dòng tiêu đề gộp ô bị bỏ, 8 cửa hàng


def test_grid_records_from_docx_pdf_tables():
    class R:
        def __init__(self, content, location):
            self.content, self.location = content, location
    recs = [R("Tên | Giá\nA | 1.000\nB | 2.000", "Trang 3 > Bảng 1"), R("không phải bảng", "Trang 4")]
    [raw] = raw_tables_from_grid_records(recs)
    assert raw.headers == ["Tên", "Giá"] and len(raw.rows) == 2


@pytest.fixture
def price_table(tmp_path):
    [raw] = raw_tables_from_xlsx("data/raw/xlsx/01_bang_gia_sku.xlsx")
    df, cols = frame_from_raw(raw)
    p = tmp_path / "t.parquet"
    df.to_parquet(p, index=False)
    return TableInfo("t1", "bang_gia", "Bảng giá", "d1", "Bảng giá", "Sheet", cols, len(df), None, str(p))


def test_run_query_groups_and_limits(price_table):
    res = run_query(CFG, [price_table], "SELECT category, count(*) AS so_sku, avg(gia) AS gia_tb FROM bang_gia GROUP BY category ORDER BY so_sku DESC")
    assert [c["name"] for c in res.columns] == ["category", "so_sku", "gia_tb"] and res.row_count == 8 and not res.truncated
    small = run_query(CFG, [price_table], "SELECT * FROM bang_gia", limit=10)
    assert small.row_count == 10 and small.truncated
    stats = column_stats(CFG, price_table)
    assert stats["gia"]["count"] == 300 and stats["gia"]["max"] >= stats["gia"]["min"]


@pytest.mark.parametrize("sql", [
    "DROP TABLE bang_gia", "SELECT 1; SELECT 2", "select * from bang_gia -- cmt", "INSERT INTO bang_gia VALUES (1)",
    "SELECT * FROM read_parquet('/etc/passwd')", "COPY bang_gia TO '/tmp/x.csv'", "PRAGMA database_list", "SET memory_limit='1GB'",
])
def test_validate_sql_blocks_non_select_and_file_access(sql):
    with pytest.raises(SQLNotAllowed):
        validate_sql(sql)


def test_sandbox_blocks_reading_files_outside_table_dir(price_table):
    with pytest.raises(ValidationFailed):
        run_query(CFG, [price_table], "SELECT content FROM read_text('/etc/hosts')")
    with pytest.raises(ValidationFailed, match="SQL lỗi"):
        run_query(CFG, [price_table], "SELECT khong_co_cot FROM bang_gia")


def _res(cols, rows):
    return QueryResult(columns=[{"name": n, "type": t} for n, t in cols], rows=rows, row_count=len(rows), truncated=False, elapsed_ms=1, sql="")


def test_chart_heuristics():
    kpi = build_chart(_res([("tong", "bigint")], [[42]]))
    assert kpi["type"] == "kpi" and kpi["kpi"]["value"] == 42
    line = build_chart(_res([("thang", "varchar"), ("doanh_thu", "double")], [[f"2026-0{i}", i * 10.0] for i in range(1, 7)]))
    assert line["type"] == "line" and line["vega_lite"]["mark"]["type"] == "line"
    bar = build_chart(_res([("category", "varchar"), ("so_sku", "bigint")], [["A", 3], ["B", 5]]))
    assert bar["type"] == "bar" and bar["encoding"]["x"] == "category" and bar["encoding"]["y"] == ["so_sku"]
    pie = build_chart(_res([("nhom", "varchar"), ("ti_trong", "double")], [["A", 0.6], ["B", 0.4]]), {"type": "pie"})
    assert pie["type"] == "pie" and pie["vega_lite"]["mark"]["type"] == "arc"
    many = build_chart(_res([("ma", "varchar"), ("gia", "bigint")], [[f"SKU{i}", i] for i in range(80)]), {"type": "bar"})
    assert many["type"] == "table"  # quá nhiều nhóm -> bảng
    bad_hint = choose_encoding([{"name": "a", "type": "varchar"}, {"name": "b", "type": "bigint"}], [["x", 1], ["y", 2]], {"x": "khong_co", "y": "b"})
    assert bad_hint["x"] == "a"  # cột sai trong gợi ý -> tự chọn
    multi = build_chart(_res([("thang", "varchar"), ("thu", "double"), ("chi", "double")], [["T1", 1, 2], ["T2", 3, 4], ["T3", 5, 6]]))
    assert multi["vega_lite"]["transform"][0]["fold"] == ["thu", "chi"]
