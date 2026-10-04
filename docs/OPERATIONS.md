# Vận hành RetailKB Copilot

Tài liệu cho người vận hành (admin/supervisor). Lệnh `make` chạy từ thư mục repo trên máy chủ chạy
docker-compose. Mọi thao tác trên Admin UI (http://localhost:8501) đều có API tương ứng (http://localhost:8000/docs)
và được ghi audit log append-only (`data/security/audit/audit_trail-YYYY-MM.log` trong volume `audit`).

Mục lục: [1. Thêm tài liệu](#1-thêm-tài-liệu-mới) · [2. File blocked](#2-xử-lý-file-blocked) ·
[3. Sửa record sai](#3-sửa-record-bị-extract-sai) · [4. Conflict](#4-xử-lý-conflict) ·
[5. Đổi embedding/chunking](#5-đổi-embedding-model-hoặc-chunking--reindex) · [6. Rollback config](#6-đổi-và-rollback-config) ·
[7. Đọc trace](#7-user-báo-trả-lời-sai--đọc-trace) · [8. Review queue](#8-review-queue-câu-hỏi-tiền) ·
[9. Alert](#9-alert-và-cách-phản-ứng) · [10. Backup/restore](#10-backup--restore) · [11. Graph](#11-graph) · [12. Khác](#12-việc-định-kỳ--khác)

---

## 1. Thêm tài liệu mới

**Cách nhanh nhất:** Admin UI → *Upload & job* → chọn file (nhiều file được) → khai báo metadata → Upload.
Hoặc dòng lệnh:

```bash
make ingest FILE=/duong/dan/bang_gia_thang_9.xlsx TITLE="Bảng giá tháng 9" EFFECTIVE_DATE=2026-09-01 DOC_TYPE=price_list
```

Hoặc API: `POST /ingest` (multipart `files`, form `doc_type`, `store_code`, `effective_date`, `title`, `replace_mode`, `series_key`).

Metadata nên khai báo:

| Trường | Khi nào khai báo | Nếu bỏ trống |
|---|---|---|
| `title` | Luôn nên đặt — là tên hiện trong citation ("bảng giá tháng 9, Sheet HCM01 dòng 42") | Lấy từ tiêu đề docx hoặc tên file |
| `effective_date` | Bảng giá, khuyến mãi, chính sách có ngày hiệu lực | Dò "Ban hành/hiệu lực từ ngày…" trong nội dung, rồi tên file (`thang_9_2026`) |
| `doc_type` | Khi tên file không nói rõ loại | Rule theo tên file/cột; không ra thì Claude Haiku phân loại |
| `store_code` | Tài liệu chỉ áp dụng 1 cửa hàng | Toàn hệ thống (sheet tên `GM-XXX01` trong file tồn kho tự gắn store) |
| `replace_mode` | `document` (mặc định): file mới thay cả file cũ cùng series · `record`: file cập nhật một phần (chỉ thay dòng cùng SKU) · `none`: không thay gì | `document` |
| `series_key` | Khi 2 kỳ của cùng tài liệu đặt tên file khác hẳn nhau | Tự suy từ tên file, bỏ ngày/tháng/phiên bản (`bang_gia_thang_8` = `Bảng giá T9` = `bang_gia`) |

**Điều gì xảy ra:** mỗi file 1 job nền (`queued → processing → done | blocked | error`), chỉ xử lý đúng file
đó. Upload lại đúng file đã có → trả job cũ, không xử lý lại. Bảng giá tháng mới **không xoá** bảng cũ:
bản cũ chuyển `superseded`, `is_active=false`, `valid_to` = ngày hiệu lực bản mới; câu hỏi thường chỉ thấy
bản mới, câu hỏi "giá ngày 15/8" vẫn trả lời từ lịch sử. Nạp bù bản cũ hơn bản đang hiệu lực → vào thẳng lịch sử.
Cùng nội dung xuất hiện ở 2 file → giữ bản hiệu lực mới hơn, bản kia `duplicate`. Xong ingest, graph chỉ
cập nhật phần của file đó (job `graph_sync`).

**Kiểm tra:** cột trạng thái job; *Duyệt KB* lọc theo `source_file`; `make kb-health`; hỏi thử ở chat UI.

## 2. Xử lý file blocked

*Upload & job* → mục "Xử lý job blocked / error" hoặc *KB health* → "File blocked chờ xử lý". Lý do nằm ở cột `error`.

| Lý do | Cách xử lý |
|---|---|
| `Định dạng file không hỗ trợ` | Chuyển sang định dạng hỗ trợ (docx/xlsx/pdf/png/jpg/mp3/wav/mp4…), upload lại |
| `File không parse được đáng tin cậy` (lỗi invariant parser) | File hỏng/mã hoá/định dạng lạ: mở bằng Office, "Save as" lại rồi upload; PDF có mật khẩu → bỏ mật khẩu |
| `Không trích được nội dung nào` | File rỗng, ảnh không có chữ, PDF scan khi vision đang tắt → kiểm tra `ANTHROPIC_API_KEY`, `ingest.image_extraction` |
| `… có dấu hiệu prompt injection — file bị chặn chờ admin duyệt` | Mở *Duyệt KB* lọc `status=quarantined` + `source_file`, đọc cột `suspicious_content`. Nội dung nghiệp vụ bình thường (false positive) → nút **Duyệt file bị chặn** (`POST /kb/documents/{id}/approve`). Có câu cố ý chèn lệnh → **Xoá mềm cả file**, yêu cầu khách gửi lại bản sạch |
| `Agent ingest dừng ở trạng thái BLOCKED` (`ingest.mode=agent`) | Xem `result.issues` của job (verifier chỉ ra lỗi), sửa file hoặc chuyển `ingest.mode=deterministic` cho loại file đó |
| Job `error` (lỗi hệ thống, đã tự retry 3 lần) | Xem log: `make logs SERVICE=worker`; khắc phục (DB, API key, parser sidecar…) rồi **Chạy lại job (requeue)** |

## 3. Sửa record bị extract sai

Admin UI → *Duyệt KB* → lọc (doc_type/store/SKU/nội dung) → chọn record → sửa nội dung/SKU/store/ngày hiệu lực
→ **Lưu**. Hệ thống tự: sinh lại chunk, embed lại, cập nhật graph của file đó; đánh dấu `manually_edited`
(ingest lại file **không ghi đè** bản đã sửa tay); lưu lịch sử sửa trong metadata + audit log.

API: `PATCH /kb/records/{record_id}` (record_id URL-encode). Record rác → **Xoá mềm** (`DELETE /kb/records/{id}`,
khôi phục bằng `POST /kb/records/{id}/restore`). Sai hàng loạt cả file → sửa file gốc rồi upload lại (thay thế).

## 4. Xử lý conflict

Admin UI → *Conflict* (hoặc `GET /conflicts`). Mỗi mâu thuẫn liệt kê các giá trị, nguồn, ngày hiệu lực,
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

1. Lệnh trên embed toàn bộ KB cho model mới trong nền (model cũ vẫn phục vụ). Theo dõi progress ở *Upload & job*.
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
5. Kiểm tra: `GET /config` (version, hash, prompt sha256) hoặc Admin UI → *Config & chi phí*.

Rollback:

```bash
make rollback-config VERSION=2026.10.03-1
```

Lệnh ghi `RETAILKB_CONFIG_VERSION` vào `.env` và restart api/worker/admin-ui; hệ thống nạp `config/versions/<VERSION>.yaml`.
Quay lại config hiện hành: xoá giá trị `RETAILKB_CONFIG_VERSION` trong `.env` rồi `make restart`.
Prompt bị sửa mà sha256 không khớp → service **không khởi động** (cố ý: không cho prompt đổi im lặng).

## 7. User báo trả lời sai — đọc trace

1. **Lấy trace_id:** user bấm 👎 trên chat UI (kèm comment) → Admin UI *Feedback 👎* có sẵn câu hỏi, câu trả lời,
   config_version, link Langfuse. Hoặc tìm theo user/cửa hàng/thời gian: `GET /admin/traces?user_id=nv01&store_code=GM-HN01`.
   Thông báo lỗi ở chat có in "Mã tra cứu" = trace_id.
2. **Decision trace** (`GET /admin/traces/{trace_id}` → `decision_trace`, hoặc panel "Vì sao có câu trả lời này?" ở chat):
   - `understood`: router hiểu loại câu hỏi gì (lookup/multi_hop/aggregation), nhận ra thực thể nào, phạm vi cửa hàng, thời điểm.
     *Sai thực thể* (vd viết tắt tên cửa hàng/sản phẩm) → thêm alias ở *Alias & graph*.
   - `searched` / `found`: đã tra đâu, lọc gì, nguồn nào + điểm, nguồn nào được dùng.
     *Không có nguồn đúng trong `found`* → KB thiếu/sai dữ liệu (thêm tài liệu, sửa record) hoặc filter sai (store/hiệu lực).
     *Có nguồn đúng nhưng điểm thấp/không được dùng* → xem rerank score, cân nhắc chunking/embedding.
   - `graph_paths`: đường đi graph làm căn cứ (câu nối nhiều thực thể).
   - `conflicts`, `degraded` (reranker/graph lỗi → chạy dự phòng), `confidence`.
3. **Langfuse** (link trong trace): từng span `router`, `retrieval.dense/bm25/fusion/rerank`, `graph.*`,
   `model.answer` (prompt thực tế, token, chi phí), `answer.postcheck` (citation hợp lệ/bị loại), `answer.final`.
4. **Đối chiếu config:** `config_version` của trace vs `GET /config` hiện tại — lỗi chỉ xuất hiện sau khi đổi config → rollback.
5. Đánh dấu feedback `fixed`/`wontfix` kèm ghi chú.

Trace QA giữ **30 ngày** (`observability.trace_retention_days`); audit log giữ 365 ngày.

## 8. Review queue (câu hỏi tiền)

Câu hỏi về giá, chiết khấu, hoàn tiền, bồi thường của nhân viên (role staff) không trả thẳng: bản nháp (đã
có citation) vào *Review queue*. Supervisor đọc nháp + nguồn + decision trace → **Duyệt & gửi** (có thể sửa
nội dung — tính là override, theo dõi ở tab "Đã sửa (override)") hoặc **Từ chối**. Chat UI của nhân viên tự
nhận câu trả lời khi được duyệt. Supervisor gắn cửa hàng chỉ thấy câu hỏi của cửa hàng mình. Tắt: `review.enabled=false`.

## 9. Alert và cách phản ứng

Gửi qua webhook (`ALERT_CHANNEL` + `ALERT_WEBHOOK_URL`/`TELEGRAM_*`), không gửi lặp trong `alerts.cooldown_minutes`.

| Alert | Kiểm tra / xử lý |
|---|---|
| 💸 Chi phí LLM ngày vượt ngưỡng | *Config & chi phí* (theo vai trò/model); job ingest lớn (vision nhiều ảnh) hay QA tăng đột biến; hạ model vai trò phụ hoặc tạm tắt `graph.llm_extraction` |
| 🔥 Tỉ lệ lỗi QA 5 phút | `curl localhost:8000/readyz`, `make logs SERVICE=api`; Anthropic quá tải/hết quota, DB đầy kết nối |
| 📉 Abstention tăng đột biến | *Abstention*: câu hỏi chung chủ đề → KB thiếu tài liệu; mọi câu đều abstain → index hỏng/embedding thiếu (*KB health* → chunk thiếu embedding) → `make reindex` |
| 📄 File ingest lỗi/bị chặn | Mục 2 |
| 🧾 Review queue tồn | Nhắc supervisor; xem lại `review.money_keywords` nếu quá nhiều câu bị bắt nhầm |

## 10. Backup / restore

```bash
make backup
```

Tạo `backups/retailkb-<TS>.dump`, `langfuse-<TS>.dump`, `uploads-<TS>.tar.gz` (+ checksum), giữ `BACKUP_KEEP_DAYS` ngày.
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

- Cập nhật incremental tự động sau mỗi ingest/xoá/khôi phục/sửa record (job `graph_sync`).
- Rebuild toàn bộ (đổi logic builder, sửa alias tên sản phẩm hàng loạt, graph hỏng): `make graph-rebuild`
  (`NO_LLM=1` để bỏ LLM extraction, nhanh + không tốn phí) hoặc Admin UI *Alias & graph*.
- Graph lỗi/AGE không dùng được → chatbot tự chuyển sang tìm kiếm văn bản (ghi rõ trong decision trace).
  Tắt hẳn graph: `GRAPH_ENABLED=false` (hoặc `router.graph_enabled=false` trong config) + restart.
- Alias (tên viết tắt cửa hàng/sản phẩm/ngành hàng): *Alias & graph* hoặc `POST /graph/aliases`.

## 12. Việc định kỳ & khác

- **Retention** chạy tự động trong worker mỗi `worker.retention_interval_hours`: trace QA + Langfuse 30 ngày,
  abstention 180 ngày, session chat nhàn rỗi 30 ngày, audit log 365 ngày (xoá nguyên file tháng quá hạn).
- **Tạo key/token:** `make api-key NAME=pos-gateway ROLE=staff STORE=GM-HN01 RATE=120`; JWT cho user
  `make token USER_ID=nv01 ROLE=staff STORE=GM-HN01` (hệ thống đăng nhập của khách gọi `POST /auth/token` bằng key admin).
  Thu hồi key: Admin UI/`DELETE /admin/api-keys/{id}`.
- **Scale:** nhiều worker song song an toàn: `docker compose up -d --scale worker=3`.
- **Production:** `APP_ENV=prod`, `DEV_LOGIN_ENABLED=false`, đổi `BOOTSTRAP_ADMIN_API_KEY` sau khi đã tạo key admin riêng,
  đặt reverse proxy TLS trước chat-ui/api/admin-ui; không publish cổng Postgres ra ngoài.
