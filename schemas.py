"""Schema dùng chung cho toàn bộ dự án RetailKB Copilot (ingest, retrieval, API, ...)."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SourceType = Literal["docx", "xlsx", "pdf", "image", "audio", "video"]
DocType = Literal["policy", "price_list", "promotion", "catalog", "training", "contract", "unknown"]


class KBRecord(BaseModel):
    record_id: str
    source_file: str
    source_type: SourceType
    doc_type: DocType
    content: str
    location: str  # "p.12" | "Sheet1!A5:D5" | "00:02:15"
    metadata: dict  # store_code, sku, effective_date, version
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("record_id", "source_file", "location", "content")
    @classmethod
    def _not_blank(cls, v):
        if not v.strip():
            raise ValueError("giá trị không được để trống")
        return v
