// Danh sách tài liệu + upload (kéo thả nhiều file, tuỳ chọn loại/hiệu lực/thay thế) + theo dõi job.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { FileText, RefreshCw, Search, Upload, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Empty, ErrorBox, Field, Loading, Modal, PageHeader, Pagination, Spinner, StatusBadge, useToast } from "@/components/ui";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtBytes, fmtDate, fmtRelative, SOURCE_ICON, STATUS_LABEL, truncate } from "@/lib/format";
import type { Job } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

const ACCEPT = ".xlsx,.xls,.csv,.docx,.pdf,.pptx,.txt,.md,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.mp4,.mov";

export function DocumentsPage() {
  const { wsId, can, ws } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const [status, setStatus] = useState(params.get("status") || "");
  const [docType, setDocType] = useState(params.get("doc_type") || "");
  const [page, setPage] = useState(1);
  const [uploadOpen, setUploadOpen] = useState(params.get("upload") === "1");
  const [includeDeleted, setIncludeDeleted] = useState(false);
  useEffect(() => { if (params.get("upload") === "1") { params.delete("upload"); setParams(params, { replace: true }); } }, [params, setParams]);

  const docs = useQuery({
    queryKey: ["documents", wsId, { q, status, docType, page, includeDeleted }],
    queryFn: () => api.documents(wsId, { q: q || undefined, status: status || undefined, doc_type: docType || undefined, page, page_size: 25, include_deleted: includeDeleted || undefined }),
    refetchInterval: (query) => (query.state.data?.items.some((d) => d.status === "uploaded" || d.status === "processing") ? 5000 : false),
  });
  const jobs = useQuery({ queryKey: ["jobs", wsId, "active"], queryFn: () => api.jobs(wsId, { limit: 50 }), refetchInterval: 5000 });
  const activeJobs = useMemo(() => new Map((jobs.data || []).filter((j) => j.status === "queued" || j.status === "processing").map((j) => [j.document_id, j])), [jobs.data]);
  const docTypes = useMemo(() => [...new Set((docs.data?.items || []).map((d) => d.doc_type))].sort(), [docs.data]);

  return (
    <Page wide>
      <PageHeader title="Tài liệu" subtitle="Mọi file đã tải lên: trạng thái xử lý, tóm tắt, bảng số liệu và thực thể trích được."
        actions={<>
          <button className="btn-secondary" onClick={() => docs.refetch()}><RefreshCw className={clsx("h-4 w-4", docs.isFetching && "animate-spin")} /></button>
          {can("editor") && <button className="btn-primary" onClick={() => setUploadOpen(true)}><Upload className="h-4 w-4" /> Tải tài liệu</button>}
        </>} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input className="input w-64 pl-8" placeholder="Tìm theo tên, nội dung…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <select className="input w-44" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">Mọi trạng thái</option>
          {["active", "uploaded", "processing", "pending_review", "blocked", "error", "superseded"].map((s) => <option key={s} value={s}>{STATUS_LABEL[s] || s}</option>)}
        </select>
        {docTypes.length > 1 && (
          <select className="input w-44" value={docType} onChange={(e) => { setDocType(e.target.value); setPage(1); }}>
            <option value="">Mọi loại</option>
            {docTypes.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
        <label className="flex items-center gap-1.5 text-sm text-slate-600"><input type="checkbox" checked={includeDeleted} onChange={(e) => setIncludeDeleted(e.target.checked)} /> Gồm đã xoá</label>
      </div>

      {docs.isLoading && <Loading />}
      {docs.error && <ErrorBox error={docs.error} retry={() => docs.refetch()} />}
      {docs.data && docs.data.items.length === 0 && (
        <Empty icon={<FileText className="h-10 w-10" />} title={q || status ? "Không có tài liệu phù hợp" : "Chưa có tài liệu"}
          hint="Hỗ trợ Excel/CSV, Word, PDF (kể cả scan), PowerPoint, ảnh, audio/video, text/markdown."
          action={can("editor") && <button className="btn-primary" onClick={() => setUploadOpen(true)}><Upload className="h-4 w-4" /> Tải tài liệu</button>} />
      )}
      {docs.data && docs.data.items.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full">
            <thead>
              <tr><th className="th">Tài liệu</th><th className="th">Loại</th><th className="th">Trạng thái</th><th className="th text-right">Bản ghi</th><th className="th text-right">Bảng</th><th className="th">Hiệu lực</th><th className="th">Cập nhật</th></tr>
            </thead>
            <tbody>
              {docs.data.items.map((d) => {
                const job = activeJobs.get(d.document_id);
                return (
                  <tr key={d.document_id} className={clsx("hover:bg-slate-50", d.deleted_at && "opacity-60")}>
                    <td className="td max-w-md">
                      <Link to={d.document_id} className="flex items-start gap-2">
                        <span className="text-lg leading-none">{SOURCE_ICON[d.source_type] || "📄"}</span>
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-slate-900 hover:text-brand-700">{d.title}</span>
                          <span className="block truncate text-xs text-slate-500">{d.summary ? truncate(d.summary, 110) : `${d.filename} · ${fmtBytes(d.size_bytes)}`}</span>
                        </span>
                      </Link>
                    </td>
                    <td className="td whitespace-nowrap">{d.doc_type}{ws.domain_pack === "retail" && d.scope ? <span className="ml-1 text-xs text-slate-400">· {d.scope}</span> : null}</td>
                    <td className="td whitespace-nowrap">
                      <StatusBadge status={d.deleted_at ? "deleted" : d.status} />
                      {job && <JobProgress job={job} />}
                    </td>
                    <td className="td text-right">{d.record_count}</td>
                    <td className="td text-right">{d.table_count || 0}</td>
                    <td className="td whitespace-nowrap">{fmtDate(d.effective_date)}</td>
                    <td className="td whitespace-nowrap text-slate-500">{fmtRelative(d.updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="px-3"><Pagination page={page} pageSize={25} total={docs.data.total} onChange={setPage} /></div>
        </div>
      )}
      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)} />
    </Page>
  );
}

export function JobProgress({ job }: { job: Job }) {
  return (
    <div className="mt-1 w-32">
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className="h-full bg-brand-500 transition-all" style={{ width: `${job.progress.percent ?? (job.status === "processing" ? 15 : 3)}%` }} /></div>
      <div className="mt-0.5 text-[11px] text-slate-500">{job.type}{job.progress.percent !== null ? ` · ${job.progress.percent}%` : ""}</div>
    </div>
  );
}

export function UploadModal({ open, onClose, replaceOf }: { open: boolean; onClose: () => void; replaceOf?: { series_key: string | null; title: string } }) {
  const { wsId, ws } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [opts, setOpts] = useState<{ doc_type: string; effective_date: string; title: string; version: string; scope: string; replace_mode: string }>({ doc_type: "", effective_date: "", title: "", version: "", scope: "", replace_mode: "document" });
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const packs = useQuery({ queryKey: ["domain-packs"], queryFn: api.domainPacks, enabled: open });
  const docTypes = packs.data?.find((p) => p.key === ws.domain_pack)?.doc_types || [];
  const mut = useMutation({
    mutationFn: () => api.upload(wsId, files, { ...opts, title: files.length === 1 ? opts.title : "", series_key: replaceOf?.series_key || undefined }),
    onSuccess: (r) => {
      const dup = r.jobs.filter((j) => j.duplicate_of_existing).length;
      toast.ok(`Đã nhận ${r.jobs.length} file${dup ? ` (${dup} trùng nội dung đã có)` : ""} — đang xử lý nền`);
      void qc.invalidateQueries({ queryKey: ["documents", wsId] });
      void qc.invalidateQueries({ queryKey: ["jobs", wsId] });
      setFiles([]);
      onClose();
    },
    onError: toast.err,
  });
  const add = (list: FileList | null) => { if (list) setFiles((xs) => [...xs, ...Array.from(list)]); };
  return (
    <Modal open={open} onClose={onClose} title={replaceOf ? `Tải phiên bản mới: ${replaceOf.title}` : "Tải tài liệu"} width="max-w-2xl"
      footer={<><button className="btn-secondary" onClick={onClose}>Huỷ</button><button className="btn-primary" disabled={!files.length || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />} Tải lên {files.length ? `(${files.length})` : ""}</button></>}>
      <div className={clsx("flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 text-center transition", drag ? "border-brand-500 bg-brand-50" : "border-slate-300 hover:bg-slate-50")}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }} onClick={() => inputRef.current?.click()}>
        <Upload className="h-8 w-8 text-brand-500" />
        <div className="mt-2 text-sm font-medium text-slate-700">Kéo thả file vào đây hoặc bấm để chọn</div>
        <div className="mt-1 text-xs text-slate-500">Excel, CSV, Word, PDF, PowerPoint, ảnh, audio/video, text — nhiều file cùng lúc</div>
        <input ref={inputRef} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </div>
      {files.length > 0 && (
        <ul className="mt-3 max-h-40 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200 text-sm">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center gap-2 px-3 py-1.5">
              <span className="flex-1 truncate">{f.name}</span><span className="text-xs text-slate-500">{fmtBytes(f.size)}</span>
              <button className="btn-ghost btn-sm" onClick={() => setFiles((xs) => xs.filter((_, j) => j !== i))}><X className="h-3.5 w-3.5" /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field label="Loại tài liệu" hint="Để trống: hệ thống tự phân loại">
          {docTypes.length ? (
            <select className="input" value={opts.doc_type} onChange={(e) => setOpts({ ...opts, doc_type: e.target.value })}><option value="">Tự nhận diện</option>{docTypes.map((t) => <option key={t} value={t}>{t}</option>)}</select>
          ) : <input className="input" value={opts.doc_type} onChange={(e) => setOpts({ ...opts, doc_type: e.target.value })} placeholder="Tự nhận diện" />}
        </Field>
        <Field label="Ngày hiệu lực" hint="Dùng khi hỏi 'tại thời điểm…' và xử lý xung đột"><input className="input" type="date" value={opts.effective_date} onChange={(e) => setOpts({ ...opts, effective_date: e.target.value })} /></Field>
        {files.length === 1 && <Field label="Tiêu đề (tuỳ chọn)"><input className="input" value={opts.title} onChange={(e) => setOpts({ ...opts, title: e.target.value })} placeholder="Mặc định lấy từ nội dung/tên file" /></Field>}
        <Field label="Phiên bản (tuỳ chọn)"><input className="input" value={opts.version} onChange={(e) => setOpts({ ...opts, version: e.target.value })} placeholder="v2, 2026-Q4…" /></Field>
        {ws.member_attributes.length > 0 && <Field label={ws.member_attributes[0].label} hint={ws.member_attributes[0].description}><input className="input" value={opts.scope} onChange={(e) => setOpts({ ...opts, scope: e.target.value })} /></Field>}
        <Field label="Khi trùng tài liệu cũ" hint="document: bản mới thay bản cũ cùng series; none: giữ song song">
          <select className="input" value={opts.replace_mode} onChange={(e) => setOpts({ ...opts, replace_mode: e.target.value })}><option value="document">Thay thế bản cũ</option><option value="none">Giữ song song</option></select>
        </Field>
      </div>
    </Modal>
  );
}
