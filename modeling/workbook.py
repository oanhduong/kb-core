"""Đọc cấu trúc workbook: mỗi sheet -> 1 bảng dữ liệu chính (header, nhóm header gộp ô, vùng cột, dòng dữ liệu
kèm số dòng Excel), công thức theo cột (mẫu chiếm đa số), pivot table (nguồn, trường dòng, giá trị + hàm, bộ lọc).

Tổng quát, không giả định bố cục:
- Dòng header = dòng đầu tiên (trong 15 dòng đầu) có nhiều ô chữ gần bằng dòng nhiều chữ nhất; dòng trên header có
  ô gộp -> nhãn nhóm cho các cột bên dưới.
- Vùng bảng = dải cột liên tục từ ô header đầu tiên; dừng ở cột trống header mà dữ liệu bên dưới cũng thưa. Pivot
  table (đọc từ XML) và mọi khối nằm sau khoảng trống được loại khỏi bảng — pivot được giữ riêng làm bằng chứng logic.
- Công thức đọc ở chế độ read-only, chuẩn hoá về dòng dữ liệu đầu để gom mẫu.
"""
from __future__ import annotations

import logging
import posixpath
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

from openpyxl.formula.translate import Translator as XlTranslator
from openpyxl.utils.cell import get_column_letter, range_boundaries

from modeling.spec import PivotFilter, PivotInfo, PivotValue

logger = logging.getLogger(__name__)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_AGG_NAME = {"sum": "sum", "count": "count", "average": "avg", "max": "max", "min": "min", "countNums": "count",
             "product": "product", "stdDev": "stddev", "stdDevp": "stddev", "var": "var", "varp": "var"}


@dataclass
class SheetTable:
    sheet: str
    header_row: int
    first_col: int
    last_col: int
    labels: list[str]
    groups: list[str | None]
    rows: list[list[Any]]
    excel_rows: list[int]
    formulas: dict[int, list[tuple[int, str]]] = field(default_factory=dict)   # offset cột -> [(dòng, công thức)]
    notes: list[str] = field(default_factory=list)
    sheet_index: int = 0

    @property
    def letters(self) -> list[str]:
        return [get_column_letter(c) for c in range(self.first_col, self.last_col + 1)]

    def dominant_formula(self, offset: int) -> tuple[str | None, int, int]:
        """-> (công thức đại diện đã dịch về dòng dữ liệu đầu, số ô theo mẫu đó, tổng số ô công thức)."""
        items = self.formulas.get(offset) or []
        if not items:
            return None, 0, 0
        letter = get_column_letter(self.first_col + offset)
        first = self.excel_rows[0] if self.excel_rows else self.header_row + 1
        pats: Counter[str] = Counter()
        for row, f in items:
            try:
                pats[XlTranslator(f, origin=f"{letter}{row}").translate_formula(f"{letter}{first}")] += 1
            except Exception:
                pats[f] += 1
        pat, n = pats.most_common(1)[0]
        return pat, n, len(items)


@dataclass
class Workbook:
    tables: list[SheetTable]
    pivots: list[PivotInfo]
    values: dict[str, Any] = field(default_factory=dict)  # sheet -> worksheet (data_only) để đọc ô tham số

    def cell_value(self, sheet: str, row: int, col: int) -> Any:
        ws = self.values.get(sheet)
        if ws is None:
            return None
        try:
            return ws.cell(row=row, column=col).value
        except Exception:
            return None


# ===================================================================================== pivot XML
def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    d, b = posixpath.split(part)
    path = posixpath.join(d, "_rels", b + ".rels")
    if path not in z.namelist():
        return {}
    out = {}
    for rel in ET.fromstring(z.read(path)).findall("pr:Relationship", NS):
        target = rel.get("Target", "")
        full = posixpath.normpath(posixpath.join(d, target)) if not target.startswith("/") else target.lstrip("/")
        out[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], full)
    return out


def _shared_items(cf: ET.Element) -> list[str]:
    si = cf.find("m:sharedItems", NS)
    if si is None:
        return []
    out = []
    for it in si:
        tag = it.tag.split("}")[-1]
        out.append("" if tag == "m" else it.get("v", ""))
    return out


