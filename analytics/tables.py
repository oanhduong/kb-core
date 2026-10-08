"""Trích bảng số liệu từ tài liệu -> parquet + catalog data_tables (DuckDB đọc trực tiếp parquet).

Nguồn bảng:
- xlsx: mỗi sheet 1 bảng. Dò số dòng header (bold ratio, cùng logic ingest/parsers), gộp header nhiều
  dòng ("Nhóm - Cột"), bỏ dòng tiêu đề gộp ô (1 ô có giá trị), bỏ dòng/cột trống hoàn toàn.
- docx/pdf: bảng do parser trích (unit kind=table, lưới "h | c | c" — ingest/chunkers._parse_table_grid).
- csv/tsv: đọc thẳng.
Chuẩn hoá: tên cột -> snake_case ASCII duy nhất (giữ tên gốc), ép kiểu theo giá trị thật (số kiểu
Việt "1.440.000"/"1,5", phần trăm, ngày dd/mm/yyyy & ISO, boolean) — cột không ép được giữ text.
Mỗi bảng: data_tables(name duy nhất trong workspace, columns[{name, original, type, role?, examples}],
row_count, storage_path, description (LLM, tuỳ chọn)). Idempotent theo (document_id, source_location):
chạy lại không tạo bảng trùng; force=True xoá & trích lại.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.i18n import detect_lang, output_language_rule
from core.config import AppConfig, read_prompt
from core.db import session_scope
from core.logging import log_event
from core.models import DataTable, Document, KBRecordRow
from graph.resolve import fold_text
from ingest.chunkers import _parse_table_grid
from security.provenance import wrap_untrusted

logger = logging.getLogger(__name__)

_NUM_VI_RE = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$")  # 1.440.000 | 1.234,56
_NUM_EN_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")  # 1,440,000 | 1,234.56
_NUM_PLAIN_RE = re.compile(r"^[+-]?\d+([.,]\d+)?$")
_PCT_RE = re.compile(r"^[+-]?\d+([.,]\d+)?\s*%$")
_DATE_VN_RE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}(\s+\d{1,2}:\d{2}(:\d{2})?)?$")
_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{1,2}:\d{2}(:\d{2})?)?$")
_BOOL = {"true": True, "false": False, "yes": True, "no": False, "có": True, "không": False, "x": True}
_CURRENCY_RE = re.compile(r"\s*(đ|vnđ|vnd|usd|\$|₫)\s*$", re.IGNORECASE)


# =====================================================================================
# Chuẩn hoá tên & kiểu
# =====================================================================================

def sql_identifier(name: str, used: set[str], fallback: str = "col") -> str:
    base = re.sub(r"[^a-z0-9]+", "_", fold_text(str(name).replace("_", " "))).strip("_")[:48] or fallback
    if base[0].isdigit():
        base = f"c_{base}"
    cand, n = base, 1
    while cand in used:
        n += 1
        cand = f"{base}_{n}"
    used.add(cand)
    return cand


def parse_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _CURRENCY_RE.sub("", str(v).strip())
    if not s:
        return None
    if _PCT_RE.match(s):
        return float(s.rstrip("% ").replace(",", ".")) / 100
    if _NUM_VI_RE.match(s):
        return float(s.replace(".", "").replace(",", "."))
    if _NUM_EN_RE.match(s):
        return float(s.replace(",", ""))
    if _NUM_PLAIN_RE.match(s):
        return float(s.replace(",", "."))
    return None


def _coerce(series: pd.Series) -> tuple[pd.Series, str]:
    """-> (series đã ép, kiểu: integer|double|percent|date|timestamp|boolean|text). Chỉ ép khi >= 90% giá trị
    không rỗng ép được (còn lại thành NULL) — tránh biến cột text có vài số thành số."""
    s = series.dropna()
    s = s[s.astype(str).str.strip() != ""]
    if s.empty:
        return series.astype("string"), "text"
    if pd.api.types.is_bool_dtype(s):
        return series.astype("boolean"), "boolean"
    if pd.api.types.is_datetime64_any_dtype(s):
        has_time = bool((pd.to_datetime(s).dt.normalize() != pd.to_datetime(s)).any())
        return pd.to_datetime(series, errors="coerce"), "timestamp" if has_time else "date"
    if pd.api.types.is_numeric_dtype(s):
        out = pd.to_numeric(series, errors="coerce")
        return (out.astype("Int64"), "integer") if (out.dropna() % 1 == 0).all() else (out.astype("float64"), "double")
    strs = s.astype(str).str.strip()
    n = len(strs)
    pct_hits = strs.str.match(_PCT_RE).sum()
    nums = strs.map(parse_number)
    if nums.notna().sum() >= 0.9 * n and n > 0:
        out = series.map(parse_number).astype("float64")
        if pct_hits >= 0.9 * n:
            return out, "percent"
        return (out.astype("Int64"), "integer") if (out.dropna() % 1 == 0).all() else (out, "double")
    if strs.str.match(_DATE_ISO_RE).sum() >= 0.9 * n:
        out = pd.to_datetime(series, errors="coerce")
        return out, "timestamp" if strs.str.contains(":").any() else "date"
    if strs.str.match(_DATE_VN_RE).sum() >= 0.9 * n:
        out = pd.to_datetime(series, errors="coerce", dayfirst=True)
        return out, "timestamp" if strs.str.contains(":").any() else "date"
    if strs.str.lower().isin(_BOOL).sum() >= 0.9 * n and strs.str.lower().nunique() <= 2:
        return series.map(lambda v: _BOOL.get(str(v).strip().lower()) if v is not None else None).astype("boolean"), "boolean"
    return series.astype("string"), "text"


@dataclass
class RawTable:
    title: str
    source_location: str
    headers: list[str]
    rows: list[list[Any]]


def frame_from_raw(raw: RawTable) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    width = max([len(raw.headers)] + [len(r) for r in raw.rows]) if raw.rows else len(raw.headers)
    headers = [str(h).strip() if h is not None and str(h).strip() else f"cot_{i + 1}" for i, h in enumerate(raw.headers)]
    headers += [f"cot_{i + 1}" for i in range(len(headers), width)]
    rows = [list(r) + [None] * (width - len(r)) for r in raw.rows]
    df = pd.DataFrame(rows, columns=headers)
    df = df.replace({"": None}).dropna(axis=0, how="all").dropna(axis=1, how="all")
    used: set[str] = set()
    cols: list[dict[str, Any]] = []
    out = pd.DataFrame(index=df.index)
    for pos, original in enumerate(df.columns):
        name = sql_identifier(original, used)
        raw_col = df.iloc[:, pos]  # theo vị trí: header trùng tên (vd 2 cột "Site Number") thì df[original] là DataFrame
        series, kind = _coerce(raw_col)
        out[name] = series
        examples = [str(v)[:60] for v in raw_col.dropna().astype(str).unique()[:3]]
        cols.append({"name": name, "original": str(original), "type": kind, "examples": examples,
                     "nullable": bool(series.isna().any())})
    return out.reset_index(drop=True), cols


# =====================================================================================
# Đọc nguồn
# =====================================================================================

def _is_title_row(values: list[Any]) -> bool:
    non_empty = [v for v in values if v is not None and str(v).strip() != ""]
    return len(values) >= 3 and len(non_empty) == 1 and isinstance(non_empty[0], str)


def raw_tables_from_xlsx(path: str) -> list[RawTable]:
    from openpyxl import load_workbook

    from ingest.parsers import _detect_header_row_count

    wb = load_workbook(path, data_only=True, read_only=False)
    out: list[RawTable] = []
    for ws in wb.worksheets:
        if ws.max_row < 2 or ws.max_column < 1:
            continue
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        # bỏ dòng tiêu đề gộp ô ở đầu (trước header)
        start = 0
        while start < len(rows) - 1 and _is_title_row(rows[start]):
            start += 1
        rows = rows[start:]
        if len(rows) < 2:
            continue
        try:
            hdr_n = max(1, min(_detect_header_row_count(ws, ws.max_row, ws.max_column), 3))
        except Exception:
            hdr_n = 1
        header_rows = rows[:hdr_n]
        headers = []
        for ci in range(len(header_rows[0])):
            parts = []
            for hr in header_rows:
                v = hr[ci] if ci < len(hr) else None
                if v is not None and str(v).strip() and (not parts or str(v).strip() != parts[-1]):
                    parts.append(str(v).strip())
            headers.append(" - ".join(parts))
        # header gộp ô: ô trống kế thừa nhóm bên trái
        if hdr_n > 1:
            last_group = None
            for ci, hr in enumerate(header_rows[0]):
                if hr is not None and str(hr).strip():
                    last_group = str(hr).strip()
                elif last_group and headers[ci] and " - " not in headers[ci]:
                    headers[ci] = f"{last_group} - {headers[ci]}"
        data = [r for r in rows[hdr_n:] if not _is_title_row(r) and any(v is not None and str(v).strip() for v in r)]
        if not data:
            continue
        out.append(RawTable(title=ws.title, source_location=f"Sheet {ws.title}", headers=headers, rows=data))
    return out


def raw_tables_from_grid_records(records: list[KBRecordRow]) -> list[RawTable]:
    out = []
    for r in records:
        grid = _parse_table_grid(r.content)
        if grid is None:
            continue
        headers, rows = grid
        out.append(RawTable(title=r.location.split(" > ")[-1], source_location=r.location, headers=headers, rows=rows))
    return out


def raw_tables_from_csv(path: str) -> list[RawTable]:
    sep = "\t" if path.lower().endswith(".tsv") else None
    df = pd.read_csv(path, sep=sep, engine="python", dtype=str, keep_default_na=False)
    return [RawTable(title=os.path.basename(path), source_location="CSV", headers=[str(c) for c in df.columns], rows=df.values.tolist())]


# =====================================================================================
# LLM mô tả bảng (tuỳ chọn)
# =====================================================================================

class _ColSem(BaseModel):
    role: str = "text"
    label: str | None = None
    unit: str | None = None


class _TableDescription(BaseModel):
    description: str = ""
    columns: dict[str, _ColSem] = Field(default_factory=dict)
    suggested_questions: list[str] = Field(default_factory=list, max_length=5)


def describe_table(cfg: AppConfig, llm: Any, doc_title: str, raw_title: str, cols: list[dict], sample: list[dict]) -> dict[str, Any]:
    if llm is None or not cfg.analytics.describe_with_llm:
        return {}
    from core.llm import LLMOutputInvalid, LLMUnavailable

    col_lines = "\n".join(f"- {c['name']} (gốc: {c['original']}, kiểu {c['type']}, ví dụ: {', '.join(c['examples'])})" for c in cols)
    sample_txt = "\n".join(" | ".join(f"{k}={v}" for k, v in row.items()) for row in sample[:5])
    user = (f"Tài liệu: {doc_title}\nVị trí bảng: {raw_title}\nCột:\n{col_lines}\n\nDòng mẫu:\n"
            + wrap_untrusted(sample_txt, source=f"{doc_title} / {raw_title}", doc_type="table"))
    try:
        system = read_prompt("table_describe", cfg) + output_language_rule(detect_lang(" ".join(c["original"] for c in cols) + "\n" + sample_txt), "The table")
        out = llm.complete_json("table_describe", system=system, user=user, schema=_TableDescription,
                                max_tokens=700, max_retries=1)
    except (LLMOutputInvalid, LLMUnavailable) as e:
        log_event(logger, "table_describe_failed", level=logging.WARNING, error=str(e)[:200])
        return {}
    known = {c["name"] for c in cols}
    return {"description": out.description[:1000],
            "columns": {k: v.model_dump() for k, v in out.columns.items() if k in known},
            "suggested_questions": out.suggested_questions}


# =====================================================================================
# Pipeline
# =====================================================================================

def _table_name(session: Session, workspace_id: str, doc_title: str, raw_title: str, used: set[str]) -> str:
    base = sql_identifier(f"{doc_title} {raw_title}" if raw_title.lower() not in doc_title.lower() else doc_title, set(), "table")[:40]
    existing = set(session.execute(select(DataTable.name).where(DataTable.workspace_id == workspace_id)).scalars()) | used
    cand, n = base, 1
    while cand in existing:
        n += 1
        cand = f"{base}_{n}"
    used.add(cand)
    return cand


def _json_safe(v: Any) -> Any:
    if v is None or (isinstance(v, float) and v != v):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def extract_tables_for_document(cfg: AppConfig, sm: sessionmaker, llm: Any, document_id: str, *, force: bool = False) -> dict[str, Any]:
    if not cfg.analytics.enabled:
        return {"skipped": "analytics.enabled=false"}
    with session_scope() as s:
        doc = s.get(Document, document_id)
        if doc is None:
            return {"skipped": "không có tài liệu"}
        existing = s.execute(select(DataTable).where(DataTable.document_id == document_id)).scalars().all()
        if existing and not force:
            if all(os.path.isfile(t.storage_path) for t in existing):
                return {"skipped": "đã có bảng", "tables": len(existing)}
        for t in existing:
            try:
                os.remove(t.storage_path)
            except OSError:
                pass
            s.delete(t)
        s.flush()
        title, path, stype, ws_id = doc.title, doc.storage_path, doc.source_type, doc.workspace_id
        grid_records = []
        if stype in ("docx", "pdf"):
            grid_records = s.execute(select(KBRecordRow).where(KBRecordRow.document_id == document_id, KBRecordRow.deleted_at.is_(None),
                                                               KBRecordRow.content.like("%|%"))).scalars().all()
    if stype == "xlsx":
        raws = raw_tables_from_xlsx(path)
    elif stype == "csv":
        raws = raw_tables_from_csv(path)
    elif stype in ("docx", "pdf"):
        raws = raw_tables_from_grid_records(grid_records)
    else:
        return {"skipped": f"loại {stype} không có bảng"}

    folder = os.path.join(cfg.analytics.storage_dir, ws_id)
    os.makedirs(folder, exist_ok=True)
    created = []
    used_names: set[str] = set()
    with session_scope() as s:
        for raw in raws:
            if len(raw.rows) < cfg.analytics.min_rows:
                continue
            df, cols = frame_from_raw(raw)
            if df.empty or len(df.columns) < 2 and len(df) < 5:
                continue
            if len(df) > cfg.analytics.max_rows_per_table:
                df = df.head(cfg.analytics.max_rows_per_table)
            name = _table_name(s, ws_id, title, raw.title, used_names)
            table = DataTable(workspace_id=ws_id, document_id=document_id, name=name, title=f"{title} — {raw.title}",
                              source_location=raw.source_location, columns=cols, row_count=len(df), storage_path="")
            s.add(table)
            s.flush()
            table.storage_path = os.path.join(folder, f"{table.id}.parquet")
            df.to_parquet(table.storage_path, index=False)
            sample = [{k: _json_safe(v) for k, v in row.items()} for row in df.head(5).to_dict(orient="records")]
            sem = describe_table(cfg, llm, title, raw.title, cols, sample)
            table.description = sem.get("description") or None
            table.semantic = {"columns": sem.get("columns", {}), "suggested_questions": sem.get("suggested_questions", []), "sample": sample}
            for c in cols:
                role = (sem.get("columns") or {}).get(c["name"], {}).get("role")
                c["role"] = role or ("time" if c["type"] in ("date", "timestamp") else "measure" if c["type"] in ("integer", "double", "percent") else "dimension")
            table.columns = cols
            created.append({"id": table.id, "name": name, "rows": len(df), "columns": len(cols)})
        d = s.get(Document, document_id)
        if d is not None:
            d.table_count = len(created)
    log_event(logger, "tables_extracted", document_id=document_id, tables=len(created))
    return {"tables": created}


def set_tables_active(session: Session, document_id: str, active: bool) -> int:
    rows = session.execute(select(DataTable).where(DataTable.document_id == document_id)).scalars().all()
    for t in rows:
        t.is_active = active
    return len(rows)
