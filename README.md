# Kho Dữ Liệu

Nền tảng **khai phá dữ liệu từ file tải lên**: người dùng tải mọi loại tài liệu (Excel/CSV, Word, PDF kể cả
scan, PowerPoint, ảnh, audio/video, text) vào **workspace** của mình; hệ thống tự trích nội dung, **bảng số
liệu** (DuckDB), **tóm tắt + hồ sơ ngữ nghĩa** từng file và **đồ thị tri thức** (thực thể/quan hệ, schema mở).
Trên web app người dùng hỏi đáp mọi kiểu câu hỏi (tổng quan, tra cứu, tóm tắt, thống kê/so sánh, mối liên hệ)
với **trích dẫn bắt buộc**, truy vấn số liệu bằng ngôn ngữ tự nhiên hoặc SQL, duyệt đồ thị tương tác và **tạo
báo cáo** (AI soạn từ dữ liệu thật, ghim kết quả chat, xuất PDF/DOCX/XLSX).

Lõi là generic; **domain pack** (hiện có `generic` và `retail`) thêm phân loại tài liệu, câu hỏi gợi ý, chính sách
duyệt câu trả lời liên quan tiền và graph quy tắc (Apache AGE) cho ngành bán lẻ.

## Khởi động nhanh

```bash
cp .env.example .env
```

Điền các biến **[BẮT BUỘC]** trong `.env` (bảng dưới), rồi:

```bash
make up
```

Mở **http://localhost:3000**, đăng nhập bằng `BOOTSTRAP_ADMIN_EMAIL`/`BOOTSTRAP_ADMIN_PASSWORD` (superadmin) hoặc
**Đăng ký** tài khoản mới (tạo workspace đầu tiên ngay trong màn hình đăng ký) → *Tài liệu* → **Tải tài liệu**.
Mỗi file là một job nền; xong bước nào UI cập nhật bước đó (nội dung → bảng số liệu → tóm tắt → thực thể).

Muốn có dữ liệu mẫu (25 file GreenMart, pack retail): `make ingest` nạp vào workspace slug `default` (tạo nếu chưa có),
`make ingest ONLY="xlsx docx"` để bỏ audio/video (speech-to-text CPU chậm). Thêm tài khoản của bạn vào workspace đó
bằng *Cài đặt → Thành viên* khi đăng nhập superadmin.

| Service | URL | Ghi chú |
|---|---|---|
| Web app | http://localhost:3000 | React + nginx, proxy `/api` → api (cùng origin) |
| API + OpenAPI | http://localhost:8000/docs | `Authorization: Bearer <JWT>` (đăng nhập) hoặc `X-API-Key` (key theo workspace) |
| Langfuse (trace LLM) | http://localhost:3001 | Đăng nhập `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` |

Kiểm tra: `make ps` (healthcheck), `curl -s localhost:8000/readyz`, trang *Tổng quan* của workspace (KB health).

### Biến môi trường

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `POSTGRES_PASSWORD` | ✅ | Mật khẩu Postgres (DB `retailkb` + DB `langfuse`) |
| `ANTHROPIC_API_KEY` | ✅ | Trợ lý trả lời/tool-use, NL→SQL, tóm tắt + hồ sơ tài liệu, trích thực thể, vision ảnh/PDF scan, lập dàn ý báo cáo |
| `JWT_SECRET` | ✅ | Ký JWT cho user (≥ 32 ký tự ngẫu nhiên) |
| `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` | ✅ | Tài khoản superadmin đầu tiên (migration tạo khi DB trống) |
| `BOOTSTRAP_ADMIN_API_KEY` | ✅ | API key hệ thống (`rk_...`, quyền superadmin) cho script/CI |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | ✅ | Key project Langfuse (tự tạo khi Langfuse khởi động) |
| `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_SALT`, `LANGFUSE_ADMIN_PASSWORD` | ✅ | Secret nội bộ + mật khẩu đăng nhập Langfuse UI |
| `ALERT_CHANNEL`, `ALERT_WEBHOOK_URL` / `TELEGRAM_*` | | Kênh alert (Slack/Telegram/generic) — trống = alert chỉ ghi log + bảng `alert_events` |
| `RETAILKB_CONFIG_VERSION` | | Chọn snapshot config để **rollback** (trống = `config.yaml`) |
| `APP_ENV`, `CORS_ORIGINS` | | **Production: `APP_ENV=prod`**; CORS chỉ cần khi web chạy khác origin với api |
| `TABLES_DIR`, `UPLOAD_DIR` | | Thư mục parquet bảng số liệu / file upload (compose: volume) |
| `EMBEDDING_PROVIDER`, `RERANK_ENABLED`, `GRAPH_ENABLED`, `INGEST_SANDBOX_MODE` | | Ghi đè nhanh tham số trong `config.yaml` |

