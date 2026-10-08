"""ORM SQLAlchemy cho toàn bộ dữ liệu vận hành. Schema thật do Alembic tạo (migrations/versions) —
file này PHẢI khớp migration; khi đổi model -> tạo migration mới (`make migration MSG=...`).

Nhóm bảng:
- KB: documents, kb_records, kb_chunks, chunk_embeddings, kb_state, conflicts
- Graph: graph_aliases, graph_documents (đồ thị thật nằm trong Apache AGE, graph `retail_kb`)
- Jobs: jobs (queue nền cho worker — SELECT ... FOR UPDATE SKIP LOCKED, không cần Redis)
- QA: qa_traces, abstentions, review_queue, chat_sessions, chat_messages, feedback
- Vận hành: api_keys, rate_limit_counters, llm_usage, alert_events, config_versions
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


# =====================================================================================
# TÀI KHOẢN & WORKSPACE (multi-tenant)
# =====================================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="vi")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # generic | retail | ... (domains/*.yaml) — quyết định graph rule-based, luật review, thuộc tính thành viên
    domain_pack: Mapped[str] = mapped_column(String(32), nullable=False, default="generic")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")  # owner | editor | viewer
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # domain pack: vd {"store_code": "GM-HN01"}
    invited_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (PrimaryKeyConstraint("workspace_id", "user_id"), Index("ix_ws_members_user", "user_id"))


class WorkspaceInvite(Base):
    __tablename__ = "workspace_invites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    invited_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("workspace_id", "email", name="uq_ws_invite_email"),)


# =====================================================================================
# ANALYTICS — bảng dữ liệu trích từ file (DuckDB/parquet)
# =====================================================================================

class DataTable(Base):
    __tablename__ = "data_tables"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(String(32), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # tên SQL hợp lệ, duy nhất trong workspace
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_location: Mapped[str] = mapped_column(Text, nullable=False)  # "Sheet BangGia" | "Trang 3 > Bảng 1"
    columns: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # [{name, type, original, nullable, examples}]
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)  # parquet
    description: Mapped[str | None] = mapped_column(Text)  # LLM: bảng này nói về gì
    semantic: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # cột đo lường/thời gian/chiều
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_data_table_name"),
                      Index("ix_data_tables_document", "document_id"))


class DataModel(Base):
    """Mô hình dữ liệu nghiệp vụ (modeling/): spec JSON (bảng, quan hệ, logic từng cột — người dùng duyệt) + kết quả build
    (schema/view trong warehouse, đối chiếu với file gốc)."""
    __tablename__ = "data_models"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # draft | analyzing | review | building | ready | error
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    document_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    schema_name: Mapped[str] = mapped_column(String(63), nullable=False)
    validation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    build_info: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    job_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_data_models_ws", "workspace_id", "updated_at"),)


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    chart_spec: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


# =====================================================================================
# KNOWLEDGE GRAPH MỞ (open schema) — entity/quan hệ do LLM trích, hợp nhất theo workspace
# =====================================================================================

class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)  # person | organization | product | location | ... (mở)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_norm: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    properties: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "type", "name_norm", name="uq_entity_ws_type_name"),
                      Index("ix_entities_ws_type", "workspace_id", "type"))


class EntityMention(Base):
    __tablename__ = "entity_mentions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(String(32), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(String(32), nullable=False)
    record_id: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_entity_mentions_entity", "entity_id"), Index("ix_entity_mentions_document", "document_id"))


class Relation(Base):
    __tablename__ = "relations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(32), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[str] = mapped_column(String(32), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # mở, chuẩn hoá snake_case
    label: Mapped[str] = mapped_column(Text, nullable=False)  # dạng người đọc ("cung cấp cho")
    evidence: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    document_id: Mapped[str] = mapped_column(String(32), nullable=False)
    record_id: Mapped[str] = mapped_column(Text, nullable=False)
    properties: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("source_id", "target_id", "type", "record_id", name="uq_relation_edge"),
                      Index("ix_relations_ws", "workspace_id", "type"), Index("ix_relations_source", "source_id"),
                      Index("ix_relations_target", "target_id"), Index("ix_relations_document", "document_id"))


# =====================================================================================
# BÁO CÁO
# =====================================================================================

class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # blocks: [{id, type: markdown|chart|table|sources|kpi, ...}]
    blocks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")  # draft | generating | ready | error
    prompt: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_reports_ws", "workspace_id", "updated_at"),)


# =====================================================================================
# KB
# =====================================================================================

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    series_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    store_code: Mapped[str | None] = mapped_column(String(32))
    effective_date: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    version: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # pending | processing | active | superseded | duplicate | blocked | error | deleted
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    replace_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="document")
    supersedes_id: Mapped[str | None] = mapped_column(String(32))
    superseded_by_id: Mapped[str | None] = mapped_column(String(32))
    duplicate_of_id: Mapped[str | None] = mapped_column(String(32))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    # Hồ sơ ngữ nghĩa (LLM, sau ingest): summary + {kind, topics, key_entities, time_range, language, audience, ...}
    summary: Mapped[str | None] = mapped_column(Text)
    semantic_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_documents_content_hash", "content_hash"),
        Index("ix_documents_series", "series_key", "doc_type", "store_code"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_workspace", "workspace_id", "status"),
    )


class KBRecordRow(Base):
    __tablename__ = "kb_records"

    record_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(String(32), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    suspicious_content: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    store_code: Mapped[str | None] = mapped_column(String(32))
    skus: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    category: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    version: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # active | superseded | duplicate | deleted
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    duplicate_of: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_key: Mapped[str | None] = mapped_column(Text)
    manually_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    edited_by: Mapped[str | None] = mapped_column(Text)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_kb_records_document", "document_id"),
        Index("ix_kb_records_content_hash", "content_hash"),
        Index("ix_kb_records_entity", "entity_key"),
        Index("ix_kb_records_filter", "doc_type", "store_code", "is_active"),
        Index("ix_kb_records_skus", "skus", postgresql_using="gin"),
        Index("ix_kb_records_workspace", "workspace_id", "doc_type", "status"),
    )


class ChunkRow(Base):
    __tablename__ = "kb_chunks"

    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    record_id: Mapped[str] = mapped_column(Text, ForeignKey("kb_records.record_id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_chunk_id: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(8), nullable=False, default="single")  # parent | child | single
    text: Mapped[str] = mapped_column(Text, nullable=False)
    context_prefix: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    store_code: Mapped[str | None] = mapped_column(String(32))
    skus: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    effective_date: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chunking_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_kb_chunks_record", "record_id"),
        Index("ix_kb_chunks_document", "document_id"),
        Index("ix_kb_chunks_filter", "role", "doc_type", "store_code", "is_active", "deleted"),
        Index("ix_kb_chunks_skus", "skus", postgresql_using="gin"),
        Index("ix_kb_chunks_workspace", "workspace_id", "role", "deleted"),
    )


class ChunkEmbedding(Base):
    """1 dòng / (chunk, embedding model). Cột vector KHÔNG cố định số chiều -> nhiều model cùng tồn
    tại (đổi model = reindex song song, rollback = đổi config về model cũ). HNSW index tạo riêng cho
    từng model bằng partial expression index (xem index/kb_store.ensure_model_index)."""

    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[str] = mapped_column(Text, ForeignKey("kb_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding = mapped_column(Vector(), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (PrimaryKeyConstraint("chunk_id", "model"), Index("ix_chunk_embeddings_model", "model"))


class KBState(Base):
    __tablename__ = "kb_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Conflict(Base):
    __tablename__ = "conflicts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    record_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open | resolved | dismissed
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_conflicts_status", "status"),)


# =====================================================================================
# GRAPH (metadata; đồ thị thật trong Apache AGE)
# =====================================================================================

class GraphAlias(Base):
    __tablename__ = "graph_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # sku | product | store | category | supplier | promotion | policy
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    alias_norm: Mapped[str] = mapped_column(Text, nullable=False)
    canonical: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "kind", "alias_norm", name="uq_graph_alias_kind_norm"),)


class GraphDocument(Base):
    """Document nào đã đóng góp vào graph, khi nào — để cập nhật incremental (A6) và cho admin xem."""

    __tablename__ = "graph_documents"

    document_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    builder: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # done | skipped | error
    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_edges: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


# =====================================================================================
# JOBS
# =====================================================================================

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # ingest_file | reindex | graph_rebuild | graph_update | ...
    # queued | processing | done | blocked | error
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    document_id: Mapped[str | None] = mapped_column(String(32))
    batch_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alerted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_jobs_queue", "status", "run_after"),
        Index("ix_jobs_document", "document_id"),
        Index("ix_jobs_batch", "batch_id"),
    )


# =====================================================================================
# QA
# =====================================================================================

class QATrace(Base):
    __tablename__ = "qa_traces"

    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)  # chat | ask
    session_id: Mapped[str | None] = mapped_column(String(32))
    user_id: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(String(16))
    store_code: Mapped[str | None] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str | None] = mapped_column(String(24))
    route: Mapped[str | None] = mapped_column(String(24))
    tool_calls: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # agent: tool + tham số + tóm tắt kết quả
    artifacts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # chart/table sinh kèm câu trả lời
    # answered | abstained | pending_review | no_citation | rejected | error
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    decision_trace: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    conflicts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    top_score: Mapped[float | None] = mapped_column(Float)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    langfuse_trace_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_qa_traces_created", "created_at"),
        Index("ix_qa_traces_status", "status", "created_at"),
        Index("ix_qa_traces_session", "session_id"),
        Index("ix_qa_traces_user", "user_id"),
    )


class Abstention(Base):
    __tablename__ = "abstentions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"))
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str | None] = mapped_column(String(16))
    store_code: Mapped[str | None] = mapped_column(String(32))
    user_id: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)  # low_score | no_results | no_citation | graph_empty
    top_score: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (Index("ix_abstentions_created", "created_at"),)


class ReviewItem(Base):
    __tablename__ = "review_queue"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"))
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    session_id: Mapped[str | None] = mapped_column(String(32))
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[str | None] = mapped_column(Text)
    store_code: Mapped[str | None] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    draft_answer: Mapped[str] = mapped_column(Text, nullable=False)
    draft_citations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    decision_trace: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending | approved | rejected
    final_answer: Mapped[str | None] = mapped_column(Text)
    overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewer_id: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_review_status", "status", "created_at"),)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    store_code: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    summarized_upto_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_chat_sessions_user", "user_id", "updated_at"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="final")  # final | pending_review | rejected | error
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_chat_messages_session", "session_id", "id"),)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1 | -1
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")  # new | seen | fixed | wontfix
    admin_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("trace_id", "user_id", name="uq_feedback_trace_user"),
        Index("ix_feedback_rating", "rating", "created_at"),
    )


# =====================================================================================
# VẬN HÀNH
# =====================================================================================

class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    store_code: Mapped[str | None] = mapped_column(String(32))
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (PrimaryKeyConstraint("key", "window_start"),)


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    job_id: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")

    __table_args__ = (Index("ix_llm_usage_created", "created_at"),)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_alert_events_rule", "rule", "fingerprint", "sent_at"),)


class ConfigVersionRow(Base):
    __tablename__ = "config_versions"

    config_hash: Mapped[str] = mapped_column(String(32), primary_key=True)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    first_loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
