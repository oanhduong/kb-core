# RetailKB Copilot

Biến file thô của khách hàng ngành bán lẻ (docx, xlsx, pdf, ảnh, audio, video) thành knowledge base có
cấu trúc + metadata graph, phục vụ chatbot hỗ trợ nhân viên cửa hàng: trả lời **có trích dẫn bắt buộc**,
từ chối khi không đủ căn cứ, nêu rõ mâu thuẫn giữa các nguồn, câu hỏi liên quan tiền qua supervisor duyệt.

## Khởi động nhanh

```bash
cp .env.example .env
```

Điền các biến **[BẮT BUỘC]** trong `.env` (bảng dưới), rồi:

```bash
make up
```

```bash
make ingest
```

`make up` build + chạy toàn bộ (migration Alembic tự chạy trước api/worker). `make ingest` nạp bộ dữ liệu
mẫu `data/raw` (25 file GreenMart) và chờ tới khi xử lý xong — audio/video chạy speech-to-text trên CPU
nên mất thêm vài phút; muốn nhanh: `make ingest ONLY="xlsx docx"`. Sau đó:

| Service | URL | Ghi chú |
|---|---|---|
| Chat UI (nhân viên) | http://localhost:3000 | Dev: đăng nhập nhanh bằng mã NV + cửa hàng (`APP_ENV=dev`) |
| Admin UI (Streamlit) | http://localhost:8501 | Dùng `ADMIN_API_KEY`/`BOOTSTRAP_ADMIN_API_KEY` |
| API + OpenAPI | http://localhost:8000/docs | `X-API-Key` hoặc `Authorization: Bearer <JWT>` |
| Langfuse (trace) | http://localhost:3001 | Đăng nhập `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` |

Ingest file của bạn: `make ingest FILE=duong/dan/bang_gia_thang_9.xlsx TITLE="Bảng giá tháng 9" EFFECTIVE_DATE=2026-09-01`.
Kiểm tra: `make ps` (healthcheck), `curl -s localhost:8000/readyz`, `make kb-health`.

### Biến môi trường

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `POSTGRES_PASSWORD` | ✅ | Mật khẩu Postgres (DB `retailkb` + DB `langfuse`) |
| `ANTHROPIC_API_KEY` | ✅ | Trả lời, router/entity khi rule không chắc, vision ảnh/PDF scan, LLM extraction graph |
| `JWT_SECRET` | ✅ | Ký JWT cho user (≥ 32 ký tự ngẫu nhiên) |
| `BOOTSTRAP_ADMIN_API_KEY` | ✅ | API key admin khởi tạo (`rk_...`), dùng tạo key/token khác + Admin UI |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | ✅ | Key project Langfuse (tự tạo khi Langfuse khởi động) |
| `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_SALT`, `LANGFUSE_ADMIN_PASSWORD` | ✅ | Secret nội bộ + mật khẩu đăng nhập Langfuse UI |
| `ADMIN_API_KEY` | | Key riêng cho Admin UI (mặc định = bootstrap key) |
| `ALERT_CHANNEL`, `ALERT_WEBHOOK_URL` / `TELEGRAM_*` | | Kênh alert (Slack/Telegram/generic) — trống = alert chỉ ghi log + bảng `alert_events` |
| `RETAILKB_CONFIG_VERSION` | | Chọn snapshot config để **rollback** (trống = `config.yaml`) |
| `APP_ENV`, `DEV_LOGIN_ENABLED` | | `dev` + `true` bật đăng nhập nhanh ở chat UI. **Production: `APP_ENV=prod`** |
| `EMBEDDING_PROVIDER`, `RERANK_ENABLED`, `GRAPH_ENABLED`, `INGEST_SANDBOX_MODE` | | Ghi đè nhanh tham số trong `config.yaml` |

Mọi tham số ảnh hưởng kết quả (model từng vai trò, chunking, embedding, top_k, rerank, ngưỡng abstention,
router, prompt + sha256) nằm trong [`config.yaml`](config.yaml) — xem `GET /config`. Secret chỉ ở `.env`.

## Kiến trúc

