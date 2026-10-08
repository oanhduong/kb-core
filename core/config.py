"""Config tập trung (F1): MỌI tham số ảnh hưởng kết quả trả lời nằm trong config.yaml — model cho
từng vai trò, chunking, embedding, top_k/rerank, ngưỡng abstention, router, đường dẫn prompt kèm
sha256. Secret (API key, DSN, JWT secret...) KHÔNG nằm ở đây — đọc từ biến môi trường (xem Settings).

Version & rollback:
- config.yaml có `config_version`. Mỗi lần đổi tham số: bump version + ghi config/CHANGELOG.md +
  `python -m core.config snapshot` (chép sang config/versions/<version>.yaml).
- Lúc khởi động: nếu RETAILKB_CONFIG_VERSION được set -> nạp config/versions/<version>.yaml, ngược
  lại nạp config.yaml. Rollback = đặt RETAILKB_CONFIG_VERSION=<bản cũ> rồi restart.
- Prompt: config ghi path + sha256. Khởi động tính lại hash, lệch -> fail ngay (không cho prompt bị
  sửa im lặng mà version không đổi). `python -m core.config hash-prompts` in hash mới để cập nhật.
- config_hash = sha256(config đã chuẩn hoá) — đi kèm mọi trace để tìm lại đúng bản đã chạy.

Biến môi trường trong YAML: ${VAR} hoặc ${VAR:-mặc_định}.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from core.errors import ConfigError
from core.i18n import L

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
VERSIONS_DIR = REPO_ROOT / "config" / "versions"

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


# =====================================================================================
# SCHEMA
# =====================================================================================

class ModelsConfig(BaseModel):
    answer: str = "claude-sonnet-4-6"
    router: str = "claude-haiku-4-5"
    entity_extraction: str = "claude-haiku-4-5"
    graph_extraction: str = "claude-haiku-4-5"
    compaction: str = "claude-haiku-4-5"
    contextual_retrieval: str = "claude-haiku-4-5"
    metadata_fallback: str = "claude-haiku-4-5"
    doc_classification: str = "claude-haiku-4-5"
    nl2sql: str = "claude-sonnet-4-6"
    table_describe: str = "claude-haiku-4-5"
    document_profile: str = "claude-haiku-4-5"
    knowledge_extract: str = "claude-haiku-4-5"
    assistant: str = "claude-sonnet-4-6"
    report: str = "claude-sonnet-4-6"
    vision: str = "claude-sonnet-4-6"
    ingest_agent: str = "claude-sonnet-4-6"
    data_model: str = "claude-sonnet-4-6"
    translate: str = "claude-haiku-4-5"     # dịch nội dung AI sinh sang ngôn ngữ giao diện còn lại


class ModelPrice(BaseModel):
    input: float
    output: float
    cache_write_multiplier: float = 1.25
    cache_read_multiplier: float = 0.10


class LLMConfig(BaseModel):
    timeout_seconds: float = 60.0
    max_retries: int = 2
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 8.0
    answer_max_tokens: int = 1024
    assistant_max_tokens: int = 2048
    pricing: dict[str, ModelPrice] = Field(default_factory=dict)


class IngestConfig(BaseModel):
    mode: Literal["deterministic", "agent"] = "deterministic"
    # none: parse trong tiến trình worker | sidecar: container `parser` không network (docker-compose) |
    # docker: worker tự `docker run` 1 container/file (security/sandbox.py, cần quyền Docker daemon)
    sandbox_mode: Literal["none", "sidecar", "docker"] = "none"
    sandbox_image: str = "retailkb-parser-sandbox:latest"
    sandbox_timeout_seconds: int = 300
    sidecar_work_dir: str = "data/parsework"
    storage_dir: str = "data/uploads"
    max_upload_mb: int = 200
    allowed_extensions: list[str] = Field(default_factory=lambda: [
        ".docx", ".xlsx", ".csv", ".tsv", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".m4a", ".mp4", ".mov"])
    contextual_retrieval: bool = False
    metadata_llm_fallback: bool = False
    image_extraction: bool = True
    whisper_model_size: str = "small"
    agent_max_turns: int = 15
    agent_max_verify_retries: int = 3


class ChunkingConfig(BaseModel):
    strategy: Literal["fixed", "recursive", "structure_aware", "parent_child"] = "parent_child"
    fixed_chunk_tokens: int = 512
    fixed_overlap_tokens: int = 50
    recursive_chunk_chars: int = 1800
    recursive_overlap_chars: int = 200
    small_table_max_rows: int = 10
    parent_chunk_tokens: int = 1024
    child_chunk_tokens: int = 256
    child_overlap_tokens: int = 30
    media_max_gap_seconds: float = 5.0
    media_max_chunk_chars: int = 2000

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()[:12]


class EmbeddingConfig(BaseModel):
    provider: Literal["sentence_transformers", "hashing"] = "sentence_transformers"
    model: str = "intfloat/multilingual-e5-base"
    dim: int = 768
    query_prefix: str = "query: "
    passage_prefix: str = "passage: "
    batch_size: int = 32
    device: str = "cpu"


class RerankConfig(BaseModel):
    enabled: bool = True
    model: str = "BAAI/bge-reranker-v2-m3"
    top_n: int = 8
    candidate_pool: int = 40
    batch_size: int = 16
    timeout_seconds: float = 20.0


class RetrievalConfig(BaseModel):
    top_k: int = 5
    candidate_top_k: int = 50
    rrf_k: int = 60
    max_context_chars: int = 16000
    bm25_refresh_seconds: float = 30.0
    rerank: RerankConfig = Field(default_factory=RerankConfig)


class AbstentionConfig(BaseModel):
    rerank_min_score: float = 0.25
    fallback_min_dense_score: float = 0.35
    graph_min_paths: int = 1
    handoff_message: str = (
        "Mình chưa tìm thấy đủ căn cứ trong tài liệu để trả lời chắc chắn câu này. "
        "Bạn vui lòng liên hệ quản lý ca/supervisor hoặc bộ phận phụ trách để được hỗ trợ."
    )
    handoff_message_en: str = (
        "I couldn't find enough evidence in the documents to answer this with confidence. "
        "Please contact your shift manager/supervisor or the team responsible."
    )

    def handoff(self) -> str:
        return L(self.handoff_message, self.handoff_message_en)


class RouterConfig(BaseModel):
    graph_enabled: bool = True
    llm_fallback: bool = True
    force_route: Literal["lookup", "multi_hop", "aggregation"] | None = None
    llm_min_confidence: float = 0.5


class GraphLLMExtractionConfig(BaseModel):
    enabled: bool = True
    doc_types: list[str] = Field(default_factory=lambda: ["policy", "training"])
    max_retries: int = 2
    max_chunks_per_document: int = 60


class GraphConfig(BaseModel):
    enabled: bool = True
    name: str = "retail_kb"
    max_hops: int = 2
    max_paths: int = 40
    max_neighbors_per_hop: int = 60
    max_chunks: int = 8
    llm_extraction: GraphLLMExtractionConfig = Field(default_factory=GraphLLMExtractionConfig)

    @field_validator("max_hops")
    @classmethod
    def _hops(cls, v: int) -> int:
        if not 1 <= v <= 2:
            raise ValueError("graph.max_hops chỉ cho phép 1..2 (yêu cầu A4)")
        return v


class AnalyticsConfig(BaseModel):
    enabled: bool = True
    storage_dir: str = "data/tables"
    min_rows: int = 2
    max_rows_per_table: int = 500_000
    max_result_rows: int = 500
    chart_max_points: int = 2000
    query_timeout_seconds: float = 20.0
    describe_with_llm: bool = True
    sql_max_retries: int = 2


class KnowledgeConfig(BaseModel):
    enabled: bool = True
    entity_extraction: bool = True
    max_chunks_per_document: int = 80
    window_chars: int = 2500
    extract_max_tokens: int = 4096  # output tối đa 1 lần trích entity; vẫn bị cắt -> window được chia đôi
    min_confidence: float = 0.5
    summary_max_chars: int = 12000


class CitationConfig(BaseModel):
    required: bool = True
    min_sentence_coverage: float = 0.0
    insufficient_message: str = (
        "Mình không thể đưa ra câu trả lời có trích dẫn nguồn rõ ràng cho câu hỏi này, nên không trả "
        "lời để tránh sai sót. Bạn vui lòng liên hệ supervisor hoặc bộ phận phụ trách."
    )
    insufficient_message_en: str = (
        "I can't give an answer backed by clear sources for this question, so I won't answer to avoid mistakes. "
        "Please contact your supervisor or the team responsible."
    )

    def insufficient(self) -> str:
        return L(self.insufficient_message, self.insufficient_message_en)


class ReviewConfig(BaseModel):
    """Công tắc tổng cho human-in-the-loop; luật cụ thể (từ khoá, vai trò phải duyệt) nằm ở domain pack."""

    enabled: bool = True
    pending_message: str = (
        "Câu hỏi liên quan đến tiền (giá/chiết khấu/hoàn tiền/bồi thường) nên đã được chuyển cho "
        "supervisor duyệt trước khi gửi. Bạn sẽ nhận câu trả lời ngay khi được duyệt."
    )
    pending_message_en: str = (
        "This question involves money (prices/discounts/refunds/compensation), so it has been sent to a "
        "supervisor for approval first. You'll get the answer as soon as it is approved."
    )

    def pending(self) -> str:
        return L(self.pending_message, self.pending_message_en)


class ChatConfig(BaseModel):
    history_max_messages: int = 12
    compaction_token_threshold: int = 6000
    keep_recent_messages: int = 6
    session_idle_days: int = 30


class PromptRef(BaseModel):
    path: str
    sha256: str | None = None


class PromptsConfig(BaseModel):
    answer_system: PromptRef
    router_classify: PromptRef
    entity_extract: PromptRef
    graph_extract: PromptRef
    chat_compaction: PromptRef
    doc_classify: PromptRef
    # Prompt thêm sau bản 2026.10.03-1 có mặc định (sha256 None = không kiểm) để snapshot config cũ vẫn
    # rollback được; config hiện hành luôn khai báo tường minh kèm sha256.
    nl2sql: PromptRef = Field(default_factory=lambda: PromptRef(path="prompts/nl2sql.v1.md"))
    table_describe: PromptRef = Field(default_factory=lambda: PromptRef(path="prompts/table_describe.v1.md"))
    document_profile: PromptRef = Field(default_factory=lambda: PromptRef(path="prompts/document_profile.v1.md"))
    knowledge_extract: PromptRef = Field(default_factory=lambda: PromptRef(path="prompts/knowledge_extract.v1.md"))
    assistant_system: PromptRef = Field(default_factory=lambda: PromptRef(path="prompts/assistant_system.v1.md"))
    report_plan: PromptRef = Field(default_factory=lambda: PromptRef(path="prompts/report_plan.v1.md"))
    data_model_rules: PromptRef = Field(default_factory=lambda: PromptRef(path="prompts/data_model_rules.v1.md"))


class AuthConfig(BaseModel):
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "retailkb"
    jwt_ttl_minutes: int = 720
    default_rate_limit_per_minute: int = 60
    user_rate_limit_per_minute: int = 30


class ObservabilityConfig(BaseModel):
    langfuse_enabled: bool = True
    trace_retention_days: int = 30
    audit_retention_days: int = 365
    abstention_retention_days: int = 180


class AlertsConfig(BaseModel):
    enabled: bool = True
    channel: Literal["slack", "telegram", "generic"] = "slack"
    check_interval_seconds: int = 60
    cooldown_minutes: int = 60
    daily_cost_usd: float = 20.0
    error_rate_5m: float = 0.2
    error_rate_min_requests: int = 10
    abstention_spike_ratio: float = 2.0
    abstention_min_questions: int = 20
    abstention_baseline_days: int = 7
    review_queue_max: int = 30


class ModelingConfig(BaseModel):
    """Mô hình dữ liệu nghiệp vụ (modeling/): file -> bảng warehouse -> quan hệ/logic -> view cho BI."""
    enabled: bool = True
    max_rows_per_table: int = 1_000_000
    schema_prefix: str = "dm_"           # mỗi mô hình 1 schema Postgres: <prefix><id 10 ký tự>
    reader_role: str | None = None       # role read-only cho BI (Superset) — được GRANT USAGE/SELECT nếu tồn tại
    ai_suggestions: bool = True
    ai_max_tokens: int = 6000
    publish_to_analytics: bool = True    # sau khi build: view -> bảng số liệu để hỏi đáp/khám phá trong app


class WorkerConfig(BaseModel):
    poll_interval_seconds: float = 2.0
    max_attempts: int = 3
    stale_lock_minutes: int = 30
    retention_interval_hours: int = 6


class AppConfig(BaseModel):
    config_version: str
    description: str = ""
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    abstention: AbstentionConfig = Field(default_factory=AbstentionConfig)
    router: RouterConfig = Field(default_factory=RouterConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    citation: CitationConfig = Field(default_factory=CitationConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    prompts: PromptsConfig
    auth: AuthConfig = Field(default_factory=AuthConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    modeling: ModelingConfig = Field(default_factory=ModelingConfig)

    # Tính lúc load, không đọc từ YAML.
    config_hash: str = ""
    source_path: str = ""
    prompt_hashes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _graph_router_consistency(self) -> "AppConfig":
        if not self.graph.enabled:
            self.router.graph_enabled = False
        return self

    def price_for(self, model: str) -> ModelPrice | None:
        if model in self.llm.pricing:
            return self.llm.pricing[model]
        for key, price in self.llm.pricing.items():  # khớp tiền tố, vd claude-haiku-4-5-20251001
            if model.startswith(key):
                return price
        return None

    def public_view(self) -> dict[str, Any]:
        """Bản hiển thị cho GET /config — không có secret (config.yaml vốn không chứa secret)."""
        data = self.model_dump(exclude={"source_path"})
        data["source_path"] = os.path.relpath(self.source_path, REPO_ROOT) if self.source_path else ""
        return data


class Settings(BaseModel):
    """Secret + endpoint đọc từ biến môi trường — KHÔNG bao giờ nằm trong config.yaml."""

    database_url: str = "postgresql+psycopg://retail:retail@localhost:5432/retailkb"
    anthropic_api_key: str | None = None
    # anthropic (API key, mặc định) | claude_cli (gọi Claude Code CLI đăng nhập subscription — CHỈ cho máy dev)
    llm_provider: str = "anthropic"
    claude_cli_path: str | None = None
    jwt_secret: str | None = None
    bootstrap_admin_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None
    langfuse_database_url: str | None = None
    alert_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    app_env: str = "dev"
    public_base_url: str = "http://localhost:8000"
    langfuse_public_url: str | None = None
    langfuse_project_id: str | None = None
    # warehouse cho mô hình dữ liệu (Superset đọc ở đây); trống = dùng chung DATABASE_URL
    warehouse_url: str | None = None
    # chuỗi kết nối hiển thị cho người dùng BI (không chứa mật khẩu), vd postgresql://bi_reader@db.internal:5432/retailkb
    warehouse_public_dsn: str | None = None

    @property
    def llm_available(self) -> bool:
        """Có đường gọi model: API key, hoặc Claude Code CLI đăng nhập subscription (máy dev)."""
        return bool(self.anthropic_api_key) or self.llm_provider == "claude_cli"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            database_url=env.get("DATABASE_URL", cls.model_fields["database_url"].default),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
            llm_provider=(env.get("LLM_PROVIDER") or "anthropic").strip().lower(),
            claude_cli_path=env.get("CLAUDE_CLI_PATH") or None,
            jwt_secret=env.get("JWT_SECRET") or None,
            bootstrap_admin_api_key=env.get("BOOTSTRAP_ADMIN_API_KEY") or None,
            langfuse_public_key=env.get("LANGFUSE_PUBLIC_KEY") or None,
            langfuse_secret_key=env.get("LANGFUSE_SECRET_KEY") or None,
            langfuse_host=env.get("LANGFUSE_HOST") or None,
            langfuse_database_url=env.get("LANGFUSE_DATABASE_URL") or None,
            alert_webhook_url=env.get("ALERT_WEBHOOK_URL") or None,
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID") or None,
            app_env=env.get("APP_ENV", "dev"),
            public_base_url=env.get("PUBLIC_BASE_URL", "http://localhost:8000"),
            langfuse_public_url=env.get("LANGFUSE_PUBLIC_URL") or env.get("LANGFUSE_HOST") or None,
            langfuse_project_id=env.get("LANGFUSE_PROJECT_ID") or None,
            warehouse_url=env.get("WAREHOUSE_URL") or None,
            warehouse_public_dsn=env.get("WAREHOUSE_PUBLIC_DSN") or None,
        )

    @property
    def psycopg_dsn(self) -> str:
        """DSN thuần cho psycopg (bỏ tiền tố driver của SQLAlchemy)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


