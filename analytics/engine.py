"""DuckDB engine trên các parquet của workspace: catalog, validate SQL read-only, chạy có timeout.

An toàn: mỗi truy vấn chạy trên connection DuckDB in-memory MỚI, chỉ đăng ký VIEW tới parquet của
đúng workspace; `enable_external_access=false` nên SQL không đọc được file/URL khác; chỉ chấp nhận
1 câu SELECT/WITH (parse bằng duckdb + chặn từ khoá ghi/đọc ngoài); kết quả giới hạn max_result_rows;
quá query_timeout_seconds -> interrupt.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import AppConfig
from core.errors import ValidationFailed
from core.i18n import L
from core.models import DataTable, Document

logger = logging.getLogger(__name__)

_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|import|pragma|install|load|call|set|reset|"
    r"truncate|vacuum|checkpoint|read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|glob|getenv|"
    r"sniff_csv|httpfs|st_read|parquet_scan|csv_scan|json_scan)\b", re.IGNORECASE)
_SAFE_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SQLNotAllowed(ValidationFailed):
    code = "sql_not_allowed"


@dataclass
class TableInfo:
    id: str
    name: str
    title: str
    document_id: str
    document_title: str
    source_location: str
    columns: list[dict[str, Any]]
    row_count: int
    description: str | None
    storage_path: str
    semantic: dict[str, Any] = field(default_factory=dict)

    def to_prompt(self) -> str:
        cols = ", ".join(f"{c['name']} {c['type']}" + (f" [{c.get('role')}]" if c.get("role") else "")
                         + (f" '{c['original']}'" if c.get("original") and c["original"] != c["name"] else "") for c in self.columns)
        sample = self.semantic.get("sample") or []
        sample_txt = ""
        if sample:
            sample_txt = "\n    ví dụ: " + json.dumps(sample[:2], ensure_ascii=False, default=str)[:600]
        return (f"- {self.name} ({self.row_count} dòng) — {self.title}" + (f": {self.description}" if self.description else "")
                + f"\n    cột: {cols}{sample_txt}")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "title": self.title, "document_id": self.document_id,
                "document_title": self.document_title, "source_location": self.source_location, "columns": self.columns,
                "row_count": self.row_count, "description": self.description,
                "suggested_questions": self.semantic.get("suggested_questions", [])}


def catalog(session: Session, workspace_id: str, *, document_ids: list[str] | None = None) -> list[TableInfo]:
    q = (select(DataTable, Document.title).join(Document, Document.id == DataTable.document_id)
         .where(DataTable.workspace_id == workspace_id, DataTable.is_active.is_(True), Document.deleted_at.is_(None))
         .order_by(DataTable.created_at))
    if document_ids:
        q = q.where(DataTable.document_id.in_(document_ids))
    return [TableInfo(t.id, t.name, t.title, t.document_id, dt, t.source_location, t.columns or [], t.row_count, t.description,
                      t.storage_path, t.semantic or {}) for t, dt in session.execute(q).all()]


def catalog_prompt(tables: list[TableInfo]) -> str:
    if not tables:
        return "<catalog>\n(workspace chưa có bảng số liệu nào)\n</catalog>"
    return "<catalog>\n" + "\n".join(t.to_prompt() for t in tables) + "\n</catalog>"


# =====================================================================================
# Validate
# =====================================================================================

def validate_sql(sql: str) -> str:
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        raise SQLNotAllowed(L("SQL rỗng", "Empty SQL"))
    if ";" in s:
        raise SQLNotAllowed(L("Chỉ chấp nhận MỘT câu lệnh SQL", "Only ONE SQL statement is allowed"))
    no_strings = re.sub(r"'(?:[^']|'')*'", "''", s)
    if "--" in no_strings or "/*" in no_strings:
        raise SQLNotAllowed(L("Không dùng comment trong SQL", "Comments are not allowed in SQL"))
    head = no_strings.lstrip().split(None, 1)[0].lower()
    if head not in ("select", "with"):
        raise SQLNotAllowed(L("Chỉ chấp nhận câu SELECT (hoặc WITH ... SELECT)", "Only SELECT statements (or WITH ... SELECT) are allowed"))
    m = _FORBIDDEN_RE.search(no_strings)
    if m:
        raise SQLNotAllowed(L(f"Từ khoá không được phép trong SQL read-only: {m.group(0)}", f"Keyword not allowed in read-only SQL: {m.group(0)}"))
    return s


# =====================================================================================
# Chạy
# =====================================================================================

@dataclass
class QueryResult:
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int
    sql: str

    def to_dict(self) -> dict[str, Any]:
        return {"columns": self.columns, "rows": self.rows, "row_count": self.row_count, "truncated": self.truncated,
                "elapsed_ms": self.elapsed_ms, "sql": self.sql}

    def records(self) -> list[dict[str, Any]]:
        names = [c["name"] for c in self.columns]
        return [dict(zip(names, r)) for r in self.rows]


def _json_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, (list, tuple)):
        return [_json_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _json_value(x) for k, x in v.items()}
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def _connect(tables: list[TableInfo]) -> duckdb.DuckDBPyConnection:
    """Connection in-memory mới: chỉ được đọc thư mục chứa parquet của các bảng truyền vào (allowed_directories),
    mọi truy cập file/URL khác bị chặn (enable_external_access=false)."""
    conn = duckdb.connect(database=":memory:")
    dirs = sorted({os.path.dirname(os.path.abspath(t.storage_path)) for t in tables})
    try:
        conn.execute("SET allowed_directories = [" + ", ".join("'" + d.replace("'", "''") + "'" for d in dirs) + "]")
        conn.execute("SET enable_external_access = false")
        materialize = False
    except duckdb.Error:  # bản DuckDB cũ không có allowed_directories -> nạp dữ liệu vào RAM rồi mới khoá
        materialize = True
    for t in tables:
        if not _SAFE_IDENT_RE.match(t.name):
            continue
        path = os.path.abspath(t.storage_path).replace("'", "''")
        kind = "TABLE" if materialize else "VIEW"
        conn.execute(f"CREATE {kind} \"{t.name}\" AS SELECT * FROM read_parquet('{path}')")
    if materialize:
        conn.execute("SET enable_external_access = false")
    return conn


def run_query(cfg: AppConfig, tables: list[TableInfo], sql: str, *, limit: int | None = None) -> QueryResult:
    sql = validate_sql(sql)
    limit = min(limit or cfg.analytics.max_result_rows, cfg.analytics.max_result_rows)
    conn = _connect(tables)
    timer = threading.Timer(cfg.analytics.query_timeout_seconds, conn.interrupt)
    start = time.perf_counter()
    try:
        timer.start()
        cur = conn.execute(f"SELECT * FROM ({sql}) AS _q LIMIT {int(limit) + 1}")
        rows = cur.fetchall()
        cols = [{"name": d[0], "type": str(d[1]).lower()} for d in cur.description]
    except duckdb.InterruptException as e:
        raise ValidationFailed(L(f"Truy vấn quá {cfg.analytics.query_timeout_seconds:.0f}s, đã dừng. Hãy thu hẹp phạm vi.", f"The query took longer than {cfg.analytics.query_timeout_seconds:.0f}s and was stopped. Try narrowing it down.")) from e
    except (duckdb.Error, RuntimeError) as e:
        raise ValidationFailed(L(f"SQL lỗi: {str(e).splitlines()[0][:300]}", f"SQL error: {str(e).splitlines()[0][:300]}"), context={"sql": sql}) from e
    finally:
        timer.cancel()
        conn.close()
    truncated = len(rows) > limit
    rows = rows[:limit]
    return QueryResult(columns=cols, rows=[[_json_value(v) for v in r] for r in rows], row_count=len(rows), truncated=truncated,
                       elapsed_ms=int((time.perf_counter() - start) * 1000), sql=sql)


def preview(cfg: AppConfig, table: TableInfo, limit: int = 50, offset: int = 0) -> QueryResult:
    return run_query(cfg, [table], f'SELECT * FROM "{table.name}" LIMIT {int(limit)} OFFSET {int(offset)}', limit=limit)


def column_stats(cfg: AppConfig, table: TableInfo) -> dict[str, Any]:
    """Thống kê nhanh từng cột (null, distinct, min/max/avg cho số, min/max cho ngày) — cho trang bảng."""
    parts = []
    for c in table.columns:
        n = c["name"]
        if c["type"] in ("integer", "double", "percent"):
            parts.append(f'count("{n}") AS "{n}__count", count(DISTINCT "{n}") AS "{n}__distinct", min("{n}") AS "{n}__min", '
                         f'max("{n}") AS "{n}__max", avg(try_cast("{n}" AS DOUBLE)) AS "{n}__avg"')
        elif c["type"] in ("date", "timestamp"):
            parts.append(f'count("{n}") AS "{n}__count", count(DISTINCT "{n}") AS "{n}__distinct", min("{n}") AS "{n}__min", max("{n}") AS "{n}__max"')
        else:
            parts.append(f'count("{n}") AS "{n}__count", count(DISTINCT "{n}") AS "{n}__distinct"')
    if not parts:
        return {}
    res = run_query(cfg, [table], f'SELECT {", ".join(parts)} FROM "{table.name}"', limit=1)
    row = res.records()[0] if res.rows else {}
    out: dict[str, Any] = {}
    for k, v in row.items():
        col, stat = k.rsplit("__", 1)
        out.setdefault(col, {})[stat] = v
    return out