def read_pivots(path: str) -> list[PivotInfo]:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return []
    out: list[PivotInfo] = []
    with z:
        try:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
        except KeyError:
            return []
        wb_rels = _rels(z, "xl/workbook.xml")
        for sh in wb.findall("m:sheets/m:sheet", NS):
            sheet_name = sh.get("name")
            rid = sh.get(f"{{{NS['r']}}}id")
            if rid not in wb_rels:
                continue
            sheet_part = wb_rels[rid][1]
            for _rid, (typ, pt_part) in _rels(z, sheet_part).items():
                if typ != "pivotTable" or pt_part not in z.namelist():
                    continue
                try:
                    out.append(_pivot(z, pt_part, sheet_name))
                except Exception as e:  # pivot hỏng không làm hỏng cả phân tích
                    logger.warning("đọc pivot %s lỗi: %s", pt_part, e)
    return out


def _pivot(z: zipfile.ZipFile, pt_part: str, sheet_name: str) -> PivotInfo:
    pt = ET.fromstring(z.read(pt_part))
    cache_part = next((p for t, p in _rels(z, pt_part).values() if t == "pivotCacheDefinition"), None)
    names: list[str] = []
    shared: list[list[str]] = []
    src_sheet = src_ref = None
    if cache_part and cache_part in z.namelist():
        cd = ET.fromstring(z.read(cache_part))
        ws = cd.find("m:cacheSource/m:worksheetSource", NS)
        if ws is not None:
            src_sheet, src_ref = ws.get("sheet"), ws.get("ref") or ws.get("name")
        for cf in cd.findall("m:cacheFields/m:cacheField", NS):
            names.append(cf.get("name", ""))
            shared.append(_shared_items(cf))

    def fname(i: int) -> str:
        return names[i] if 0 <= i < len(names) else f"field{i}"

    loc = pt.find("m:location", NS)
    location = loc.get("ref") if loc is not None else ""
    page_rows = int(loc.get("rowPageCount", "0")) if loc is not None else 0
    pfields = pt.findall("m:pivotFields/m:pivotField", NS)
    hidden: dict[int, list[str]] = {}
    for i, pf in enumerate(pfields):
        hs = []
        for it in pf.findall("m:items/m:item", NS):
            if it.get("h") == "1" and it.get("x") is not None:
                x = int(it.get("x"))
                vals = shared[i] if i < len(shared) else []
                hs.append(vals[x] if x < len(vals) else str(x))
        if hs:
            hidden[i] = hs
    rows = [fname(int(f.get("x"))) for f in pt.findall("m:rowFields/m:field", NS) if int(f.get("x", "-1")) >= 0]
    cols = [fname(int(f.get("x"))) for f in pt.findall("m:colFields/m:field", NS) if int(f.get("x", "-1")) >= 0]
    values = [PivotValue(field=fname(int(d.get("fld", "0"))), func=_AGG_NAME.get(d.get("subtotal", "sum"), d.get("subtotal", "sum")),
                         label=d.get("name") or "") for d in pt.findall("m:dataFields/m:dataField", NS)]
    filters: list[PivotFilter] = []
    seen: set[int] = set()
    for pfd in pt.findall("m:pageFields/m:pageField", NS):
        fld = int(pfd.get("fld", "0"))
        seen.add(fld)
        item = pfd.get("item")
        sel = None
        if item is not None:
            vals = shared[fld] if fld < len(shared) else []
            # item là chỉ số trong danh sách items của pivotField, mỗi item trỏ x vào sharedItems
            items = pfields[fld].findall("m:items/m:item", NS) if fld < len(pfields) else []
            it = items[int(item)] if int(item) < len(items) else None
            x = int(it.get("x")) if it is not None and it.get("x") is not None else int(item)
            sel = [vals[x]] if x < len(vals) else None
        filters.append(PivotFilter(field=fname(fld), selected=sel, excluded=hidden.get(fld, [])))
    for i, hs in hidden.items():
        if i not in seen:
            filters.append(PivotFilter(field=fname(i), excluded=hs))
    info = PivotInfo(name=pt.get("name", "PivotTable"), sheet=sheet_name, location=location, source_sheet=src_sheet,
                     source_range=src_ref, rows=rows, columns=cols, values=values, filters=filters)
    info.__dict__["_page_rows"] = page_rows
    return info


