# Config changelog

Mỗi lần đổi `config.yaml`: bump `config_version`, thêm 1 mục ở ĐẦU file này, chạy `make config-snapshot`.
Rollback: `RETAILKB_CONFIG_VERSION=<version>` trong `.env` rồi `make restart`.

## 2026.10.19-1
- Đổi tên sản phẩm thành **Kho Dữ Liệu**: prompt `answer_system.v2` và `chat_compaction.v2` (chỉ thay tên trợ lý,
  nội dung giữ nguyên v1). Rollback về `2026.10.05-2` để dùng lại prompt v1.

## 2026.10.05-2
- Mô hình dữ liệu nghiệp vụ (`modeling.*`): file Excel/CSV -> mỗi sheet 1 bảng trong schema `dm_<id>` của warehouse ->
  quan hệ + logic từng cột (công thức Excel, pivot, mẫu dữ liệu, AI đề xuất — đều kiểm chứng trên dữ liệu) -> người dùng
  duyệt -> view tính lại + đối chiếu với file gốc + tài liệu quan hệ. Model `data_model` (sonnet) + prompt
  `data_model_rules.v1`. Env mới (tuỳ chọn): `WAREHOUSE_URL`, `WAREHOUSE_PUBLIC_DSN`.
- `analytics/charts.py`: bảng màu biểu đồ theo hệ màu mới của web.

## 2026.10.05-1
- `knowledge.extract_max_tokens: 4096` (trước đây cố định 1800 trong code). Tài liệu dày đặc thực thể (catalogue,
  ảnh chụp POS, biên bản liệt kê hàng) làm output JSON bị cắt -> mất toàn bộ entity của window; nay nâng giới hạn và
  window lỗi được chia đôi trích lại.

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