Mọi tham số ảnh hưởng kết quả (model từng vai trò, chunking, embedding, top_k, rerank, ngưỡng abstention,
analytics, knowledge, prompt + sha256) nằm trong [`config.yaml`](config.yaml) có version — xem `GET /config`.
Secret chỉ ở `.env`.

## Người dùng làm được gì (web app)

Giao diện song ngữ **Tiếng Việt / English** (menu người dùng), viết cho người dùng nghiệp vụ; SQL, mã định danh, trace thô,
việc nền, chi phí chỉ hiện khi bật **Chi tiết kỹ thuật**. Hướng dẫn thiết kế & viết chữ: [`web/DESIGN.md`](web/DESIGN.md).

| Trang | Chức năng |
|---|---|
| Trang chủ | Ô hỏi trung tâm; **dải truy vết** câu trả lời gần nhất (hiểu → tra ở đâu → tìm được gì → kiểm chứng → tin cậy); các lớp bảo đảm đo trên dữ liệu thật (tỉ lệ có nguồn, số lần từ chối đoán, câu trả lời có số liệu tính bằng SQL, kết hợp nhiều nguồn, người duyệt, mâu thuẫn, tốc độ); kho tri thức theo loại nguồn |
| Hỏi đáp | Chat nhiều lượt (SSE): trợ lý dùng công cụ thật (tài liệu: tìm từ khoá + ngữ nghĩa; bảng: tính bằng SQL; bản đồ tri thức); chip trích dẫn mở đoạn gốc; dải truy vết từng câu; 👍/👎; thêm vào báo cáo; giới hạn theo file; chế độ *Kỹ lưỡng* / *Tra nhanh* |
| Nguồn dữ liệu | Tải nhiều file; "những gì đã tìm thấy trong file" (tóm tắt, bảng, đối tượng, đoạn nội dung, thời lượng chép lời); phiên bản, duyệt, xử lý lại, xoá mềm/khôi phục |
| **Mô hình dữ liệu** | Workbook → mỗi sheet 1 bảng Postgres → quan hệ + logic từng cột (đọc công thức Excel/pivot, dò trên dữ liệu, AI đề xuất — tất cả kiểm chứng trên dữ liệu) → người dùng duyệt bằng câu nghiệp vụ / mô tả bằng lời / form có hướng dẫn → view tính lại cho BI (Superset) + đối chiếu từng dòng với Excel + tài liệu quan hệ (Word/PDF/Markdown, vi/en) — xem mục 15 trong [OPERATIONS](docs/OPERATIONS.md) |
| Khám phá số liệu | Hỏi số liệu bằng lời → biểu đồ + giải thích; duyệt bảng (cột, độ đầy, khoảng giá trị); export; câu hỏi đã lưu |
| Báo cáo | AI lập dàn ý và viết từng mục bằng công cụ thật; chỉnh sửa kiểu trình soạn thảo; xuất PDF/DOCX/XLSX/HTML |
| Bản đồ tri thức | Người, tổ chức, sản phẩm, địa điểm… và liên kết giữa chúng; nơi được nhắc tới kèm đoạn trích; sửa/gộp/xoá |
| Trung tâm tin cậy (editor+) | Các lớp bảo đảm đang hoạt động; duyệt câu trả lời nhạy cảm; nguồn mâu thuẫn; câu hỏi thiếu tài liệu; phản hồi; (kỹ thuật) nhật ký hỏi đáp, việc nền, chi phí AI |
| Cài đặt | Workspace, thành viên & quyền, kết nối hệ thống khác (API key), tài khoản (ngôn ngữ, chi tiết kỹ thuật) |
| Quản trị hệ thống (superadmin) | Người dùng, workspaces, việc nền toàn hệ thống, chi phí AI, config đang chạy |

Nguyên tắc không đổi: mọi khẳng định dựa trên tài liệu phải có `[n]` trỏ tới nguồn (code kiểm tra, không phải model);
không đủ căn cứ → từ chối và ghi nhận để biết KB thiếu gì; mâu thuẫn giữa nguồn được nêu kèm ngày hiệu lực;
câu hỏi về tiền (theo domain pack) qua người duyệt; số liệu luôn tính bằng SQL trên bảng thật, không để model "đoán".

