// Nguồn dữ liệu: danh sách file (tìm/lọc/phân trang) + tải lên (kéo thả nhiều file, tuỳ chọn gói trong "Thêm tuỳ chọn")
// + tiến độ xử lý thân thiện. Chi tiết kỹ thuật (tên job, mã) chỉ hiện khi bật "Chi tiết kỹ thuật".
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronDown, CloudUpload, FileStack, RefreshCw, Search, Upload, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { DocType, docTypeLabel } from "@/components/SourceDrawer";
import { Empty, ErrorBox, Field, Loading, Modal, PageHeader, Pagination, SourceIcon, Spinner, StatusBadge, useToast } from "@/components/ui";
import { useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { docStatus, fmtBytes, fmtDate, fmtNumber, fmtRelative, statusLabel, truncate } from "@/lib/format";
import type { Document, Job } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { Advanced, usePrefs } from "@/state/prefs";

const ACCEPT = ".xlsx,.xls,.csv,.docx,.pdf,.pptx,.txt,.md,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.mp4,.mov";
const PAGE_SIZE = 25;
const STATUSES = ["active", "uploaded", "processing", "pending_review", "blocked", "error", "superseded"];
const KNOWN_JOBS = new Set(["ingest_file", "reindex", "graph_sync", "graph_rebuild", "document_lifecycle", "knowledge_extract"]);

/** Đoán loại nguồn từ đuôi file để hiện icon trong danh sách file chờ tải. */
function typeFromName(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (ext === "xls" || ext === "xlsx") return "xlsx";
  if (["png", "jpg", "jpeg", "webp"].includes(ext)) return "image";
  if (["mp3", "wav", "m4a"].includes(ext)) return "audio";
  if (["mp4", "mov"].includes(ext)) return "video";
  return ext;
}

const isRunning = (j: Job) => j.status === "queued" || j.status === "processing";

export function DocumentsPage() {
  const t = useT();
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
    queryFn: () => api.documents(wsId, { q: q || undefined, status: status || undefined, doc_type: docType || undefined, page, page_size: PAGE_SIZE, include_deleted: includeDeleted || undefined }),
    refetchInterval: (query) => (query.state.data?.items.some((d) => d.status === "uploaded" || d.status === "processing") ? 5000 : false),
  });
  const jobs = useQuery({ queryKey: ["jobs", wsId, "active"], queryFn: () => api.jobs(wsId, { limit: 50 }), refetchInterval: 5000 });
  const activeJobs = useMemo(() => new Map((jobs.data || []).filter(isRunning).map((j) => [j.document_id, j])), [jobs.data]);
  const busyFiles = useMemo(() => [...activeJobs.keys()].filter(Boolean).length, [activeJobs]);
  const docTypes = useMemo(() => [...new Set([...(docs.data?.items || []).map((d) => d.doc_type), ...(docType ? [docType] : [])])].sort(), [docs.data, docType]);
  const filtered = !!(q || status || docType || includeDeleted);
  const clearFilters = () => { setQ(""); setStatus(""); setDocType(""); setIncludeDeleted(false); setPage(1); };
  const uploadBtn = can("editor") && (
    <button className="btn-primary" onClick={() => setUploadOpen(true)}><Upload className="h-4 w-4" /> {t("action.upload")}</button>
  );

  return (
    <Page wide>
      <PageHeader title={t("sources.title")} subtitle={t("sources.subtitle")}
        actions={<>
          <button className="btn-ghost" onClick={() => docs.refetch()} aria-label={t("action.refresh")} title={t("action.refresh")}>
            <RefreshCw className={clsx("h-4 w-4", docs.isFetching && "animate-spin")} />
          </button>
          {uploadBtn}
        </>} />

      {busyFiles > 0 && (
        <div className="mb-5 flex items-center gap-3 rounded-xl border border-brand-100 bg-brand-50/70 px-4 py-3 text-sm text-ink-800" role="status">
          <Spinner />
          <span>{t(busyFiles === 1 ? "sources.list.busyOne" : "sources.list.busyMany", { n: busyFiles })}</span>
        </div>
      )}

      <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
          <input className="input pl-9" type="search" placeholder={t("sources.list.searchPlaceholder")} aria-label={t("sources.list.searchPlaceholder")}
            value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <select className="input sm:w-48" aria-label={t("sources.list.statusFilter")} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">{t("sources.list.anyStatus")}</option>
          {STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
        </select>
        {docTypes.length > 1 && (
          <select className="input sm:w-48" aria-label={t("sources.list.typeFilter")} value={docType} onChange={(e) => { setDocType(e.target.value); setPage(1); }}>
            <option value="">{t("sources.list.anyType")}</option>
            {docTypes.map((dt) => <option key={dt} value={dt}>{docTypeLabel(dt)}</option>)}
          </select>
        )}
        <label className="flex cursor-pointer items-center gap-2 px-1 text-sm text-ink-700">
          <input type="checkbox" className="h-4 w-4 rounded accent-brand-600" checked={includeDeleted} onChange={(e) => { setIncludeDeleted(e.target.checked); setPage(1); }} />
          {t("sources.list.includeDeleted")}
        </label>
        {filtered && <button className="btn-ghost btn-sm" onClick={clearFilters}>{t("sources.list.clearFilters")}</button>}
      </div>

      {docs.isLoading && <Loading />}
      {docs.error && <ErrorBox error={docs.error} retry={() => docs.refetch()} />}
      {docs.data && docs.data.items.length === 0 && (
        <div className="panel">
          {filtered ? (
            <Empty icon={<Search />} title={t("sources.list.noMatch")} hint={t("sources.list.noMatchHint")}
              action={<button className="btn-secondary" onClick={clearFilters}>{t("sources.list.clearFilters")}</button>} />
          ) : (
            <Empty icon={<FileStack />} title={t("sources.list.emptyTitle")} hint={t("sources.list.emptyHint")} action={uploadBtn || undefined} />
          )}
        </div>
      )}
      {docs.data && docs.data.items.length > 0 && (
        <div className="panel overflow-hidden">
          <ul className="divide-y divide-ink-100">
            {docs.data.items.map((d) => (
              <DocumentRow key={d.document_id} d={d} job={activeJobs.get(d.document_id)} showScope={ws.domain_pack === "retail"} />
            ))}
          </ul>
          <div className="border-t border-ink-100 px-5"><Pagination page={page} pageSize={PAGE_SIZE} total={docs.data.total} onChange={setPage} /></div>
        </div>
      )}
      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)} />
    </Page>
  );
}

