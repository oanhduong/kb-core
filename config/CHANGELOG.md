# Config changelog

Mỗi lần đổi `config.yaml`: bump `config_version`, thêm 1 mục ở ĐẦU file này, chạy `make config-snapshot`.
Rollback: `RETAILKB_CONFIG_VERSION=<version>` trong `.env` rồi `make restart`.

## 2026.10.04-1
- Nền tảng tổng quát: thêm `analytics.*` (bảng -> DuckDB, SQL read-only, chart), `knowledge.*` (knowledge graph mở,
  hồ sơ ngữ nghĩa), model cho vai trò nl2sql/table_describe/document_profile/knowledge_extract/assistant/report,
  `llm.assistant_max_tokens`, 6 prompt mới (v1). `review.*` chỉ còn công tắc tổng — luật theo domain pack.

## 2026.10.03-1
- Bản đầu tiên chạy production.
- Model: answer `claude-sonnet-4-6`; router/entity/graph-extract/compaction `claude-haiku-4-5`.
- Embedding `intfloat/multilingual-e5-base` (768 chiều); rerank `BAAI/bge-reranker-v2-m3` top_n=8.
- Chunking `parent_child` 1024/256 token.
- Abstention: rerank_min_score=0.25 (sigmoid), fallback dense 0.35.
- Prompt v1 cho cả 6 vai trò.
