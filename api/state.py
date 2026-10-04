"""Khởi tạo các service dùng chung cho tiến trình api (1 lần lúc startup)."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from core.config import AppConfig, Settings, get_config, get_settings
from core.db import get_sessionmaker, session_scope
from core.llm import LLMClient, get_llm
from core.logging import log_event
from core.models import ConfigVersionRow, utcnow
from graph.store import GraphStore, get_graph_store
from index.bm25_cache import BM25Cache
from index.embeddings import get_embedder
from index.kb_store import KBVectorStore
from qa.answer import QAService
from qa.retrieval import VectorRetriever

logger = logging.getLogger(__name__)


@dataclass
class Services:
    cfg: AppConfig
    settings: Settings
    sm: sessionmaker
    llm: LLMClient
    embedder: Any
    bm25: BM25Cache
    retriever: VectorRetriever
    qa: QAService

    def graph_store(self) -> GraphStore | None:
        return get_graph_store() if self.cfg.graph.enabled else None


def record_config_version(cfg: AppConfig) -> None:
    """Lưu bản config đang chạy (hash) — tra lại được request cũ chạy bằng config nào (F1/E2)."""
    with session_scope() as s:
        s.execute(insert(ConfigVersionRow).values(
            config_hash=cfg.config_hash, config_version=cfg.config_version, source_path=cfg.source_path,
            content=cfg.public_view(), first_loaded_at=utcnow(), last_loaded_at=utcnow(),
        ).on_conflict_do_update(index_elements=["config_hash"], set_={"last_loaded_at": utcnow()}))


def build_services(*, warmup: bool = True) -> Services:
    cfg, settings = get_config(), get_settings()
    sm = get_sessionmaker()
    llm = get_llm()
    embedder = get_embedder(cfg)
    bm25 = BM25Cache(sm, cfg.retrieval.bm25_refresh_seconds)
    retriever = VectorRetriever(cfg, KBVectorStore(sm, embedder.model_name, embedder.dim), bm25.get, embedder)
    qa = QAService(cfg, sm, llm, retriever, lambda: get_graph_store() if cfg.graph.enabled else None)
    services = Services(cfg, settings, sm, llm, embedder, bm25, retriever, qa)
    if warmup:
        retriever.warmup_reranker()

        def _warm() -> None:
            try:
                embedder.embed_query("khởi động")
                bm25.get()
                log_event(logger, "warmup_done", embedding_model=embedder.model_name)
            except Exception:
                logger.exception("warmup lỗi (request đầu tiên sẽ tự tải lại)")
        threading.Thread(target=_warm, daemon=True, name="warmup").start()
    try:
        record_config_version(cfg)
    except Exception:
        logger.exception("không ghi được config_versions (DB chưa sẵn sàng?)")
    log_event(logger, "api_started", config_version=cfg.config_version, config_hash=cfg.config_hash,
              embedding_model=embedder.model_name, graph=cfg.graph.enabled, rerank=cfg.retrieval.rerank.enabled)
    return services