function DocumentRow({ d, job, showScope }: { d: Document; job?: Job; showScope: boolean }) {
  const t = useT();
  const shownStatus = d.deleted_at ? "deleted" : docStatus(d.status);
  return (
    <li className={clsx(d.deleted_at && "opacity-60")}>
      <Link to={d.document_id} className="group flex gap-4 px-4 py-4 transition hover:bg-paper focus-visible:bg-paper focus-visible:rounded-none focus-visible:shadow-[inset_0_0_0_3px_rgba(91,76,245,.35)] sm:px-5">
        <SourceIcon type={d.source_type} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="min-w-0 truncate font-medium text-ink-900 group-hover:text-brand-700">{d.title}</span>
            {shownStatus !== "active" && <StatusBadge status={shownStatus} />}
          </div>
          <p className="mt-0.5 line-clamp-2 text-sm leading-6 text-ink-600">
            {d.summary ? truncate(d.summary, 220) : t("sources.fileAndSize", { name: d.filename, size: fmtBytes(d.size_bytes) })}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-600">
            <span className="font-medium text-ink-700"><DocType code={d.doc_type} /></span>
            {showScope && d.scope && <span>{d.scope}</span>}
            {d.record_count > 0 && <span>{t(d.record_count === 1 ? "sources.count.passagesOne" : "sources.count.passages", { n: fmtNumber(d.record_count) })}</span>}
            {d.table_count > 0 && <span>{t(d.table_count === 1 ? "sources.count.tablesOne" : "sources.count.tables", { n: fmtNumber(d.table_count) })}</span>}
            {d.effective_date && <span>{t("sources.inEffectFrom", { date: fmtDate(d.effective_date) })}</span>}
            {d.version && <span>{t("sources.versionN", { v: d.version })}</span>}
            <span className="sm:hidden">{t("sources.updatedAgo", { when: fmtRelative(d.updated_at) })}</span>
          </div>
          {job && <JobProgress job={job} />}
        </div>
        <div className="hidden shrink-0 text-right text-xs text-ink-600 sm:block">{t("sources.updatedAgo", { when: fmtRelative(d.updated_at) })}</div>
      </Link>
    </li>
  );
}

