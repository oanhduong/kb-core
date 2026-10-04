"""(tạm) Trích bảng số liệu -> parquet/DuckDB. Hiện thực đầy đủ ở bước Analytics."""
from __future__ import annotations

from typing import Any


def extract_tables_for_document(cfg: Any, sm: Any, llm: Any, document_id: str, *, force: bool = False) -> dict[str, Any]:
    return {"skipped": "analytics chưa bật"}
