"""Warehouse Postgres cho mô hình dữ liệu: mỗi mô hình 1 schema riêng (dm_<id>), mỗi sheet 1 bảng có kiểu dữ liệu thật
(+ cột _row = số dòng Excel để truy vết), view kết quả do modeling/compile.py sinh. Tên bảng/cột/nhãn gốc được ghi vào
COMMENT để BI (Superset) hiển thị đúng tên nghiệp vụ. Role read-only cho BI (modeling.reader_role) được cấp quyền sau
mỗi lần build. Mọi tên định danh đều do sql_identifier sinh (an toàn) và luôn được quote.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from functools import lru_cache
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from core.config import AppConfig, get_settings
from modeling.compile import SQL_TYPE, compile_view, q
from modeling.explain import column_text
from modeling.spec import ModelSpec, TableSpec

logger = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def warehouse_engine(url: str | None = None) -> Engine:
    s = get_settings()
    return create_engine(url or s.warehouse_url or s.database_url, pool_pre_ping=True, pool_size=3, max_overflow=2, future=True)


def schema_for(cfg: AppConfig, model_id: str) -> str:
    return f"{cfg.modeling.schema_prefix}{model_id[:10]}".lower()


def _cell(v: Any, col_type: str) -> Any:
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if col_type == "date":
        if isinstance(v, (pd.Timestamp, datetime)):
            return v.date()
        return v if isinstance(v, date) else None
    if col_type == "timestamp":
        return v.to_pydatetime() if isinstance(v, pd.Timestamp) else v
    if col_type == "integer":
        return int(v)
    if col_type in ("double", "percent"):
        return float(v)
    if col_type == "boolean":
        return bool(v)
    return str(v)


def _comment(s: str) -> str:
    return "'" + s.replace("'", "''")[:1000] + "'"


def load_tables(engine: Engine, schema: str, spec: ModelSpec, frames: dict[str, pd.DataFrame]) -> dict[str, int]:
    """(Tái) tạo schema + toàn bộ bảng nguồn. Idempotent: xoá schema cũ của mô hình rồi nạp lại."""
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {q(schema)} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {q(schema)}"))
        for t in spec.tables:
            df = frames.get(t.key)
            if df is None:
                continue
            _create_table(conn, schema, t)
            counts[t.key] = _copy_rows(conn, schema, t, df)
    return counts


def _create_table(conn: Any, schema: str, t: TableSpec) -> None:
    cols = ["_row BIGINT NOT NULL"] + [f"{q(c.name)} {SQL_TYPE.get(c.type, 'VARCHAR')}" for c in t.columns]
    conn.execute(text(f"CREATE TABLE {q(schema)}.{q(t.key)} ({', '.join(cols)})"))
    origin = f"Sheet “{t.sheet or t.title}”" + (f" of {t.filename}" if t.filename else "")
    conn.execute(text(f"COMMENT ON TABLE {q(schema)}.{q(t.key)} IS {_comment(origin + f' ({t.role})')}"))
    conn.execute(text(f"COMMENT ON COLUMN {q(schema)}.{q(t.key)}._row IS {_comment('Row number in the Excel sheet')}"))
    for c in t.columns:
        label = c.label + (f" [{c.group}]" if c.group else "") + (f" — Excel column {c.letter}" if c.letter else "")
        conn.execute(text(f"COMMENT ON COLUMN {q(schema)}.{q(t.key)}.{q(c.name)} IS {_comment(label)}"))


def _copy_rows(conn: Any, schema: str, t: TableSpec, df: pd.DataFrame) -> int:
    names = ["_row"] + [c.name for c in t.columns]
    types = {c.name: c.type for c in t.columns}
    types["_row"] = "integer"
    raw = conn.connection.dbapi_connection if hasattr(conn.connection, "dbapi_connection") else conn.connection
    cols_sql = ", ".join(q(n) for n in names)
    n = 0
    with raw.cursor() as cur:
        with cur.copy(f"COPY {q(schema)}.{q(t.key)} ({cols_sql}) FROM STDIN") as cp:
            for row in df[names].itertuples(index=False, name=None):
                cp.write_row([_cell(v, types[name]) for v, name in zip(row, names)])
                n += 1
    return n


def create_view(engine: Engine, cfg: AppConfig, schema: str, spec: ModelSpec) -> tuple[str, dict[str, str]]:
    cv = compile_view(spec, qualify=lambda name: f"{q(schema)}.{q(name)}")
    with engine.begin() as conn:
        conn.execute(text(f"DROP VIEW IF EXISTS {q(schema)}.{q(spec.view_name)}"))
        conn.execute(text(f"CREATE VIEW {q(schema)}.{q(spec.view_name)} AS {cv.sql}"))
        main = spec.main()
        conn.execute(text(f"COMMENT ON VIEW {q(schema)}.{q(spec.view_name)} IS "
                          f"{_comment(f'Recalculated “{main.title if main else spec.view_name}” — one row per row of the Excel sheet')}"))
        conn.execute(text(f"COMMENT ON COLUMN {q(schema)}.{q(spec.view_name)}.source_row IS {_comment('Row number in the Excel sheet')}"))
        for c in spec.columns:
            note = column_text(spec, c)["en"]
            conn.execute(text(f"COMMENT ON COLUMN {q(schema)}.{q(spec.view_name)}.{q(c.name)} IS {_comment(f'{c.label}: {note}')}"))
        role = cfg.modeling.reader_role
        if role:
            exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
            if exists:
                conn.execute(text(f"GRANT USAGE ON SCHEMA {q(schema)} TO {q(role)}"))
                conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {q(schema)} TO {q(role)}"))
            else:
                logger.warning("reader_role %s chưa tồn tại — bỏ qua GRANT", role)
    return cv.sql, cv.problems


def read_view(engine: Engine, schema: str, view: str, limit: int | None = None, offset: int = 0) -> pd.DataFrame:
    sql = f"SELECT * FROM {q(schema)}.{q(view)} ORDER BY source_row"
    if limit is not None:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)


def read_table(engine: Engine, schema: str, table: str, limit: int, offset: int = 0) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(f"SELECT * FROM {q(schema)}.{q(table)} ORDER BY _row LIMIT {int(limit)} OFFSET {int(offset)}"), conn)


def drop_schema(engine: Engine, schema: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {q(schema)} CASCADE"))