```
                ┌────────── chat-ui (React, nginx /api proxy) ──┐   admin-ui (Streamlit)
 nhân viên ───► │  POST /chat (SSE) · /ask · /feedback          │──► api (FastAPI) ◄── API key / JWT, rate limit
                └───────────────────────────────────────────────┘        │
   router (rule → LLM) ─┬─ lookup ──► hybrid (pgvector dense ∥ BM25) → RRF → rerank bge-m3 ─┐
                        ├─ multi_hop ─► graph local search (Apache AGE, ≤2 hop) ───────────┤
                        └─ aggregation ► graph đếm/liệt kê (computed fact) ───────────────┤
          parent chunk → context (nội dung bọc <untrusted_document>) → Claude → post-check citation (code)
          → công bố mâu thuẫn → review queue (câu hỏi tiền) → decision trace → qa_traces + Langfuse
 upload ─► api ─► jobs (Postgres, SKIP LOCKED) ─► worker ─► gateway ─► parser sidecar (KHÔNG network)
                                                   └► record → chunk → embed → replace/dedup → conflict → graph sync
 Postgres 16: pgvector + Apache AGE (graph `retail_kb`) + bảng KB/QA/jobs + DB Langfuse
```

| Phần | Mã nguồn chính |
|---|---|
| A. Graph | `graph/build.py` (rule), `graph/extract.py` (LLM, schema cố định), `graph/resolve.py` (alias DB), `graph/query.py`, `graph/router.py`, `graph/sync.py` (incremental), `graph/store.py` (AGE) |
| B. QA | `qa/answer.py` (pipeline + SSE), `qa/retrieval.py`, `qa/citation.py`, `qa/abstention.py`, `qa/conflicts.py`, `qa/trace.py`, `qa/review.py`, `qa/sessions.py` |
| C. KB lifecycle | `ingest/pipeline.py`, `ingest/service.py`, `worker/`, `kb/lifecycle.py`, `kb/reindex.py`, `kb/health.py`, `kb/service.py` |
| D. API & UI | `api/` (routes, auth, ratelimit, sse), `admin_ui/`, `chat_ui/` |
| E. Observability | `observability/tracing.py` (Langfuse), `alerts.py`, `costs.py`, `retention.py` |
| F. Vận hành | `config.yaml`, `config/`, `prompts/`, `docker-compose.yml`, `docker/`, `Makefile`, `scripts/backup.sh`, `scripts/restore.sh`, [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| Đã có từ trước | `ingest/parsers.py`, `ingest/chunkers.py`, `agent/` (ingest agent, `ingest.mode=agent`), `security/` (gateway, policy, sandbox, provenance, audit), `index/` (hybrid, rerank), `bench/` + `ops/docker-compose.yml` (bench pgvector/qdrant cũ, không dùng khi vận hành) |

Bảo mật (giữ nguyên + mở rộng): mọi tool call của agent **và** pipeline ingest đi qua `security/gateway.py`
(policy READ / WRITE_REVERSIBLE dry-run→commit / WRITE_IRREVERSIBLE cần duyệt), audit append-only xoay vòng
theo tháng; parser chạy trong container `parser` không có mạng, rootfs read-only, non-root; nội dung tài
liệu đưa vào context (cả ingest lẫn QA) bọc `<untrusted_document>`; nội dung nghi prompt injection → file
bị chặn chờ admin duyệt.

## Chạy test

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
```

```bash
make test
```

Integration test (Postgres + pgvector + Apache AGE thật — ingest, thay thế/lịch sử, dedup, QA end-to-end,
API, SSE, alert, retention) cần DSN tới Postgres có AGE, ví dụ container `postgres` của compose:

```bash
RETAILKB_TEST_PG_DSN=postgresql://retail:<mật_khẩu>@localhost:5432/retailkb_test make test-integration
```

(Tạo DB trước: `docker compose exec postgres createdb -U retail retailkb_test`.) Test KHÔNG gọi LLM thật —
model được giả lập; phần gọi LLM không có test theo yêu cầu.

Phát triển local không Docker: chat UI `npm --prefix chat_ui run dev` (proxy `/api` → `localhost:8000`),
admin UI `streamlit run admin_ui/app.py`, api `uvicorn api.main:app --reload`, worker `python -m worker.main`.
