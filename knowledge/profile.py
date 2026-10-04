"""(tạm) Hồ sơ ngữ nghĩa + tóm tắt tài liệu. Hiện thực đầy đủ ở bước Knowledge."""
from __future__ import annotations

from typing import Any


def build_document_profile(cfg: Any, sm: Any, llm: Any, document_id: str, *, force: bool = False) -> dict[str, Any]:
    return {"skipped": "knowledge chưa bật"}
