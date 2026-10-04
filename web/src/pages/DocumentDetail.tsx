// Chi tiết 1 tài liệu: tóm tắt + hồ sơ ngữ nghĩa, bảng số liệu, thực thể, bản ghi, job; hành động vòng đời.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, MessageSquare, Pencil, RefreshCw, RotateCcw, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Confirm, ErrorBox, Field, Loading, Modal, PageHeader, Pagination, Spinner, StatusBadge, Tabs, useToast } from "@/components/ui";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtBytes, fmtDate, fmtNumber, fmtRelative, SOURCE_ICON } from "@/lib/format";
import type { DocumentDetail } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { JobProgress, UploadModal } from "./Documents";

type Tab = "overview" | "tables" | "entities" | "records" | "jobs";

export function DocumentDetailPage() {
  const { wsId, can } = useWorkspace();
  const { documentId = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("overview");
  const [edit, setEdit] = useState(false);
  const [del, setDel] = useState(false);
  const [replace, setReplace] = useState(false);
  const doc = useQuery({
    queryKey: ["document", wsId, documentId], queryFn: () => api.document(wsId, documentId),
    refetchInterval: (q) => (q.state.data?.jobs.some((j) => j.status === "queued" || j.status === "processing") ? 4000 : false),
  });
  const invalidate = () => { void qc.invalidateQueries({ queryKey: ["document", wsId, documentId] }); void qc.invalidateQueries({ queryKey: ["documents", wsId] }); };
  const act = useMutation({
    mutationFn: async (a: "approve" | "reprocess" | "delete" | "restore" | "reindex") => {
      if (a === "approve") return api.approveDocument(wsId, documentId);
      if (a === "reprocess") return api.reprocessDocument(wsId, documentId);
      if (a === "delete") return api.deleteDocument(wsId, documentId);
      if (a === "restore") return api.restoreDocument(wsId, documentId);
      return api.reindex(wsId, { document_id: documentId });
    },
    onSuccess: (_r, a) => { toast.ok({ approve: "Đã duyệt tài liệu", reprocess: "Đã xếp hàng xử lý lại", delete: "Đã xoá (có thể khôi phục)", restore: "Đã khôi phục", reindex: "Đã xếp hàng reindex" }[a]); setDel(false); invalidate(); },
    onError: toast.err,
  });

  if (doc.isLoading) return <Page><Loading /></Page>;
  if (doc.error || !doc.data) return <Page><ErrorBox error={doc.error || "Không có tài liệu"} /></Page>;
  const d = doc.data;
  const running = d.jobs.find((j) => j.status === "queued" || j.status === "processing");

  return (
    <Page wide>
      <Link to={`/w/${wsId}/documents`} className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" /> Tài liệu</Link>
      <PageHeader
        title={<span className="flex items-center gap-2"><span>{SOURCE_ICON[d.source_type] || "📄"}</span>{d.title}<StatusBadge status={d.deleted_at ? "deleted" : d.status} /></span>}
        subtitle={<span className="flex flex-wrap gap-x-3 gap-y-1">
          <span>{d.filename} · {fmtBytes(d.size_bytes)}</span><span>Loại: <b>{d.doc_type}</b></span>
          {d.effective_date && <span>Hiệu lực: {fmtDate(d.effective_date)}{d.valid_to ? ` → ${fmtDate(d.valid_to)}` : ""}</span>}
          {d.version && <span>Phiên bản: {d.version}</span>}{d.scope && <span>Phạm vi: {d.scope}</span>}
          <span>Cập nhật {fmtRelative(d.updated_at)}</span>
        </span>}
        actions={<>
          <button className="btn-primary" onClick={() => nav(`/w/${wsId}/chat?q=${encodeURIComponent(`Tóm tắt nội dung chính của tài liệu "${d.title}"`)}`)}><MessageSquare className="h-4 w-4" /> Hỏi về tài liệu này</button>
          {can("editor") && !d.deleted_at && <>
            {d.status === "pending_review" && <button className="btn-secondary" onClick={() => act.mutate("approve")}><Check className="h-4 w-4" /> Duyệt</button>}
            <button className="btn-secondary" onClick={() => setEdit(true)}><Pencil className="h-4 w-4" /> Sửa</button>
            <button className="btn-secondary" onClick={() => setReplace(true)}><Upload className="h-4 w-4" /> Phiên bản mới</button>
            <button className="btn-secondary" disabled={!!running} onClick={() => act.mutate("reprocess")} title="Chạy lại tóm tắt, bảng số liệu, thực thể"><RefreshCw className="h-4 w-4" /> Xử lý lại</button>
            <button className="btn-danger" onClick={() => setDel(true)}><Trash2 className="h-4 w-4" /></button>
          </>}
          {can("editor") && d.deleted_at && <button className="btn-secondary" onClick={() => act.mutate("restore")}><RotateCcw className="h-4 w-4" /> Khôi phục</button>}
        </>} />

      {running && <div className="mb-4 flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800"><Spinner /> Đang xử lý: {running.type}<JobProgress job={running} /></div>}
      {d.status === "blocked" && <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">File bị chặn khi xử lý: {d.jobs.find((j) => j.status === "blocked")?.error || "xem chi tiết ở tab Xử lý"}</div>}
      {d.superseded_by_id && <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">Đã được thay thế bởi <Link className="text-brand-700 underline" to={`/w/${wsId}/documents/${d.superseded_by_id}`}>phiên bản mới hơn</Link>.</div>}

      <Tabs value={tab} onChange={setTab} tabs={[
        { key: "overview", label: "Tổng quan" }, { key: "tables", label: "Bảng số liệu", count: d.tables.length }, { key: "entities", label: "Thực thể", count: d.entities.length },
        { key: "records", label: "Bản ghi", count: d.record_count_live }, { key: "jobs", label: "Xử lý", count: d.jobs.length }]} />
      <div className="mt-4">
        {tab === "overview" && <OverviewTab d={d} />}
        {tab === "tables" && <TablesTab d={d} />}
        {tab === "entities" && <EntitiesTab d={d} />}
        {tab === "records" && <RecordsTab documentId={documentId} />}
        {tab === "jobs" && <JobsTab d={d} />}
      </div>
      <EditModal open={edit} onClose={() => setEdit(false)} d={d} onSaved={invalidate} />
      <UploadModal open={replace} onClose={() => setReplace(false)} replaceOf={{ series_key: d.series_key, title: d.title }} />
      <Confirm open={del} onClose={() => setDel(false)} onConfirm={() => act.mutate("delete")} danger busy={act.isPending} title="Xoá tài liệu"
        message="Tài liệu và các bản ghi sẽ bị ẩn khỏi tìm kiếm (soft delete), có thể khôi phục sau." />
    </Page>
  );
}

function OverviewTab({ d }: { d: DocumentDetail }) {
  const { wsId } = useWorkspace();
  const nav = useNavigate();
  const p = d.semantic_profile || {};
  const topics = p.topics || [];
  const sq = p.suggested_questions || [];
  const stats = p.stats || {};
  const STAT_LABEL: Record<string, string> = { rows: "Dòng", sheets: "Sheet", tables: "Bảng", records: "Bản ghi", characters: "Ký tự", table_rows: "Dòng dữ liệu", pages: "Trang", headings: "Đề mục", articles: "Điều khoản", duration_sec: "Thời lượng (s)", words: "Từ" };
  const SENS: Record<string, { label: string; tone: "slate" | "amber" | "red" }> = { none: { label: "Không nhạy cảm", tone: "slate" }, internal: { label: "Nội bộ", tone: "amber" }, confidential: { label: "Bảo mật", tone: "red" }, personal: { label: "Có dữ liệu cá nhân", tone: "red" } };
  const sens = p.sensitivity ? SENS[p.sensitivity] || { label: p.sensitivity, tone: "amber" as const } : null;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <section className="card p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-900">Tóm tắt</h3>
          {d.summary ? <p className="whitespace-pre-wrap text-[15px] leading-7 text-slate-700">{d.summary}</p> : <p className="text-sm text-slate-500">Chưa có tóm tắt (sẽ có sau khi xử lý xong).</p>}
          {topics.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{topics.map((t) => <Badge key={t} tone="brand">{t}</Badge>)}</div>}
        </section>
        {p.structure && (
          <section className="card p-4">
            <h3 className="mb-1 text-sm font-semibold text-slate-900">Cấu trúc</h3>
            <p className="text-sm leading-6 text-slate-700">{p.structure}</p>
          </section>
        )}
        {p.key_entities && p.key_entities.length > 0 && (
          <section className="card p-4">
            <h3 className="mb-2 text-sm font-semibold text-slate-900">Đối tượng nổi bật</h3>
            <div className="flex flex-wrap gap-1.5">{p.key_entities.map((e, i) => <Link key={`${e.name}-${i}`} to={`/w/${wsId}/graph?q=${encodeURIComponent(e.name)}`} className="badge bg-slate-100 text-slate-700 hover:bg-brand-50 hover:text-brand-800" title={e.type}>{e.name}<span className="text-slate-400">· {e.type}</span></Link>)}</div>
          </section>
        )}
        {sq.length > 0 && (
          <section className="card p-4">
            <h3 className="mb-2 text-sm font-semibold text-slate-900">Câu hỏi gợi ý</h3>
            <div className="flex flex-col">{sq.map((s) => <button key={s} className="rounded-lg px-3 py-1.5 text-left text-sm text-slate-700 hover:bg-brand-50 hover:text-brand-800" onClick={() => nav(`/w/${wsId}/chat?q=${encodeURIComponent(s)}`)}>{s}</button>)}</div>
          </section>
        )}
        {p.quality_notes && (
          <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800"><b>Lưu ý chất lượng:</b> {p.quality_notes}</section>
        )}
      </div>
      <div className="space-y-4">
        <section className="card p-4 text-sm">
          <h3 className="mb-2 text-sm font-semibold text-slate-900">Hồ sơ ngữ nghĩa</h3>
          <dl className="space-y-1.5">
            {p.kind ? <Row k="Bản chất" v={p.kind} /> : null}
            {p.audience ? <Row k="Đối tượng đọc" v={p.audience} /> : null}
            {p.language ? <Row k="Ngôn ngữ" v={p.language} /> : null}
            {p.time_range && (p.time_range.from || p.time_range.to || p.time_range.note) ? <Row k="Khoảng thời gian" v={p.time_range.from || p.time_range.to ? `${fmtDate(p.time_range.from)} → ${fmtDate(p.time_range.to)}` : p.time_range.note || ""} /> : null}
            {sens ? <Row k="Độ nhạy cảm" v={<Badge tone={sens.tone}>{sens.label}</Badge>} /> : null}
            <Row k="Định dạng" v={d.source_type} />
            {Object.entries(stats).map(([k, v]) => <Row key={k} k={STAT_LABEL[k] || k} v={fmtNumber(v)} />)}
            {p.generated_by ? <Row k="Nguồn hồ sơ" v={p.generated_by === "llm" ? "AI phân tích" : "thống kê"} /> : null}
          </dl>
        </section>
        <section className="card p-4 text-sm">
          <h3 className="mb-2 font-semibold text-slate-900">Nguồn gốc</h3>
          <dl className="space-y-1.5"><Row k="Tải lên bởi" v={d.uploaded_by || "—"} /><Row k="Tải lên" v={fmtDate(d.created_at, true)} />{d.series_key && <Row k="Series" v={d.series_key} />}{d.supersedes_id && <Row k="Thay cho" v={<Link className="text-brand-700 underline" to={`/w/${wsId}/documents/${d.supersedes_id}`}>bản trước</Link>} />}</dl>
        </section>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3"><dt className="text-slate-500">{k}</dt><dd className="text-right text-slate-800">{v}</dd></div>;
}

function TablesTab({ d }: { d: DocumentDetail }) {
  const { wsId } = useWorkspace();
  if (!d.tables.length) return <div className="card p-6 text-center text-sm text-slate-500">Không trích được bảng số liệu từ tài liệu này.</div>;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {d.tables.map((t) => (
        <Link key={t.id} to={`/w/${wsId}/tables/${t.id}`} className="card p-4 hover:border-brand-300">
          <div className="flex items-start justify-between gap-2"><div className="font-medium text-slate-900">{t.title}</div><Badge>{fmtNumber(t.row_count)} dòng</Badge></div>
          <div className="mt-0.5 font-mono text-xs text-slate-500">{t.name}{t.source_location ? ` · ${t.source_location}` : ""}</div>
          {t.description && <p className="mt-2 text-sm text-slate-600">{t.description}</p>}
          <div className="mt-2 flex flex-wrap gap-1">{t.columns.slice(0, 8).map((c) => <span key={c.name} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600">{c.name}</span>)}{t.columns.length > 8 && <span className="text-[11px] text-slate-400">+{t.columns.length - 8}</span>}</div>
        </Link>
      ))}
    </div>
  );
}

function EntitiesTab({ d }: { d: DocumentDetail }) {
  const { wsId } = useWorkspace();
  if (!d.entities.length) return <div className="card p-6 text-center text-sm text-slate-500">Chưa có thực thể (chạy sau khi xử lý xong).</div>;
  const byType = d.entities.reduce<Record<string, typeof d.entities>>((acc, e) => { (acc[e.type] ||= []).push(e); return acc; }, {});
  return (
    <div className="space-y-4">
      {Object.entries(byType).map(([type, list]) => (
        <section key={type} className="card p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-900">{type} <span className="text-slate-400">({list.length})</span></h3>
          <div className="flex flex-wrap gap-1.5">{list.map((e) => <Link key={e.id} to={`/w/${wsId}/graph/${e.id}`} className="badge bg-slate-100 text-slate-700 hover:bg-brand-50 hover:text-brand-800" title={e.description || ""}>{e.name}<span className="text-slate-400">×{e.mentions}</span></Link>)}</div>
        </section>
      ))}
    </div>
  );
}

function RecordsTab({ documentId }: { documentId: string }) {
  const { wsId } = useWorkspace();
  const [page, setPage] = useState(1);
  const q = useQuery({ queryKey: ["doc-records", wsId, documentId, page], queryFn: () => api.documentRecords(wsId, documentId, { page, page_size: 25 }) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <div className="card">
      <ul className="divide-y divide-slate-100">
        {q.data?.items.map((r) => (
          <li key={r.record_id} className="px-4 py-3">
            <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500"><Badge>{r.location || r.doc_type}</Badge>{r.citation && <span>{r.citation}</span>}{!r.is_active && <StatusBadge status="superseded" />}{r.suspicious_content && <Badge tone="amber">Nội dung nghi ngờ</Badge>}</div>
            <div className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{r.content}</div>
          </li>
        ))}
      </ul>
      <div className="px-3"><Pagination page={page} pageSize={25} total={q.data?.total || 0} onChange={setPage} /></div>
    </div>
  );
}

function JobsTab({ d }: { d: DocumentDetail }) {
  return (
    <div className="card overflow-hidden">
      <table className="w-full"><thead><tr><th className="th">Loại</th><th className="th">Trạng thái</th><th className="th">Tiến độ</th><th className="th">Bắt đầu</th><th className="th">Kết thúc</th><th className="th">Kết quả / lỗi</th></tr></thead>
        <tbody>{d.jobs.map((j) => (
          <tr key={j.job_id}><td className="td font-mono text-xs">{j.type}</td><td className="td"><StatusBadge status={j.status} /></td><td className="td text-xs">{j.progress.percent !== null ? `${j.progress.percent}%` : "—"}</td>
            <td className="td whitespace-nowrap text-xs">{fmtDate(j.started_at, true)}</td><td className="td whitespace-nowrap text-xs">{fmtDate(j.finished_at, true)}</td>
            <td className="td max-w-md text-xs">{j.error ? <span className="text-red-600">{j.error}</span> : j.result ? <code className="break-all text-slate-500">{JSON.stringify(j.result).slice(0, 200)}</code> : "—"}</td></tr>
        ))}</tbody></table>
    </div>
  );
}

function EditModal({ open, onClose, d, onSaved }: { open: boolean; onClose: () => void; d: DocumentDetail; onSaved: () => void }) {
  const { wsId } = useWorkspace();
  const toast = useToast();
  const [form, setForm] = useState({ title: d.title, doc_type: d.doc_type, effective_date: d.effective_date || "", version: d.version || "" });
  const mut = useMutation({
    mutationFn: () => api.updateDocument(wsId, d.document_id, { title: form.title, doc_type: form.doc_type, effective_date: form.effective_date || null, version: form.version || null }),
    onSuccess: () => { toast.ok("Đã lưu"); onSaved(); onClose(); }, onError: toast.err,
  });
  return (
    <Modal open={open} onClose={onClose} title="Sửa thông tin tài liệu" footer={<><button className="btn-secondary" onClick={onClose}>Huỷ</button><button className="btn-primary" disabled={mut.isPending} onClick={() => mut.mutate()}>Lưu</button></>}>
      <div className="space-y-3">
        <Field label="Tiêu đề"><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
        <Field label="Loại tài liệu"><input className="input" value={form.doc_type} onChange={(e) => setForm({ ...form, doc_type: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Ngày hiệu lực"><input className="input" type="date" value={form.effective_date} onChange={(e) => setForm({ ...form, effective_date: e.target.value })} /></Field>
          <Field label="Phiên bản"><input className="input" value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} /></Field>
        </div>
      </div>
    </Modal>
  );
}