function jobLabel(job: Job): string {
  if (job.status === "queued") return "sources.job.waiting";
  return KNOWN_JOBS.has(job.type) ? `sources.job.type.${job.type}` : "sources.job.type.other";
}

export function JobProgress({ job }: { job: Job }) {
  const t = useT();
  const pct = job.progress.percent;
  const width = pct ?? (job.status === "processing" ? 15 : 3);
  const label = t(jobLabel(job));
  return (
    <div className="mt-2 w-full max-w-[240px]">
      <div className="h-1.5 overflow-hidden rounded-full bg-ink-100" role="progressbar" aria-label={label}
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
        <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${width}%` }} />
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-ink-600">
        <span>{pct !== null ? t("sources.job.withPercent", { label, pct }) : label}</span>
        <Advanced><code className="font-mono text-[11px] text-ink-400">{job.type}</code></Advanced>
      </div>
    </div>
  );
}

type UploadOpts = { doc_type: string; effective_date: string; title: string; version: string; scope: string; replace_mode: string };
const EMPTY_OPTS: UploadOpts = { doc_type: "", effective_date: "", title: "", version: "", scope: "", replace_mode: "document" };

export function UploadModal({ open, onClose, replaceOf }: { open: boolean; onClose: () => void; replaceOf?: { series_key: string | null; title: string } }) {
  const t = useT();
  const { advanced } = usePrefs();
  const { wsId, ws } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [opts, setOpts] = useState<UploadOpts>(EMPTY_OPTS);
  const [more, setMore] = useState(false);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const moreId = useId();
  const packs = useQuery({ queryKey: ["domain-packs"], queryFn: api.domainPacks, enabled: open });
  const docTypes = packs.data?.find((p) => p.key === ws.domain_pack)?.doc_types || [];
  const mut = useMutation({
    mutationFn: () => api.upload(wsId, files, { ...opts, title: files.length === 1 ? opts.title : "", series_key: replaceOf?.series_key || undefined }),
    onSuccess: (r) => {
      const dup = r.jobs.filter((j) => j.duplicate_of_existing).length;
      const n = r.jobs.length;
      toast.ok(t(n === 1 ? "sources.upload.receivedOne" : "sources.upload.receivedMany", { n }) + (dup ? ` ${t("sources.upload.duplicates", { n: dup })}` : ""));
      void qc.invalidateQueries({ queryKey: ["documents", wsId] });
      void qc.invalidateQueries({ queryKey: ["jobs", wsId] });
      setFiles([]);
      setOpts(EMPTY_OPTS);
      setMore(false);
      onClose();
    },
    onError: toast.err,
  });
  // chụp mảng ngay: FileList là "live", bị xoá trắng khi reset input.value trước khi updater của setFiles chạy
  const add = (list: FileList | null) => { const picked = Array.from(list || []); if (picked.length) setFiles((xs) => [...xs, ...picked]); };
  const pick = () => inputRef.current?.click();
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
  const set = (k: keyof UploadOpts) => (e: { target: { value: string } }) => setOpts((o) => ({ ...o, [k]: e.target.value }));
  const scopeAttr = ws.member_attributes[0];
  const submitLabel = files.length === 0 ? t("action.upload") : t(files.length === 1 ? "sources.upload.submitOne" : "sources.upload.submitMany", { n: files.length });

  return (
    <Modal open={open} onClose={onClose} width="max-w-2xl"
      title={replaceOf ? t("sources.upload.newVersionTitle") : t("sources.upload.title")}
      footer={<>
        <button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button>
        <button className="btn-primary" disabled={!files.length || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending && <Spinner className="text-white" />} {submitLabel}
        </button>
      </>}>
      {replaceOf && <p className="mb-4 text-sm leading-6 text-ink-700">{t("sources.upload.newVersionHint", { title: replaceOf.title })}</p>}

      <div role="button" tabIndex={0} aria-label={t("sources.upload.dropTitle")} onKeyDown={onKey} onClick={pick}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
        className={clsx("flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition",
          drag ? "border-brand-500 bg-brand-50" : "border-ink-100 bg-paper hover:border-brand-300 hover:bg-brand-50/50")}>
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-brand-600 shadow-card"><CloudUpload className="h-6 w-6" /></span>
        <div className="mt-3 font-medium text-ink-900">{t("sources.upload.dropTitle")}</div>
        <div className="mt-1 max-w-md text-sm leading-6 text-ink-600">{t("sources.upload.dropHint")}</div>
        <input ref={inputRef} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </div>

      {files.length > 0 && (
        <ul className="mt-4 max-h-56 divide-y divide-ink-100 overflow-y-auto rounded-xl border border-ink-100" aria-label={t("sources.upload.fileList")}>
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center gap-3 px-3 py-2">
              <SourceIcon type={typeFromName(f.name)} size="sm" />
              <span className="min-w-0 flex-1 truncate text-sm text-ink-900">{f.name}</span>
              <span className="shrink-0 text-xs text-ink-600">{fmtBytes(f.size)}</span>
              <button className="btn-ghost btn-sm" aria-label={t("sources.upload.remove", { name: f.name })} onClick={() => setFiles((xs) => xs.filter((_, j) => j !== i))}>
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4">
        <button type="button" className="btn-ghost -ml-2 px-2" aria-expanded={more} aria-controls={moreId} onClick={() => setMore((v) => !v)}>
          <ChevronDown className={clsx("h-4 w-4 transition", more && "rotate-180")} /> {t("sources.upload.moreOptions")}
        </button>
        {!more && <p className="mt-0.5 text-xs text-ink-600">{t("sources.upload.moreOptionsHint")}</p>}
        {more && (
          <div id={moreId} className="mt-3 grid gap-4 sm:grid-cols-2">
            <Field label={t("sources.field.docType")} hint={t("sources.field.docTypeHint")}>
              {docTypes.length ? (
                <select className="input" value={opts.doc_type} onChange={set("doc_type")}>
                  <option value="">{t("sources.field.autoDetect")}</option>
                  {docTypes.map((dt) => <option key={dt} value={dt}>{docTypeLabel(dt)}{advanced && docTypeLabel(dt) !== dt ? ` (${dt})` : ""}</option>)}
                </select>
              ) : <input className="input" value={opts.doc_type} onChange={set("doc_type")} placeholder={t("sources.field.autoDetect")} />}
            </Field>
            <Field label={t("sources.field.effectiveDate")} hint={t("sources.field.effectiveDateHint")}>
              <input className="input" type="date" value={opts.effective_date} onChange={set("effective_date")} />
            </Field>
            {files.length === 1 && (
              <Field label={t("sources.field.title")} hint={t("sources.field.titleHint")}>
                <input className="input" value={opts.title} onChange={set("title")} />
              </Field>
            )}
            <Field label={t("sources.field.version")} hint={t("sources.field.versionHint")}>
              <input className="input" value={opts.version} onChange={set("version")} placeholder={t("sources.field.versionPlaceholder")} />
            </Field>
            {scopeAttr && (
              <Field label={scopeAttr.label} hint={scopeAttr.description}>
                <input className="input" value={opts.scope} onChange={set("scope")} />
              </Field>
            )}
            <Field label={t("sources.field.replaceMode")} hint={t("sources.field.replaceModeHint")}>
              <select className="input" value={opts.replace_mode} onChange={set("replace_mode")}>
                <option value="document">{t("sources.field.replaceDocument")}</option>
                <option value="none">{t("sources.field.replaceNone")}</option>
              </select>
            </Field>
          </div>
        )}
      </div>
    </Modal>
  );
}
