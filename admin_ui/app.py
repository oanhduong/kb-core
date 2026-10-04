"""Admin UI (D3) — Streamlit, ưu tiên tốc độ. Chạy: streamlit run admin_ui/app.py

Trang: Upload & job · Duyệt KB · Review queue · Conflict · KB health · Abstention · Feedback 👎 ·
Alias & graph · Config & chi phí. Mọi thao tác gọi API (admin_ui/client.py) bằng ADMIN_API_KEY.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # chạy được bằng `streamlit run admin_ui/app.py`

import pandas as pd
import streamlit as st

from admin_ui import client as api

st.set_page_config(page_title="RetailKB Admin", page_icon="🗂️", layout="wide")

DOC_TYPES = ["", "policy", "price_list", "promotion", "catalog", "training", "contract", "inventory", "supplier", "unknown"]
STATUS_ICON = {"queued": "⏳", "processing": "⚙️", "done": "✅", "blocked": "⛔", "error": "❌"}


def show_error(e: Exception) -> None:
    st.error(f"Lỗi: {e}")


def df(rows: list[dict], cols: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if cols and not frame.empty:
        frame = frame[[c for c in cols if c in frame.columns]]
    return frame


# =====================================================================================
def page_upload() -> None:
    st.header("📤 Upload tài liệu & theo dõi job")
    with st.form("upload", clear_on_submit=True):
        files = st.file_uploader("Chọn 1 hoặc nhiều file", accept_multiple_files=True,
                                 type=["docx", "xlsx", "pdf", "png", "jpg", "jpeg", "webp", "mp3", "wav", "m4a", "mp4", "mov"])
        c1, c2, c3 = st.columns(3)
        doc_type = c1.selectbox("doc_type (bỏ trống = tự nhận diện)", DOC_TYPES)
        store_code = c2.text_input("store_code (bỏ trống = toàn hệ thống)")
        replace_mode = c3.selectbox("Chế độ thay thế", ["document", "record", "none"],
                                    help="document: thay cả file cùng loại; record: chỉ thay dòng cùng SKU; none: không thay")
        c4, c5, c6 = st.columns(3)
        use_date = c4.checkbox("Khai báo ngày hiệu lực")
        eff = c4.date_input("Ngày hiệu lực", value=date.today(), disabled=False)
        title = c5.text_input("Tên hiển thị (1 file) — dùng trong citation, vd 'Bảng giá tháng 8'")
        series_key = c6.text_input("series_key (tuỳ chọn) — file cùng series sẽ thay thế nhau")
        if st.form_submit_button("Upload", type="primary") and files:
            data = {"doc_type": doc_type or None, "store_code": store_code or None, "replace_mode": replace_mode,
                    "title": title or None, "series_key": series_key or None,
                    "effective_date": eff.isoformat() if use_date else None}
            try:
                res = api.post("/ingest", files=[("files", (f.name, f.getvalue())) for f in files],
                               data={k: v for k, v in data.items() if v})
                st.success(f"Đã nhận {len(res['jobs'])} file — batch {res['batch_id']}")
                st.dataframe(df(res["jobs"]), use_container_width=True)
            except Exception as e:
                show_error(e)

    st.subheader("Job gần đây")
    c1, c2, c3 = st.columns([1, 1, 1])
    status = c1.selectbox("Trạng thái", ["", "queued", "processing", "done", "blocked", "error"])
    jtype = c2.selectbox("Loại job", ["", "ingest_file", "graph_sync", "graph_rebuild", "reindex", "document_lifecycle"])
    c3.button("🔄 Làm mới")
    try:
        jobs = api.get("/ingest", status=status or None, type=jtype or None, limit=100)
    except Exception as e:
        return show_error(e)
    rows = [{"": STATUS_ICON.get(j["status"], ""), "job_id": j["job_id"], "type": j["type"], "status": j["status"],
             "document_id": j["document_id"], "progress": f"{j['progress']['done']}/{j['progress']['total']}" if j["progress"]["total"] else "",
             "attempts": j["attempts"], "error": (j["error"] or "")[:200], "created_at": j["created_at"]} for j in jobs]
    st.dataframe(df(rows), use_container_width=True, hide_index=True)
    bad = [j for j in jobs if j["status"] in ("blocked", "error")]
    if bad:
        st.subheader("Xử lý job blocked / error")
        pick = st.selectbox("Job", [j["job_id"] for j in bad],
                            format_func=lambda i: next(f"{j['type']} {j['document_id']} — {(j['error'] or '')[:80]}" for j in bad if j["job_id"] == i))
        job = next(j for j in bad if j["job_id"] == pick)
        st.json(job["result"], expanded=False)
        c1, c2 = st.columns(2)
        if c1.button("Chạy lại job (requeue)"):
            try:
                api.post(f"/ingest/{pick}/requeue")
                st.success("Đã requeue")
            except Exception as e:
                show_error(e)
        if job.get("document_id") and c2.button("Duyệt file (nếu bị chặn vì nghi injection — đã kiểm tra nội dung)"):
            try:
                st.json(api.post(f"/kb/documents/{job['document_id']}/approve"))
            except Exception as e:
                show_error(e)


# =====================================================================================
def page_records() -> None:
    st.header("🔎 Duyệt KB record")
    tab_rec, tab_doc = st.tabs(["Record", "File (document)"])
    with tab_rec:
        c = st.columns(6)
        doc_type = c[0].selectbox("doc_type", DOC_TYPES, key="r_dt")
        store = c[1].text_input("store_code", key="r_store")
        source_file = c[2].text_input("source_file chứa", key="r_sf")
        status = c[3].selectbox("status", ["", "active", "superseded", "duplicate", "quarantined", "deleted"], key="r_st")
        sku = c[4].text_input("SKU", key="r_sku")
        q = c[5].text_input("Nội dung chứa", key="r_q")
        page = st.number_input("Trang", min_value=1, value=1, step=1)
        try:
            res = api.get("/kb/records", doc_type=doc_type or None, store_code=store or None, source_file=source_file or None,
                          status=status or None, sku=sku or None, q=q or None, page=page, page_size=50,
                          include_deleted=status == "deleted")
        except Exception as e:
            return show_error(e)
        st.caption(f"{res['total']} record")
        st.dataframe(df(res["items"], ["record_id", "citation", "doc_type", "status", "is_active", "store_code", "skus",
                                       "effective_date", "valid_to", "manually_edited", "content"]),
                     use_container_width=True, hide_index=True)
        ids = [r["record_id"] for r in res["items"]]
        if not ids:
            return
        rid = st.selectbox("Chọn record để xem/sửa", ids)
        rec = next(r for r in res["items"] if r["record_id"] == rid)
        with st.form("edit"):
            content = st.text_area("Nội dung", rec["content"], height=200)
            c1, c2, c3 = st.columns(3)
            store_e = c1.text_input("store_code", rec["store_code"] or "")
            skus_e = c2.text_input("SKU (phân cách dấu phẩy)", ", ".join(rec["skus"] or []))
            eff_e = c3.text_input("effective_date (YYYY-MM-DD)", rec["effective_date"] or "")
            if st.form_submit_button("💾 Lưu sửa (chunk/embedding/graph tự cập nhật)", type="primary"):
                body = {}
                if content != rec["content"]:
                    body["content"] = content
                if (store_e or None) != rec["store_code"]:
                    body["store_code"] = store_e or None
                new_skus = [s.strip() for s in skus_e.split(",") if s.strip()]
                if new_skus != (rec["skus"] or []):
                    body["skus"] = new_skus
                if (eff_e or None) != rec["effective_date"]:
                    body["effective_date"] = eff_e or None
                if not body:
                    st.info("Không có thay đổi")
                else:
                    try:
                        api.patch(f"/kb/records/{api.enc(rid)}", body)
                        st.success("Đã lưu")
                    except Exception as e:
                        show_error(e)
        c1, c2 = st.columns(2)
        if rec["status"] != "deleted" and c1.button("🗑️ Xoá mềm record"):
            try:
                api.delete(f"/kb/records/{api.enc(rid)}")
                st.success("Đã xoá mềm")
            except Exception as e:
                show_error(e)
        if rec["status"] == "deleted" and c2.button("♻️ Khôi phục record"):
            try:
                api.post(f"/kb/records/{api.enc(rid)}/restore")
                st.success("Đã khôi phục")
            except Exception as e:
                show_error(e)
        with st.expander("Metadata / lịch sử sửa"):
            st.json(rec["metadata"])
    with tab_doc:
        c1, c2, c3 = st.columns(3)
        dstatus = c1.selectbox("status", ["", "active", "superseded", "blocked", "error", "processing", "pending", "deleted"], key="d_st")
        dq = c2.text_input("Tên file chứa", key="d_q")
        try:
            docs = api.get("/kb/documents", status=dstatus or None, q=dq or None, include_deleted=dstatus == "deleted", page_size=200)["items"]
        except Exception as e:
            return show_error(e)
        st.dataframe(df(docs, ["document_id", "title", "filename", "doc_type", "status", "is_active", "store_code",
                               "effective_date", "valid_to", "record_count", "superseded_by_id", "created_at"]),
                     use_container_width=True, hide_index=True)
        if docs:
            did = st.selectbox("Chọn file", [d["document_id"] for d in docs],
                               format_func=lambda i: next(f"{d['title']} ({d['status']})" for d in docs if d["document_id"] == i))
            d = next(x for x in docs if x["document_id"] == did)
            c1, c2, c3 = st.columns(3)
            if d["status"] != "deleted" and c1.button("🗑️ Xoá mềm cả file"):
                try:
                    st.json(api.delete(f"/kb/documents/{did}"))
                except Exception as e:
                    show_error(e)
            if d["status"] == "deleted" and c2.button("♻️ Khôi phục file"):
                try:
                    st.json(api.post(f"/kb/documents/{did}/restore"))
                except Exception as e:
                    show_error(e)
            if d["status"] == "blocked" and c3.button("✅ Duyệt file bị chặn"):
                try:
                    st.json(api.post(f"/kb/documents/{did}/approve"))
                except Exception as e:
                    show_error(e)


# =====================================================================================
def page_review() -> None:
    st.header("🧑‍⚖️ Review queue (câu hỏi liên quan tiền)")
    tab_p, tab_h = st.tabs(["Chờ duyệt", "Đã sửa (override)"])
    with tab_p:
        try:
            items = api.get("/review", status="pending")
        except Exception as e:
            return show_error(e)
        st.caption(f"{len(items)} mục đang chờ")
        for it in items:
            with st.expander(f"❓ {it['question']}  —  {it['store_code'] or 'toàn hệ thống'} · {it['user_id']} · {it['created_at']}"):
                st.markdown(f"**Lý do:** {it['reason']}")
                for line in it["decision_trace"].get("summary", []):
                    st.caption(line)
                for cit in it["draft_citations"]:
                    st.markdown(f"[{cit['n']}] **{cit['label']}** — {cit['snippet'][:200]}")
                edited = st.text_area("Câu trả lời (sửa nếu cần)", it["draft_answer"], key=f"a_{it['id']}", height=150)
                note = st.text_input("Ghi chú", key=f"n_{it['id']}")
                c1, c2 = st.columns(2)
                if c1.button("✅ Duyệt & gửi", key=f"ok_{it['id']}", type="primary"):
                    try:
                        api.post(f"/review/{it['id']}/approve", {"edited_answer": edited if edited != it["draft_answer"] else None, "note": note or None})
                        st.success("Đã duyệt")
                    except Exception as e:
                        show_error(e)
                if c2.button("⛔ Từ chối", key=f"no_{it['id']}"):
                    try:
                        api.post(f"/review/{it['id']}/reject", {"note": note or None})
                        st.warning("Đã từ chối")
                    except Exception as e:
                        show_error(e)
    with tab_h:
        try:
            done = api.get("/review", status="approved", overridden=True)
        except Exception as e:
            return show_error(e)
        st.dataframe(df(done, ["reviewed_at", "reviewer_id", "question", "draft_answer", "final_answer", "review_note"]),
                     use_container_width=True, hide_index=True)


# =====================================================================================
def page_conflicts() -> None:
    st.header("⚠️ Mâu thuẫn giữa các nguồn")
    status = st.radio("Trạng thái", ["open", "resolved", "dismissed"], horizontal=True)
    try:
        items = api.get("/conflicts", status=status)
    except Exception as e:
        return show_error(e)
    st.caption(f"{len(items)} mâu thuẫn")
    for c in items:
        with st.expander(f"{c['kind']} — {c['entity_key']} ({c['created_at']})"):
            vals = c["details"].get("values", [])
            st.dataframe(df(vals, ["display", "source", "effective_date", "is_newest", "record_id"]), hide_index=True, use_container_width=True)
            if status != "open":
                st.info(f"{c['status']} bởi {c['resolved_by']}: {c['resolution']}")
                continue
            keep = st.selectbox("Giữ nguồn", [v["record_id"] for v in vals], key=f"k_{c['id']}",
                                format_func=lambda rid: next(f"{v['display']} — {v['source']}" for v in vals if v["record_id"] == rid))
            note = st.text_input("Ghi chú", key=f"cn_{c['id']}")
            b1, b2, b3 = st.columns(3)
            for col, action, label in ((b1, "keep_one", "Giữ nguồn đã chọn, xoá mềm nguồn kia"),
                                       (b2, "dismiss", "Không phải mâu thuẫn"), (b3, "resolved", "Đã xử lý cách khác")):
                if col.button(label, key=f"{action}_{c['id']}"):
                    try:
                        api.post(f"/conflicts/{c['id']}/resolve", {"action": action, "keep_record_id": keep if action == "keep_one" else None,
                                                                   "note": note or None})
                        st.success("Đã cập nhật")
                    except Exception as e:
                        show_error(e)


# =====================================================================================
def page_health() -> None:
    st.header("🩺 KB health")
    days = st.slider("Số ngày nhìn lại (abstention)", 1, 30, 7)
    try:
        h = api.get("/kb/health", days=days)
    except Exception as e:
        return show_error(e)
    a = h["abstentions_last_days"]
    m = st.columns(5)
    m[0].metric("File blocked", h["blocked_files"]["count"])
    m[1].metric("Conflict mở", h["open_conflicts"])
    m[2].metric(f"Abstention {days} ngày", a["count"], help=f"tỉ lệ {a['rate']}" if a["rate"] is not None else None)
    m[3].metric("Chunk thiếu embedding", h["embedding_coverage"]["missing"])
    m[4].metric("Config", h["config_version"])
    st.subheader("Record theo doc_type")
    rows = [{"doc_type": dt, **counts, "file đang hiệu lực": h["active_documents_by_doc_type"].get(dt, 0),
             "cập nhật gần nhất": h["last_updated_by_doc_type"].get(dt)} for dt, counts in h["records_by_doc_type"].items()]
    st.dataframe(df(rows), use_container_width=True, hide_index=True)
    st.subheader("File blocked chờ xử lý")
    st.dataframe(df(h["blocked_files"]["items"]), use_container_width=True, hide_index=True)
    if h["failed_ingest_jobs"]:
        st.subheader("Job ingest lỗi")
        st.dataframe(df(h["failed_ingest_jobs"]), use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    c1.subheader("Hàng đợi job")
    c1.json(h["jobs"])
    c2.subheader("Graph")
    c2.json(h["graph"], expanded=False)


# =====================================================================================
def page_abstentions() -> None:
    st.header("🙅 Câu hỏi không trả lời được gần đây — KB đang thiếu gì")
    days = st.slider("Số ngày", 1, 90, 7, key="ab_days")
    try:
        rows = api.get("/admin/abstentions", days=days, limit=1000)
    except Exception as e:
        return show_error(e)
    frame = df(rows, ["created_at", "question", "reason", "top_score", "question_type", "store_code", "user_id", "trace_id"])
    if not frame.empty:
        st.subheader("Gộp theo câu hỏi")
        g = frame.groupby("question").agg(lần=("trace_id", "count"), lý_do=("reason", lambda s: ", ".join(sorted(set(s)))),
                                          gần_nhất=("created_at", "max")).sort_values("lần", ascending=False)
        st.dataframe(g, use_container_width=True)
        st.subheader("Chi tiết")
    st.dataframe(frame, use_container_width=True, hide_index=True)


# =====================================================================================
def page_feedback() -> None:
    st.header("👎 Feedback tiêu cực (kèm trace để debug)")
    status = st.selectbox("Trạng thái", ["", "new", "seen", "fixed", "wontfix"])
    try:
        rows = api.get("/admin/feedback", rating=-1, status=status or None)
    except Exception as e:
        return show_error(e)
    st.caption(f"{len(rows)} phản hồi")
    for fb in rows:
        with st.expander(f"{fb['created_at']} · {fb['user_id']} · {fb['question'] or '(trace đã hết hạn)'}"):
            st.markdown(f"**Comment:** {fb['comment'] or '—'}")
            st.markdown(f"**Trả lời ({fb['answer_status']}):** {fb['answer'] or '—'}")
            st.caption(f"config {fb['config_version']} · trace_id {fb['trace_id']}")
            if fb["trace_url"]:
                st.markdown(f"[Mở trace trên Langfuse]({fb['trace_url']})")
            if st.button("Xem decision trace", key=f"tr_{fb['id']}"):
                try:
                    st.json(api.get(f"/admin/traces/{fb['trace_id']}")["decision_trace"])
                except Exception as e:
                    show_error(e)
            c1, c2 = st.columns([1, 2])
            new_status = c1.selectbox("Đánh dấu", ["new", "seen", "fixed", "wontfix"], index=["new", "seen", "fixed", "wontfix"].index(fb["status"]), key=f"s_{fb['id']}")
            note = c2.text_input("Ghi chú admin", fb["admin_note"] or "", key=f"an_{fb['id']}")
            if st.button("Lưu", key=f"sv_{fb['id']}"):
                try:
                    api.patch(f"/admin/feedback/{fb['id']}", {"status": new_status, "admin_note": note or None})
                    st.success("Đã lưu")
                except Exception as e:
                    show_error(e)


# =====================================================================================
def page_alias_graph() -> None:
    st.header("🔗 Alias entity & graph")
    try:
        st.json(api.get("/graph/stats"), expanded=False)
    except Exception as e:
        show_error(e)
    with st.form("alias"):
        c1, c2, c3 = st.columns(3)
        kind = c1.selectbox("Loại", ["store", "product", "sku", "category", "supplier", "promotion", "policy"])
        alias = c2.text_input("Alias (cách viết tắt/khác)")
        canonical = c3.text_input("Tên/mã chuẩn")
        note = st.text_input("Ghi chú")
        if st.form_submit_button("Lưu alias") and alias and canonical:
            try:
                st.success(api.post("/graph/aliases", {"kind": kind, "alias": alias, "canonical": canonical, "note": note or None})["hint"])
            except Exception as e:
                show_error(e)
    try:
        rows = api.get("/graph/aliases")
    except Exception as e:
        return show_error(e)
    st.dataframe(df(rows), use_container_width=True, hide_index=True)
    if rows:
        rid = st.selectbox("Xoá alias", [r["id"] for r in rows], format_func=lambda i: next(f"{r['kind']}: {r['alias']} -> {r['canonical']}" for r in rows if r["id"] == i))
        if st.button("Xoá"):
            try:
                api.delete(f"/graph/aliases/{rid}")
                st.success("Đã xoá")
            except Exception as e:
                show_error(e)
    st.divider()
    llm = st.checkbox("Chạy cả LLM extraction cho văn bản tự do khi rebuild", value=True)
    if st.button("♻️ Rebuild TOÀN BỘ graph (job nền)"):
        try:
            st.json(api.post("/graph/rebuild", {"llm": llm}))
        except Exception as e:
            show_error(e)


# =====================================================================================
def page_config() -> None:
    st.header("⚙️ Config đang chạy & chi phí")
    try:
        cost = api.get("/admin/costs", days=1)
        c1, c2 = st.columns(2)
        c1.metric("Chi phí LLM hôm nay (USD)", cost["today_usd"], help=f"ngưỡng alert {cost['daily_budget_usd']}")
        c2.dataframe(df(cost["by_role"]), hide_index=True, use_container_width=True)
    except Exception as e:
        show_error(e)
    try:
        cfg = api.get("/config")
    except Exception as e:
        return show_error(e)
    st.markdown(f"**config_version:** `{cfg['config_version']}` · **hash:** `{cfg['config_hash']}` · **file:** `{cfg['source_path']}`")
    st.subheader("Model theo vai trò")
    st.json(cfg["models"])
    st.subheader("Prompt (path + sha256)")
    st.dataframe(df([{"vai trò": k, **v} for k, v in cfg["prompts"].items()]), hide_index=True, use_container_width=True)
    with st.expander("Toàn bộ tham số"):
        st.code(json.dumps(cfg, ensure_ascii=False, indent=2), language="json")
    st.subheader("Reindex")
    with st.form("reindex"):
        c1, c2, c3 = st.columns(3)
        dt = c1.selectbox("doc_type (trống = tất cả)", DOC_TYPES, key="ri_dt")
        rechunk = c2.selectbox("rechunk", ["auto", "true", "false"])
        model = c3.text_input("Model khác (chuẩn bị đổi model)")
        if st.form_submit_button("Tạo job reindex"):
            try:
                st.json(api.post("/kb/reindex", {"doc_type": dt or None, "rechunk": rechunk, "model": model or None}))
            except Exception as e:
                show_error(e)


PAGES = {
    "Upload & job": page_upload, "Duyệt KB": page_records, "Review queue": page_review, "Conflict": page_conflicts,
    "KB health": page_health, "Abstention": page_abstentions, "Feedback 👎": page_feedback,
    "Alias & graph": page_alias_graph, "Config & chi phí": page_config,
}

st.sidebar.title("RetailKB Admin")
st.sidebar.caption(f"API: {api.API_BASE_URL}")
if not api.ADMIN_API_KEY:
    st.sidebar.error("Chưa đặt ADMIN_API_KEY")
choice = st.sidebar.radio("Trang", list(PAGES))
PAGES[choice]()
