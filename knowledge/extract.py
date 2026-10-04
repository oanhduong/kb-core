"""(tạm) Entity/quan hệ mở. Hiện thực đầy đủ ở bước Knowledge."""
from __future__ import annotations

from typing import Any


def extract_entities_for_document(cfg: Any, sm: Any, llm: Any, document_id: str, *, force: bool = False) -> dict[str, Any]:
    return {"skipped": "knowledge chưa bật"}