## Kiến trúc

```
 web (React/Vite, nginx /api) ──► api (FastAPI: JWT/API key theo workspace, role viewer<editor<owner, rate limit)
   │ /chat (SSE) · /ask · /analytics · /entities · /reports · /documents · /ingest · /ops …
   ▼
 assistant agent (Claude tool-use, stream): search_documents (hybrid pgvector+BM25 → RRF → rerank → abstention)
   · get_document_overview · list_tables/query_tables (DuckDB read-only, Vega-Lite) · explore_graph · read_source
   → post-check citation (code) → confidence → decision trace → qa_traces + Langfuse
 upload ─► api ─► jobs (Postgres SKIP LOCKED) ─► worker ─► gateway ─► parser sidecar (KHÔNG network)
   └► record → chunk → embed → replace/dedup → conflict → [graph_sync (pack retail, AGE)]
   └► knowledge_extract: bảng → parquet + mô tả · tóm tắt + hồ sơ ngữ nghĩa · thực thể/quan hệ (schema mở)
 report_generate: prompt → dàn ý (LLM) → mỗi mục chạy assistant → block (markdown+citation, chart, table, sources)
 Postgres 16: pgvector + Apache AGE + bảng accounts/workspaces/KB/analytics/knowledge/reports/QA/jobs + DB Langfuse
```

| Phần | Mã nguồn chính |
|---|---|
| Tài khoản, workspace, phân quyền | `accounts/`, `core/principal.py`, `api/auth.py`, `api/routes/accounts.py`, `domains/packs.py` |
| Ingest & KB lifecycle | `ingest/`, `worker/`, `kb/` (lifecycle, reindex, health), `security/` (gateway, sandbox, audit) |
| Bảng số liệu & phân tích | `analytics/tables.py` (trích bảng → parquet), `engine.py` (DuckDB, validate SQL), `nl2sql.py`, `charts.py`, `api/routes/analytics.py` |
| Hồ sơ tài liệu & đồ thị tri thức | `knowledge/` (pipeline, profile, extract, service), `api/routes/knowledge.py` |
| Trợ lý & QA | `assistant/` (agent, tools, sources), `qa/` (retrieval, citation, abstention, conflicts, trace, review, sessions, answer = pipeline strict) |
| Graph quy tắc (pack retail) | `graph/` (build, extract, resolve, query, router, sync, store = AGE) |
| Báo cáo | `reports/generator.py`, `reports/export.py`, `api/routes/reports.py` |
| Mô hình dữ liệu | `modeling/` (workbook, excel_formula, expr, infer, ai, compile, evaluate, warehouse, validate, explain, document, service), `api/routes/models.py` |
| Số liệu năng lực/tin cậy | `api/routes/insights.py` |
| Observability & vận hành | `observability/` (Langfuse, alerts, costs, retention), `config.yaml` + `config/`, `prompts/`, `docker-compose.yml`, `docker/`, `Makefile`, `scripts/`, [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| Web app | `web/` (React 18 + TypeScript + Vite + Tailwind, TanStack Query, vega-embed, react-force-graph) |

Bảo mật: mọi tool call của pipeline ingest đi qua `security/gateway.py` (policy + audit append-only xoay theo tháng);
parser chạy trong container không mạng, rootfs read-only, non-root; nội dung tài liệu đưa vào context bọc
`<untrusted_document>`; nội dung nghi prompt injection → file bị chặn chờ duyệt; SQL do model sinh chỉ chạy qua
validator (một `SELECT`/`WITH`, denylist, DuckDB không truy cập ngoài, timeout); mọi dữ liệu scope theo workspace.

## Chạy test

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
```

```bash
make test
```

Integration test (Postgres + pgvector + Apache AGE thật — tài khoản/workspace, ingest, lifecycle, analytics,
knowledge graph, assistant, reports, API, SSE, alert, retention) cần DSN tới DB tên kết thúc `_test`:

```bash
RETAILKB_TEST_PG_DSN=postgresql://retail:<mật_khẩu>@localhost:5432/retailkb_test make test-integration
```

(Tạo DB trước: `docker compose exec postgres createdb -U retail retailkb_test`.) Test KHÔNG gọi LLM thật — model
được giả lập; không có benchmark/eval theo yêu cầu.

Phát triển local không Docker: api `uvicorn api.main:app --reload`, worker `python -m worker.main`, web
`make web-dev` (Vite :5173, proxy `/api` → `localhost:8000`).
