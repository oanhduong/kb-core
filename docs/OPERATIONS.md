# Vận hành Kho Dữ Liệu

Tài liệu cho người vận hành (owner/editor workspace, superadmin). Lệnh `make` chạy từ thư mục repo trên máy chủ chạy
docker-compose. Mọi thao tác trên web app (http://localhost:3000 — *Tài liệu*, *Giám sát & duyệt*, *Cài đặt*, *Quản trị hệ thống*)
đều có API tương ứng (http://localhost:8000/docs, đường dẫn theo workspace `/w/{workspace_id}/...`) và được ghi audit log append-only (`data/security/audit/audit_trail-YYYY-MM.log` trong volume `audit`).

Mục lục: [1. Thêm tài liệu](#1-thêm-tài-liệu-mới) · [2. File blocked](#2-xử-lý-file-blocked) ·
[3. Sửa record sai](#3-sửa-record-bị-extract-sai) · [4. Conflict](#4-xử-lý-conflict) ·
[5. Đổi embedding/chunking](#5-đổi-embedding-model-hoặc-chunking--reindex) · [6. Rollback config](#6-đổi-và-rollback-config) ·
[7. Đọc trace](#7-user-báo-trả-lời-sai--đọc-trace) · [8. Review queue](#8-review-queue-câu-hỏi-tiền) ·
[9. Alert](#9-alert-và-cách-phản-ứng) · [10. Backup/restore](#10-backup--restore) · [11. Graph](#11-graph) · [12. Khác](#12-việc-định-kỳ--khác) ·
[13. Workspace & tài khoản](#13-workspace-tài-khoản-phân-quyền) · [14. Bảng số liệu, đồ thị tri thức, báo cáo](#14-bảng-số-liệu-đồ-thị-tri-thức-báo-cáo)

---

## 1. Thêm tài liệu mới

**Cách nhanh nhất:** web app → *Tài liệu* → **Tải tài liệu** → kéo thả nhiều file → khai báo metadata → Tải lên
(cần vai trò editor trở lên trong workspace). Hoặc dòng lệnh (vào workspace slug `default`, thêm `WORKSPACE=<slug|id>` để đổi):

```bash
make ingest FILE=/duong/dan/bang_gia_thang_9.xlsx TITLE="Bảng giá tháng 9" EFFECTIVE_DATE=2026-09-01 DOC_TYPE=price_list
```

Hoặc API: `POST /w/{workspace_id}/ingest` (multipart `files`, form `doc_type`, `scope`, `effective_date`, `title`, `version`, `replace_mode`, `series_key`).

Metadata nên khai báo:

| Trường | Khi nào khai báo | Nếu bỏ trống |
|---|---|---|
| `title` | Luôn nên đặt — là tên hiện trong citation ("bảng giá tháng 9, Sheet HCM01 dòng 42") | Lấy từ tiêu đề docx hoặc tên file |
| `effective_date` | Bảng giá, khuyến mãi, chính sách có ngày hiệu lực | Dò "Ban hành/hiệu lực từ ngày…" trong nội dung, rồi tên file (`thang_9_2026`) |
| `doc_type` | Khi tên file không nói rõ loại | Rule theo tên file/cột (danh sách loại theo domain pack); không ra thì Claude Haiku phân loại |
| `scope` (pack retail: mã cửa hàng) | Tài liệu chỉ áp dụng 1 cửa hàng | Toàn workspace (sheet tên `GM-XXX01` trong file tồn kho tự gắn store) |
| `replace_mode` | `document` (mặc định): file mới thay cả file cũ cùng series · `record`: file cập nhật một phần (chỉ thay dòng cùng SKU) · `none`: không thay gì | `document` |
| `series_key` | Khi 2 kỳ của cùng tài liệu đặt tên file khác hẳn nhau | Tự suy từ tên file, bỏ ngày/tháng/phiên bản (`bang_gia_thang_8` = `Bảng giá T9` = `bang_gia`) |

**Điều gì xảy ra:** mỗi file 1 job nền (`queued → processing → done | blocked | error`), chỉ xử lý đúng file
đó. Upload lại đúng file đã có → trả job cũ, không xử lý lại. Bảng giá tháng mới **không xoá** bảng cũ:
bản cũ chuyển `superseded`, `is_active=false`, `valid_to` = ngày hiệu lực bản mới; câu hỏi thường chỉ thấy
bản mới, câu hỏi "giá ngày 15/8" vẫn trả lời từ lịch sử. Nạp bù bản cũ hơn bản đang hiệu lực → vào thẳng lịch sử.
Cùng nội dung xuất hiện ở 2 file → giữ bản hiệu lực mới hơn, bản kia `duplicate`. Xong ingest, job `knowledge_extract`
trích **bảng số liệu** (→ parquet/DuckDB + mô tả AI), viết **tóm tắt + hồ sơ ngữ nghĩa** và **thực thể/quan hệ** của
file; pack retail còn chạy `graph_sync` cập nhật graph quy tắc phần của file đó.

**Kiểm tra:** trang *Tài liệu* (trạng thái + tiến độ job), chi tiết tài liệu (tab Xử lý, Bảng số liệu, Thực thể);
*Giám sát & duyệt → Việc nền*; `GET /w/{ws}/kb/health`; hỏi thử ở *Hỏi đáp & khai phá*.

## 2. Xử lý file blocked

*Tài liệu* lọc trạng thái **Bị chặn** (hoặc banner trên *Tổng quan*), mở tài liệu → tab *Xử lý*; hoặc *Giám sát & duyệt → Việc nền*
lọc `blocked`/`error`. Lý do nằm ở cột lỗi.

| Lý do | Cách xử lý |
|---|---|
| `Định dạng file không hỗ trợ` | Chuyển sang định dạng hỗ trợ (docx/xlsx/pdf/png/jpg/mp3/wav/mp4…), upload lại |
| `File không parse được đáng tin cậy` (lỗi invariant parser) | File hỏng/mã hoá/định dạng lạ: mở bằng Office, "Save as" lại rồi upload; PDF có mật khẩu → bỏ mật khẩu |
| `Không trích được nội dung nào` | File rỗng, ảnh không có chữ, PDF scan khi vision đang tắt → kiểm tra `ANTHROPIC_API_KEY`, `ingest.image_extraction` |
| `… có dấu hiệu prompt injection — file bị chặn chờ admin duyệt` | Mở tài liệu → tab *Bản ghi* (bản ghi nghi ngờ có nhãn), đọc nội dung. Nội dung nghiệp vụ bình thường (false positive) → nút **Duyệt** (`POST /w/{ws}/documents/{id}/approve`). Có câu cố ý chèn lệnh → **Xoá** (mềm) cả file, yêu cầu gửi lại bản sạch |
| `Agent ingest dừng ở trạng thái BLOCKED` (`ingest.mode=agent`) | Xem `result.issues` của job (verifier chỉ ra lỗi), sửa file hoặc chuyển `ingest.mode=deterministic` cho loại file đó |
| Job `error` (lỗi hệ thống, đã tự retry 3 lần) | Xem log: `make logs SERVICE=worker`; khắc phục (DB, API key, parser sidecar…) rồi **Chạy lại** ở *Giám sát & duyệt → Việc nền* (`POST /w/{ws}/ingest/{job_id}/requeue`) hoặc **Xử lý lại** trên trang tài liệu |

## 3. Sửa record bị extract sai

API `GET /w/{ws}/records` (lọc doc_type/scope/SKU/nội dung) → `PATCH /w/{ws}/records/{record_id}` sửa nội dung/SKU/scope/ngày hiệu lực
(UI: tài liệu → tab *Bản ghi* để đọc; sửa hàng loạt nên sửa file gốc rồi tải *Phiên bản mới*). Hệ thống tự: sinh lại chunk, embed lại, cập nhật graph của file đó; đánh dấu `manually_edited`
(ingest lại file **không ghi đè** bản đã sửa tay); lưu lịch sử sửa trong metadata + audit log.

Record rác → **Xoá mềm** (`DELETE /w/{ws}/records/{id}`, khôi phục bằng `POST /w/{ws}/records/{id}/restore`). Sai hàng loạt cả file → sửa file gốc rồi upload lại (thay thế).

## 4. Xử lý conflict

*Giám sát & duyệt → Xung đột* (hoặc `GET /w/{ws}/conflicts`). Mỗi mâu thuẫn liệt kê các giá trị, nguồn, ngày hiệu lực,
nguồn nào mới hơn. Trong lúc chưa xử lý, chatbot **trình bày đủ các phương án** + ngày hiệu lực, không tự chọn.

| Tình huống | Hành động |
|---|---|
| Một nguồn sai/lỗi thời (poster cũ chưa gỡ, bảng giá nhập nhầm) | **Giữ nguồn đã chọn** → nguồn kia bị xoá mềm |
| Cả hai đúng (giá khác nhau vì khác cửa hàng/khác chương trình) | **Không phải mâu thuẫn** (dismiss) — cân nhắc khai báo `store_code` cho tài liệu riêng cửa hàng |
| Đã sửa dữ liệu nguồn bằng cách khác (upload bảng giá mới) | **Đã xử lý cách khác** |

## 5. Đổi embedding model hoặc chunking — reindex

Embedding của nhiều model **cùng tồn tại** (`chunk_embeddings` theo model, HNSW riêng từng model) nên đổi
model không làm gián đoạn, rollback không cần reindex lại.

**Đổi embedding model** (vd sang `BAAI/bge-m3`, 1024 chiều):

```bash
make reindex MODEL=BAAI/bge-m3 DIM=1024
```

1. Lệnh trên embed toàn bộ KB cho model mới trong nền (model cũ vẫn phục vụ). Theo dõi progress ở *Giám sát & duyệt → Việc nền* (hoặc *Quản trị hệ thống → Hàng đợi job* cho mọi workspace).
2. Kiểm tra độ phủ: chunk thiếu embedding của model mới = 0 (`SELECT count(*) FROM kb_chunks c LEFT JOIN chunk_embeddings e ON e.chunk_id=c.chunk_id AND e.model='BAAI/bge-m3' WHERE c.deleted=false AND c.role<>'parent' AND e.chunk_id IS NULL`).
3. Sửa `config.yaml`: `embedding.model`, `embedding.dim` (+ `query_prefix/passage_prefix` theo model), bump `config_version`, ghi `config/CHANGELOG.md`, `make config-snapshot`, `make restart`.
4. Rollback: `make rollback-config VERSION=<bản trước>` — embedding cũ còn nguyên.
5. Dọn embedding model cũ khi chắc chắn không rollback: `DELETE FROM chunk_embeddings WHERE model='<model cũ>'`.

**Đổi chunking** (kích thước parent/child, chiến lược): sửa `chunking.*` + bump version + snapshot + restart, rồi
`make reindex` (mặc định `RECHUNK=auto` chỉ chunk lại record có fingerprint chunking cũ). Phạm vi hẹp:
`make reindex DOC_TYPE=policy` hoặc `DOCUMENT=<document_id>`. Reindex idempotent — chạy lại chỉ làm phần còn thiếu.

## 6. Đổi và rollback config

Quy trình đổi (bắt buộc để tra lại được request cũ chạy config nào):

1. Sửa `config.yaml` + **bump `config_version`** (`YYYY.MM.DD-N`).
2. Đổi prompt → tạo file **mới** `prompts/<tên>.v2.md` (không sửa đè v1), trỏ path mới, cập nhật sha256: `python -m core.config hash-prompts --write`.
3. Ghi mục mới ở đầu `config/CHANGELOG.md`.
4. `make config-check` → `make config-snapshot` → `make restart`.
5. Kiểm tra: `GET /config` (version, hash, prompt sha256) hoặc *Quản trị hệ thống → Config*.

Rollback:

```bash
make rollback-config VERSION=2026.10.03-1
```

Lệnh ghi `RETAILKB_CONFIG_VERSION` vào `.env` và restart api/worker; hệ thống nạp `config/versions/<VERSION>.yaml`.
Quay lại config hiện hành: xoá giá trị `RETAILKB_CONFIG_VERSION` trong `.env` rồi `make restart`.
Prompt bị sửa mà sha256 không khớp → service **không khởi động** (cố ý: không cho prompt đổi im lặng).

## 7. User báo trả lời sai — đọc trace

1. **Lấy trace_id:** user bấm 👎 dưới câu trả lời → *Giám sát & duyệt → Phản hồi* có sẵn câu hỏi, câu trả lời,
   link Langfuse. Hoặc *Lịch sử hỏi đáp* tìm theo câu hỏi/trạng thái (`GET /w/{ws}/ops/traces?q=...&status=...`).
2. **Decision trace** (`GET /w/{ws}/ops/traces/{trace_id}` → `decision_trace`, hoặc mục "Hệ thống đã làm gì để trả lời" dưới câu trả lời):
   - `understood`: loại câu hỏi (chế độ trợ lý: `tools_used` = công cụ đã gọi, `turns`; chế độ tra cứu: lookup/multi_hop/aggregation), thực thể, phạm vi, thời điểm.
     *Sai thực thể* → gộp/sửa thực thể ở *Đồ thị tri thức*; pack retail: thêm alias (`POST /w/{ws}/graph/aliases`).
   - `searched` / `found`: đã tra đâu, lọc gì, nguồn nào + điểm, nguồn nào được dùng.
     *Không có nguồn đúng trong `found`* → KB thiếu/sai dữ liệu (thêm tài liệu, sửa record) hoặc filter sai (store/hiệu lực).
     *Có nguồn đúng nhưng điểm thấp/không được dùng* → xem rerank score, cân nhắc chunking/embedding.
   - `graph_paths`: đường đi graph làm căn cứ (câu nối nhiều thực thể).
   - `conflicts`, `degraded` (reranker/graph lỗi → chạy dự phòng), `confidence`.
3. **Langfuse** (link trong trace): span theo tool (`search_documents`, `query_tables` kèm SQL, `explore_graph`…), `model.*`
   (prompt thực tế, token, chi phí), `answer.postcheck` (citation hợp lệ/bị loại), `answer.final`.
4. **Đối chiếu config:** `config_version` của trace vs `GET /config` hiện tại — lỗi chỉ xuất hiện sau khi đổi config → rollback.
5. Đánh dấu feedback `fixed`/`wontfix` kèm ghi chú.

Trace QA giữ **30 ngày** (`observability.trace_retention_days`); audit log giữ 365 ngày.

## 8. Review queue (câu hỏi tiền)

Theo chính sách của domain pack (retail: giá, chiết khấu, hoàn tiền, bồi thường…), câu hỏi của **viewer** không trả thẳng:
bản nháp (đã có citation) vào *Giám sát & duyệt → Chờ duyệt*. Editor/owner đọc nháp + nguồn + decision trace → **Duyệt & gửi**
(có thể sửa nội dung — tính là override, có nhãn) hoặc **Từ chối**. Người hỏi thấy câu trả lời khi mở lại cuộc trò chuyện
(`GET /w/{ws}/answers/{trace_id}` để poll). Tắt cho pack: `review.enabled=false` trong `domains/packs.py`.

## 9. Alert và cách phản ứng

Gửi qua webhook (`ALERT_CHANNEL` + `ALERT_WEBHOOK_URL`/`TELEGRAM_*`), không gửi lặp trong `alerts.cooldown_minutes`.

| Alert | Kiểm tra / xử lý |
|---|---|
| 💸 Chi phí LLM ngày vượt ngưỡng | *Quản trị hệ thống → Chi phí* (theo vai trò/model), *Giám sát & duyệt → Chi phí* theo workspace; job ingest lớn (vision nhiều ảnh, knowledge_extract nhiều file) hay QA tăng đột biến; hạ model vai trò phụ (`models.*` trong config) hoặc tạm tắt `knowledge.extract_entities`/`graph.llm_extraction` |
| 🔥 Tỉ lệ lỗi QA 5 phút | `curl localhost:8000/readyz`, `make logs SERVICE=api`; Anthropic quá tải/hết quota, DB đầy kết nối |
| 📉 Abstention tăng đột biến | *Giám sát & duyệt → Không trả lời được*: câu hỏi chung chủ đề → KB thiếu tài liệu; mọi câu đều abstain → index hỏng/embedding thiếu (*Tổng quan* báo "đoạn chưa được index") → `make reindex` |
| 📄 File ingest lỗi/bị chặn | Mục 2 |
| 🧾 Review queue tồn | Nhắc editor/owner; xem lại `review.keywords` của domain pack nếu quá nhiều câu bị bắt nhầm |

## 10. Backup / restore

```bash
make backup
```

Tạo `backups/retailkb-<TS>.dump`, `langfuse-<TS>.dump`, `uploads-<TS>.tar.gz`, `tables-<TS>.tar.gz` (parquet bảng số liệu) (+ checksum), giữ `BACKUP_KEEP_DAYS` ngày.
Cron hằng đêm (máy chủ chạy compose):

```bash
15 2 * * * cd /opt/retailkb && ./scripts/backup.sh >> backups/backup.log 2>&1
```

Restore (dừng api/worker, khôi phục DB + file upload, khởi động lại):

```bash
make restore TS=20261003T021500Z
```

Nên copy thư mục `backups/` sang nơi lưu trữ khác máy chủ.

## 11. Graph

Có hai lớp graph:

- **Đồ thị tri thức (mọi pack):** thực thể/quan hệ schema mở do LLM trích trong `knowledge_extract`, lưu Postgres
  (`entities`, `relations`, `entity_mentions`). Xem/sửa/gộp/xoá ở *Đồ thị tri thức*; chạy lại cho 1 file: **Xử lý lại** trên trang tài liệu.
- **Graph quy tắc (pack retail, Apache AGE, graph `ws_<workspace_id>`):** cập nhật incremental tự động sau mỗi ingest/xoá/khôi phục/sửa record
  (job `graph_sync`). Rebuild toàn bộ: `make graph-rebuild` (`NO_LLM=1` bỏ LLM extraction) hoặc `POST /w/{ws}/graph/rebuild`.
- Graph lỗi/AGE không dùng được → chatbot tự chuyển sang tìm kiếm văn bản (ghi rõ trong decision trace).
  Tắt hẳn graph: `GRAPH_ENABLED=false` (hoặc `router.graph_enabled=false` trong config) + restart.
- Alias (tên viết tắt cửa hàng/sản phẩm/ngành hàng, pack retail): `GET/POST /w/{ws}/graph/aliases`.

## 12. Việc định kỳ & khác

- **Retention** chạy tự động trong worker mỗi `worker.retention_interval_hours`: trace QA + Langfuse 30 ngày,
  abstention 180 ngày, session chat nhàn rỗi 30 ngày, audit log 365 ngày (xoá nguyên file tháng quá hạn).
- **API key tích hợp:** *Cài đặt → API key* (owner) hoặc `POST /w/{ws}/api-keys` — key gắn 1 workspace + vai trò, gọi API với header
  `X-API-Key`; thu hồi `DELETE /w/{ws}/api-keys/{id}`. `BOOTSTRAP_ADMIN_API_KEY` là key hệ thống (superadmin) cho script.
- **Scale:** nhiều worker song song an toàn: `docker compose up -d --scale worker=3`.
- **Production:** `APP_ENV=prod`, đổi mật khẩu superadmin sau lần đăng nhập đầu, đổi `BOOTSTRAP_ADMIN_API_KEY`,
  đặt reverse proxy TLS trước web/api; không publish cổng Postgres ra ngoài.
  Với `APP_ENV=prod`, chỉ email trùng `BOOTSTRAP_ADMIN_EMAIL` mới thành superadmin khi đăng ký — người đăng ký
  đầu tiên **không** tự có quyền (SaaS public). Ở dev chưa đặt `BOOTSTRAP_ADMIN_EMAIL` thì tài khoản đầu tiên là superadmin.

## 13. Workspace, tài khoản, phân quyền

- **Tài khoản:** đăng ký bằng email/mật khẩu (bcrypt), JWT `JWT_SECRET`. User đầu tiên của hệ thống (hoặc `BOOTSTRAP_ADMIN_*`)
  là **superadmin**: thấy mọi workspace, *Quản trị hệ thống* (khoá user, cấp superadmin, hàng đợi job, chi phí, config).
- **Workspace:** đơn vị cô lập dữ liệu (tài liệu, bảng, đồ thị, báo cáo, session, trace, job đều có `workspace_id`).
  Tạo ở màn hình đăng ký/onboarding hoặc nút *Tạo workspace mới* trong menu chọn workspace; chọn **domain pack**
  (`generic` mặc định, `retail` cho bán lẻ: loại tài liệu, câu hỏi gợi ý, duyệt câu hỏi tiền, graph quy tắc, thuộc tính
  thành viên `store_code` giới hạn phạm vi dữ liệu). Lưu trữ workspace: *Cài đặt → Chung* (owner).
- **Vai trò:** `viewer` (hỏi đáp, xem) < `editor` (tải/sửa tài liệu, duyệt câu trả lời, xử lý xung đột, tạo báo cáo, truy vấn đã lưu)
  < `owner` (thành viên, API key, cài đặt, chi phí). Mời bằng email ở *Cài đặt → Thành viên*: chưa có tài khoản → lời mời
  chờ, tự vào workspace khi đăng ký đúng email.
- **Rate limit:** theo user/API key (`security.rate_limit_per_minute`, API key có giới hạn riêng).

## 14. Bảng số liệu, đồ thị tri thức, báo cáo

- **Bảng số liệu:** mỗi sheet Excel/CSV/bảng PDF đủ `analytics.min_rows` dòng → 1 bảng DuckDB (parquet ở `TABLES_DIR/<workspace_id>/`),
  header tự dò, kiểu cột ép theo quy tắc ≥90% (integer/double/percent/date/timestamp/boolean/text), mô tả + câu hỏi gợi ý do
  Haiku viết (`analytics.describe_with_llm`). Tài liệu bị thay thế/xoá → bảng `is_active=false`, không truy vấn được.
  SQL (người dùng hoặc model sinh) qua `analytics/engine.validate_sql`: một `SELECT`/`WITH`, denylist, DuckDB
  `enable_external_access=false`, `allowed_directories` chỉ thư mục bảng, timeout `analytics.query_timeout_seconds`,
  tối đa `analytics.max_result_rows` dòng. Bảng không dùng được → xoá/ẩn qua tài liệu nguồn hoặc **Xử lý lại**.
- **Đồ thị tri thức:** `knowledge.*` trong config (bật/tắt trích thực thể, cửa sổ, số luồng). Thực thể trùng tên khác cách viết
  → **Gộp** ở panel thực thể (tên cũ thành alias, mention/quan hệ chuyển sang). Xoá thực thể không ảnh hưởng tài liệu.
- **Báo cáo:** job `report_generate` (worker) → `reports.blocks`; mỗi mục là một lượt hỏi trợ lý (có trace riêng, xem ở *Lịch sử hỏi đáp*).
  Báo cáo lỗi (`status=error`) ghi lý do ở block đầu; **Sinh lại** ghi đè toàn bộ block. Xuất PDF cần WeasyPrint + Pango
  (đã có trong image `docker/app/Dockerfile`); nếu máy chủ thiếu → API trả lỗi 422 gợi ý xuất DOCX/HTML.

## 15. Mô hình dữ liệu (Excel → warehouse → view cho BI)

- **Luồng:** *Mô hình dữ liệu → Mô hình mới* (chọn/tải workbook) → job `model_analyze` (worker): mỗi sheet → bảng trong schema
  `dm_<id 10 ký tự>` (cột `_row` = số dòng Excel, COMMENT = nhãn gốc), dò sheet chính, quan hệ (khoá + chuẩn hoá khoá như
  "chỉ phần số" STE0870→870), logic từng cột theo thứ tự bằng chứng: công thức Excel → pivot table → mẫu dữ liệu → AI
  (`modeling.ai_suggestions`, model `data_model`). Người dùng duyệt ở 5 bước; mọi chỉnh sửa được kiểm chứng ngay trên DuckDB
  (parquet ở `TABLES_DIR/<ws>/models/<id>/`). **Dựng view** → job `model_build`: tạo view `dm_<id>.vw_<sheet chính>`
  (tính lại từ sheet phụ, không chép số cuối), đọc view so từng dòng với Excel (`data_models.validation`), xuất view sang
  bảng số liệu của workspace để hỏi đáp/khám phá.
- **Superset/BI:** tạo role chỉ-đọc rồi khai báo `modeling.reader_role` — mỗi lần build hệ thống tự `GRANT USAGE/SELECT` trên
  schema của mô hình:
  ```sql
  CREATE ROLE bi_reader LOGIN PASSWORD '…';
  ```
  Trong Superset: Database = Postgres (host/DB của `WAREHOUSE_URL` hoặc `DATABASE_URL`), user `bi_reader`; Dataset = schema
  `dm_…`, bảng `vw_…`. Đặt `WAREHOUSE_PUBLIC_DSN` để màn *Chia sẻ* hiện đúng chuỗi kết nối cho người dùng.
- **Ngày báo cáo:** cột kiểu "số ngày kể từ…" dùng `AS_OF()`; hệ thống tự suy ra từ chính số liệu trong file khi có thể,
  người dùng đổi được (ngày cố định để tái tạo đúng báo cáo, hoặc "hôm nay" để luôn cập nhật).
- **Phân tích lại** (đổi sheet chính, file mới cùng cấu trúc) giữ các quyết định người dùng đã xác nhận nếu cột/quan hệ còn tồn tại.
- **Xoá mô hình** xoá schema `dm_…` (dashboard BI dùng view sẽ lỗi) và tắt bảng số liệu đã xuất; file nguồn giữ nguyên.
- **Giới hạn:** công thức Excel kết hợp nhiều phép tra cứu/gộp trong 1 ô (vd `=SUMIFS(..)-SUMIFS(..)`) chưa dịch tự động —
  giữ làm bằng chứng, người dùng mô tả lại bằng lời hoặc form; khoá nối đa cột chỉ đến từ công thức hoặc người dùng.