def _pivot_boxes(pivots: list[PivotInfo], sheet: str) -> list[tuple[int, int, int, int]]:
    """Vùng chiếm chỗ của pivot trên sheet (gồm cả các dòng bộ lọc trang phía trên): (c1, r1, c2, r2)."""
    out = []
    for p in pivots:
        if p.sheet != sheet or not p.location:
            continue
        try:
            c1, r1, c2, r2 = range_boundaries(p.location)
        except ValueError:
            continue
        page_rows = int(p.__dict__.get("_page_rows", 0) or 0)
        out.append((c1, max(1, r1 - page_rows - 1), c2, r2))
    return out


# ===================================================================================== sheet -> bảng
def _is_text(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != "" and not v.strip().startswith("=")


def detect_table(ws: Any, boxes: list[tuple[int, int, int, int]], *, max_rows: int) -> SheetTable | None:
    max_row, max_col = ws.max_row or 0, ws.max_column or 0
    if max_row < 2 or max_col < 1:
        return None

    def excluded(r: int, c: int) -> bool:
        return any(c1 <= c <= c2 and r1 <= r <= r2 for c1, r1, c2, r2 in boxes)

    scan = min(max_row, 40)
    grid = [[ws.cell(row=r, column=c).value for c in range(1, max_col + 1)] for r in range(1, scan + 1)]
    texts = [sum(1 for c, v in enumerate(row, 1) if _is_text(v) and not excluded(r, c)) for r, row in enumerate(grid, 1)]
    best = max(texts[:15]) if texts else 0
    if best < 1:
        return None
    header_row = None
    for r in range(1, min(15, scan) + 1):
        if texts[r - 1] >= max(1, 0.6 * best):
            nxt = grid[r] if r < len(grid) else []
            if sum(1 for v in nxt if v not in (None, "")) >= min(2, texts[r - 1]):
                header_row = r
                break
    if header_row is None:
        return None
    hdr = grid[header_row - 1]
    start = next((c for c in range(1, max_col + 1) if hdr[c - 1] not in (None, "") and not excluded(header_row, c)), None)
    if start is None:
        return None
    below = grid[header_row: header_row + 50]

    def density(c: int) -> float:
        if not below:
            return 0.0
        return sum(1 for i, row in enumerate(below) if c - 1 < len(row) and row[c - 1] not in (None, "")
                   and not excluded(header_row + 1 + i, c)) / len(below)

    end = start
    c = start + 1
    while c <= max_col:
        if excluded(header_row, c):
            break
        h = hdr[c - 1]
        if h in (None, "") and density(c) < 0.3:
            break
        end = c
        c += 1
    # nhãn + nhóm (ô gộp phía trên header)
    labels: list[str] = []
    seen: Counter[str] = Counter()
    for c in range(start, end + 1):
        raw = hdr[c - 1]
        lab = " ".join(str(raw).split()) if raw not in (None, "") else f"Column {get_column_letter(c)}"
        seen[lab] += 1
        labels.append(lab if seen[lab] == 1 else f"{lab} ({seen[lab]})")
    groups: list[str | None] = [None] * (end - start + 1)
    for mr in getattr(ws, "merged_cells", None).ranges if getattr(ws, "merged_cells", None) else []:
        if mr.max_row < header_row and mr.max_col >= start and mr.min_col <= end:
            v = ws.cell(row=mr.min_row, column=mr.min_col).value
            if _is_text(v):
                for cc in range(max(start, mr.min_col), min(end, mr.max_col) + 1):
                    groups[cc - start] = " ".join(str(v).split())
    notes = []
    for r in range(1, header_row):
        for c in range(1, max_col + 1):
            v = grid[r - 1][c - 1]
            if _is_text(v) and not any(g == " ".join(str(v).split()) for g in groups if g):
                if not excluded(r, c) and start <= c <= end:
                    notes.append(" ".join(str(v).split())[:200])
    rows: list[list[Any]] = []
    excel_rows: list[int] = []
    for r_idx, row in enumerate(ws.iter_rows(min_row=header_row + 1, max_row=max_row, min_col=start, max_col=end, values_only=True),
                                start=header_row + 1):
        vals = list(row)
        if boxes:
            vals = [None if excluded(r_idx, start + i) else v for i, v in enumerate(vals)]
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in vals):
            continue
        rows.append(vals)
        excel_rows.append(r_idx)
        if len(rows) >= max_rows:
            notes.append(f"truncated:{max_rows}")
            break
    if not rows:
        return None
    return SheetTable(sheet=ws.title, header_row=header_row, first_col=start, last_col=end, labels=labels, groups=groups,
                      rows=rows, excel_rows=excel_rows, notes=notes)


