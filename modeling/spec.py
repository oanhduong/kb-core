"""Spec của 1 mô hình dữ liệu — nguồn sự thật duy nhất cho phân tích, duyệt, build view và tài liệu.

Mọi thứ ở đây là DỮ LIỆU khai báo (không có SQL tự do): bảng nguồn + vai trò, quan hệ (khoá nối + phép chuẩn hoá
khoá), logic từng cột đầu ra theo 5 loại quy tắc. Nhờ vậy cùng 1 spec được: dịch sang câu tiếng Việt/Anh cho người
không biết kỹ thuật (modeling/explain.py), biên dịch sang SQL Postgres/DuckDB (modeling/compile.py), kiểm chứng trên
dữ liệu thật (modeling/infer.py, modeling/validate.py) và xuất tài liệu (modeling/document.py).

Loại quy tắc của 1 cột đầu ra (Rule.kind):
- input      giá trị nhập tay trong sheet chính (mã, tên, mô tả) — giữ nguyên, không phải phép tính
- lookup     lấy 1 giá trị từ bảng khác qua quan hệ (VLOOKUP/INDEX-MATCH/XLOOKUP)
- aggregate  gộp nhiều dòng của bảng khác theo quan hệ: đếm/tổng/TB/nhỏ nhất/lớn nhất (+ điều kiện) — COUNTIFS/SUMIFS/pivot
- formula    biểu thức trên các cột khác của cùng dòng, cú pháp kiểu Excel: DAYS([Ngày B], [Ngày A]), [A] - [B], ROUND(...)
- constant   hằng số
- unresolved chưa xác định — view trả NULL cho tới khi người dùng chọn/khai báo
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["proposed", "confirmed", "rejected"]
RuleKind = Literal["input", "lookup", "aggregate", "formula", "constant", "unresolved"]
AggFunc = Literal["count", "count_distinct", "sum", "avg", "min", "max", "first"]
CondOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "not_null", "contains", "not_contains", "starts_with"]
TransformOp = Literal["trim", "lower", "upper", "number_part", "first_token", "remove_prefix", "remove_suffix", "left", "right", "replace"]
EvidenceKind = Literal["formula", "pivot", "data_match", "name_match", "ai", "user", "structure"]

SPEC_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Evidence(BaseModel):
    kind: EvidenceKind
    detail: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class Transform(BaseModel):
    """Chuẩn hoá giá trị khoá trước khi so khớp (luôn so dạng text đã trim)."""
    op: TransformOp
    args: list[Any] = Field(default_factory=list)


class KeyPair(BaseModel):
    base_column: str            # cột của bảng đích (sheet chính)
    source_column: str          # cột của bảng nguồn
    base_transforms: list[Transform] = Field(default_factory=list)
    source_transforms: list[Transform] = Field(default_factory=list)


class Relationship(BaseModel):
    id: str
    source_table: str
    target_table: str
    keys: list[KeyPair]
    # số dòng nguồn cho mỗi khoá đích: one_to_one (lookup) | many_to_one (gộp nhiều dòng nguồn về 1 khoá)
    cardinality: Literal["one_to_one", "many_to_one"] = "many_to_one"
    coverage: float | None = None        # tỉ lệ khoá của bảng đích tìm thấy bên nguồn
    source_match: float | None = None    # tỉ lệ khoá nguồn có mặt ở bảng đích
    matched_keys: int | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.5
    status: Status = "proposed"


class Condition(BaseModel):
    column: str
    op: CondOp
    value: Any = None
    # với cột ngày: so với (ngày báo cáo + N ngày) thay vì giá trị cố định — vd "trong 90 ngày tới"
    as_of_offset_days: int | None = None


class Rule(BaseModel):
    kind: RuleKind
    relationship_id: str | None = None
    table: str | None = None
    column: str | None = None
    func: AggFunc | None = None
    filters: list[Condition] = Field(default_factory=list)
    expression: str | None = None
    value: Any = None
    default: Any = None            # khi không có dòng khớp (Excel: IFERROR(...,0) / COUNTIFS ra 0)
    round_digits: int | None = None


class ColumnCheck(BaseModel):
    compared: int = 0
    matched: int = 0
    rate: float | None = None
    expected_nulls: int = 0
    samples: list[dict[str, Any]] = Field(default_factory=list)   # [{row, expected, actual}] các dòng lệch
    # view: tính end-to-end trong view; isolated: công thức được thử với đầu vào lấy thẳng từ Excel vì cột đầu vào
    # của nó chưa xác định (logic đúng nhưng view hiện chưa ra số)
    basis: Literal["view", "isolated"] = "view"
    waiting_on: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    rule: Rule
    check: ColumnCheck | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.0


class OutputColumn(BaseModel):
    name: str                      # tên cột SQL trong view (snake_case)
    label: str                     # tên hiển thị gốc trong Excel
    group: str | None = None       # nhóm header gộp ô (vd "CRF Data")
    type: str = "text"
    is_key: bool = False
    rule: Rule
    status: Status = "proposed"
    confidence: float = 0.0
    evidence: list[Evidence] = Field(default_factory=list)
    check: ColumnCheck | None = None
    alternatives: list[Candidate] = Field(default_factory=list)
    excel_formula: str | None = None
    note: str | None = None        # ghi chú nghiệp vụ người dùng nhập
    problem: str | None = None     # lỗi khi biên dịch/chạy quy tắc (view trả NULL cho cột này)


class SheetColumn(BaseModel):
    name: str
    label: str
    group: str | None = None
    type: str = "text"
    letter: str | None = None
    examples: list[str] = Field(default_factory=list)
    distinct: int = 0
    nulls: int = 0
    formula_count: int = 0
    formula: str | None = None     # công thức đại diện (đã dịch về dòng dữ liệu đầu)


class PivotValue(BaseModel):
    field: str
    func: str
    label: str


class PivotFilter(BaseModel):
    field: str
    selected: list[str] | None = None   # None = tất cả
    excluded: list[str] = Field(default_factory=list)


class PivotInfo(BaseModel):
    name: str
    sheet: str
    location: str
    source_sheet: str | None = None
    source_range: str | None = None
    rows: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    values: list[PivotValue] = Field(default_factory=list)
    filters: list[PivotFilter] = Field(default_factory=list)


class TableSpec(BaseModel):
    key: str                       # tên bảng SQL trong schema của mô hình
    title: str                     # tên sheet / file
    document_id: str | None = None
    filename: str | None = None
    sheet: str | None = None
    role: Literal["main", "source", "ignored"] = "source"
    columns: list[SheetColumn] = Field(default_factory=list)
    row_count: int = 0
    header_row: int | None = None
    data_range: str | None = None
    formula_count: int = 0
    pivots: list[PivotInfo] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    unique_keys: list[str] = Field(default_factory=list)   # cột (đơn) không trùng — ứng viên khoá
    main_score: float = 0.0


class Notice(BaseModel):
    code: str
    level: Literal["info", "warning", "error"] = "info"
    params: dict[str, Any] = Field(default_factory=dict)


class ModelParameters(BaseModel):
    # ngày báo cáo dùng cho TODAY()/"số ngày kể từ": "today" | YYYY-MM-DD | None (chưa cần)
    as_of_date: str | None = None
    as_of_source: Literal["excel", "inferred", "user", "default"] | None = None


class ModelSpec(BaseModel):
    version: int = SPEC_VERSION
    tables: list[TableSpec] = Field(default_factory=list)
    main_table: str | None = None
    view_name: str = "vw_output"
    relationships: list[Relationship] = Field(default_factory=list)
    columns: list[OutputColumn] = Field(default_factory=list)
    parameters: ModelParameters = Field(default_factory=ModelParameters)
    notices: list[Notice] = Field(default_factory=list)
    analyzed_at: str | None = None

    # ------------------------------------------------------------------ tiện ích tra cứu
    def table(self, key: str | None) -> TableSpec | None:
        return next((t for t in self.tables if t.key == key), None)

    def main(self) -> TableSpec | None:
        return self.table(self.main_table)

    def relationship(self, rid: str | None) -> Relationship | None:
        return next((r for r in self.relationships if r.id == rid), None)

    def column(self, name: str) -> OutputColumn | None:
        return next((c for c in self.columns if c.name == name), None)

    def active_relationships(self) -> list[Relationship]:
        return [r for r in self.relationships if r.status != "rejected"]
