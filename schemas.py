"""Schema dùng chung cho toàn bộ dự án RetailKB Copilot (ingest, retrieval, API, ...)."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SourceType = Literal["docx", "xlsx", "pdf", "image", "audio", "video", "csv"]
DocType = Literal["policy", "price_list", "promotion", "catalog", "training", "contract", "inventory", "supplier", "unknown"]


class KBRecord(BaseModel):
    record_id: str
    source_file: str
    source_type: SourceType
    doc_type: str  # danh sách hợp lệ do domain pack quyết định (domains/packs.py); DocType ở trên là enum cho agent retail
    content: str
    location: str  # "p.12" | "Sheet1!A5:D5" | "00:02:15"
    metadata: dict  # store_code, sku, effective_date, version
    confidence: float = Field(ge=0.0, le=1.0)
    # Đoạn nội dung nghi vấn prompt injection lấy từ file nguồn (xem security/provenance.py) — model
    # tự báo cáo theo PROVENANCE_SYSTEM_INSTRUCTIONS, và/hoặc write_kb_record tự bổ sung qua
    # scan_for_suspicious_content(). Rỗng ở tuyệt đại đa số record hợp lệ.
    suspicious_content: list[str] = Field(default_factory=list)

    @field_validator("record_id", "source_file", "location", "content")
    @classmethod
    def _not_blank(cls, v):
        if not v.strip():
            raise ValueError("giá trị không được để trống")
        return v