# =====================================================================================
# LOAD
# =====================================================================================

def _interpolate_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            env_val = os.environ.get(name)
            if env_val is None or env_val == "":
                if default is None:
                    raise ConfigError(f"Biến môi trường {name} được config tham chiếu nhưng chưa set",
                                      context={"var": name})
                return default
            return env_val
        return _ENV_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else REPO_ROOT / path


def _verify_prompts(cfg: AppConfig, strict: bool) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, ref in cfg.prompts:
        path = _resolve_path(ref.path)
        if not path.is_file():
            raise ConfigError(f"Prompt '{name}' không tồn tại: {ref.path}", context={"prompt": name})
        actual = file_sha256(path)
        if strict and ref.sha256 and ref.sha256 != actual:
            raise ConfigError(
                f"Prompt '{name}' ({ref.path}) đã bị sửa nhưng sha256 trong config chưa cập nhật "
                f"(config={ref.sha256[:12]}…, thực tế={actual[:12]}…). Tạo file prompt mới + bump "
                "config_version, hoặc chạy `python -m core.config hash-prompts` nếu thay đổi là chủ ý.",
                context={"prompt": name, "expected": ref.sha256, "actual": actual},
            )
        hashes[name] = actual
    return hashes


def config_path_for_version(version: str | None) -> Path:
    if not version:
        return Path(os.environ.get("RETAILKB_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    path = VERSIONS_DIR / f"{version}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in VERSIONS_DIR.glob("*.yaml")) if VERSIONS_DIR.is_dir() else []
        raise ConfigError(f"Không có snapshot config version {version!r}. Có sẵn: {available}",
                          context={"version": version})
    return path


def load_config(path: Path | str | None = None, *, strict_prompts: bool = True) -> AppConfig:
    path = Path(path) if path else config_path_for_version(os.environ.get("RETAILKB_CONFIG_VERSION"))
    if not path.is_file():
        raise ConfigError(f"Không tìm thấy file config: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"config YAML lỗi cú pháp: {e}", context={"path": str(path)}) from e
    raw = _interpolate_env(raw)
    try:
        cfg = AppConfig(**raw)
    except Exception as e:  # pydantic.ValidationError — giữ message gốc, có đủ field lỗi
        raise ConfigError(f"config không hợp lệ: {e}", context={"path": str(path)}) from e
    cfg.prompt_hashes = _verify_prompts(cfg, strict=strict_prompts)
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
    cfg.config_hash = hashlib.sha256((canonical + json.dumps(cfg.prompt_hashes, sort_keys=True)).encode()).hexdigest()[:16]
    cfg.source_path = str(path)
    return cfg


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def read_prompt(name: str, cfg: AppConfig | None = None) -> str:
    cfg = cfg or get_config()
    ref: PromptRef = getattr(cfg.prompts, name)
    return _resolve_path(ref.path).read_text(encoding="utf-8")


def reset_caches() -> None:
    """Dùng trong test hoặc khi reload config bằng tay."""
    get_config.cache_clear()
    get_settings.cache_clear()


# =====================================================================================
# CLI: check | hash-prompts | snapshot | versions
# =====================================================================================

def _cmd_hash_prompts(path: Path, write: bool) -> int:
    cfg = load_config(path, strict_prompts=False)
    text = path.read_text(encoding="utf-8")
    for name, digest in cfg.prompt_hashes.items():
        print(f"{name}: {digest}")
        ref = getattr(cfg.prompts, name)
        if write and ref.sha256 != digest:
            if ref.sha256:
                text = text.replace(ref.sha256, digest)
            else:
                text = text.replace(f"path: {ref.path}\n    sha256: \"\"", f"path: {ref.path}\n    sha256: {digest}")
    if write:
        path.write_text(text, encoding="utf-8")
        print(f"Đã cập nhật sha256 trong {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quản lý config RetailKB (F1).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check", help="Validate config + hash prompt, in version/hash.")
    p_check.add_argument("--path", default=None)
    p_hash = sub.add_parser("hash-prompts", help="In sha256 của các prompt đang tham chiếu.")
    p_hash.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    p_hash.add_argument("--write", action="store_true", help="Ghi đè sha256 lệch vào file config.")
    p_snap = sub.add_parser("snapshot", help="Chép config.yaml hiện tại sang config/versions/<version>.yaml")
    p_snap.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    sub.add_parser("versions", help="Liệt kê snapshot có sẵn để rollback.")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "check":
            cfg = load_config(Path(args.path) if args.path else None)
            print(json.dumps({"config_version": cfg.config_version, "config_hash": cfg.config_hash,
                              "source": cfg.source_path, "prompts": cfg.prompt_hashes}, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "hash-prompts":
            return _cmd_hash_prompts(Path(args.path), args.write)
        if args.cmd == "snapshot":
            cfg = load_config(Path(args.path))
            VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
            dest = VERSIONS_DIR / f"{cfg.config_version}.yaml"
            if dest.exists() and dest.read_bytes() != Path(args.path).read_bytes():
                print(f"LỖI: {dest} đã tồn tại với nội dung KHÁC — bump config_version trước khi snapshot.",
                      file=sys.stderr)
                return 1
            shutil.copyfile(args.path, dest)
            print(f"Đã snapshot -> {dest}")
            return 0
        if args.cmd == "versions":
            for p in sorted(VERSIONS_DIR.glob("*.yaml")):
                print(p.stem)
            return 0
    except ConfigError as e:
        print(f"LỖI CONFIG: {e.message}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