def _collect_formulas(path: str, tables: list[SheetTable], max_per_col: int = 5000) -> None:
    from openpyxl import load_workbook

    try:
        wbf = load_workbook(path, data_only=False, read_only=True)
    except Exception as e:
        logger.warning("không đọc được công thức: %s", e)
        return
    try:
        for t in tables:
            if t.sheet not in wbf.sheetnames or not t.excel_rows:
                continue
            ws = wbf[t.sheet]
            wanted = set(t.excel_rows)
            for r_idx, row in enumerate(ws.iter_rows(min_row=t.excel_rows[0], max_row=t.excel_rows[-1], min_col=t.first_col,
                                                     max_col=t.last_col, values_only=True), start=t.excel_rows[0]):
                if r_idx not in wanted:
                    continue
                for off, v in enumerate(row):
                    if isinstance(v, str) and v.startswith("=") and len(v) > 1:
                        lst = t.formulas.setdefault(off, [])
                        if len(lst) < max_per_col:
                            lst.append((r_idx, v))
    finally:
        wbf.close()


def _fill_constant_formulas(t: SheetTable) -> None:
    """File tạo bằng công cụ không tính lại công thức -> giá trị cache rỗng; hằng số kiểu =TRUE() thì tự điền."""
    consts = {"=TRUE()": True, "=FALSE()": False, "=TRUE": True, "=FALSE": False}
    row_pos = {r: i for i, r in enumerate(t.excel_rows)}
    for off, items in t.formulas.items():
        for r, f in items:
            i = row_pos.get(r)
            if i is None or t.rows[i][off] is not None:
                continue
            fu = f.strip().upper()
            if fu in consts:
                t.rows[i][off] = consts[fu]
            elif fu.startswith('="') and fu.endswith('"'):
                t.rows[i][off] = f.strip()[2:-1]
            else:
                try:
                    t.rows[i][off] = float(f[1:]) if "." in f else int(f[1:])
                except ValueError:
                    pass


def read_workbook(path: str, *, max_rows: int = 1_000_000) -> Workbook:
    from openpyxl import load_workbook

    pivots = read_pivots(path)
    wb = load_workbook(path, data_only=True)
    tables: list[SheetTable] = []
    values: dict[str, Any] = {}
    for i, ws in enumerate(wb.worksheets):
        if getattr(ws, "sheet_state", "visible") == "veryHidden":
            continue
        values[ws.title] = ws
        t = detect_table(ws, _pivot_boxes(pivots, ws.title), max_rows=max_rows)
        if t is not None:
            t.sheet_index = i
            tables.append(t)
    _collect_formulas(path, tables)
    for t in tables:
        _fill_constant_formulas(t)
    return Workbook(tables=tables, pivots=pivots, values=values)


def read_csv_table(path: str, title: str, *, max_rows: int = 1_000_000) -> Workbook:
    import pandas as pd

    df = pd.read_csv(path, sep=None, engine="python", dtype=str, keep_default_na=False, nrows=max_rows)
    labels = [" ".join(str(c).split()) or f"Column {i + 1}" for i, c in enumerate(df.columns)]
    rows = [[None if v == "" else v for v in r] for r in df.itertuples(index=False, name=None)]
    t = SheetTable(sheet=title, header_row=1, first_col=1, last_col=len(labels), labels=labels, groups=[None] * len(labels),
                   rows=rows, excel_rows=list(range(2, len(rows) + 2)))
    return Workbook(tables=[t], pivots=[])
