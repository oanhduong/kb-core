// Danh sách mô hình dữ liệu + tạo mới (chọn file Excel/CSV đã có hoặc tải file mới).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowRight, Check, Database, FileSpreadsheet, GitMerge, Sheet, Upload, Workflow } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorBox, Field, Loading, MatchMeter, Modal, PageHeader, SourceIcon, Spinner, StatusBadge, useToast } from "@/components/ui";
import { useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtBytes, fmtRelative } from "@/lib/format";
import type { DataModel } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

export function FlowStrip({ compact }: { compact?: boolean }) {
  const t = useT();
  const steps = [
    { icon: FileSpreadsheet, title: t("models.flow.sheets"), hint: t("models.flow.sheetsHint") },
    { icon: Database, title: t("models.flow.tables"), hint: t("models.flow.tablesHint") },
    { icon: GitMerge, title: t("models.flow.logic"), hint: t("models.flow.logicHint") },
    { icon: Workflow, title: t("models.flow.view"), hint: t("models.flow.viewHint") },
  ];
  return (
    <ol className={clsx("grid gap-3", compact ? "sm:grid-cols-4" : "md:grid-cols-4")}>
      {steps.map((s, i) => (
        <li key={i} className="relative flex gap-3 rounded-xl bg-white/70 p-3.5 ring-1 ring-ink-100">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-ink-900 font-display text-sm font-semibold text-marker-200">{i + 1}</span>
          <span className="min-w-0">
            <span className="flex items-center gap-1.5 text-sm font-semibold text-ink-900"><s.icon className="h-4 w-4 text-ink-400" />{s.title}</span>
            {!compact && <span className="mt-0.5 block text-xs leading-5 text-ink-600">{s.hint}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function ModelsPage() {
  const { wsId, can } = useWorkspace();
  const t = useT();
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["models", wsId], queryFn: () => api.models(wsId),
    refetchInterval: (query) => (query.state.data?.some((m) => m.status === "analyzing" || m.status === "building") ? 3000 : false),
  });
  return (
    <Page>
      <PageHeader title={t("models.title")} subtitle={t("models.subtitle")}
        actions={can("editor") && <button className="btn-primary" onClick={() => setOpen(true)}><Workflow className="h-4 w-4" /> {t("models.new")}</button>} />
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {q.data && q.data.length === 0 && (
        <div className="panel overflow-hidden">
          <div className="p-6 pb-2"><FlowStrip /></div>
          <Empty icon={<Workflow />} title={t("models.empty.title")} hint={t("models.empty.hint")}
            action={can("editor") && <button className="btn-primary" onClick={() => setOpen(true)}><Upload className="h-4 w-4" /> {t("models.empty.cta")}</button>} />
        </div>
      )}
      {q.data && q.data.length > 0 && (
        <>
          <div className="mb-6"><FlowStrip compact /></div>
          <ul className="panel divide-y divide-ink-100 overflow-hidden">
            {q.data.map((m) => <ModelRow key={m.id} m={m} />)}
          </ul>
        </>
      )}
      <CreateModelModal open={open} onClose={() => setOpen(false)} />
    </Page>
  );
}

function ModelRow({ m }: { m: DataModel }) {
  const t = useT();
  const c = m.counts;
  const v = m.validation_summary;
  return (
    <li>
      <Link to={m.id} className="group flex flex-wrap items-center gap-4 px-5 py-4 transition hover:bg-paper">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-ink-900 text-marker-200"><Workflow className="h-5 w-5" /></span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="truncate font-display text-[17px] font-semibold text-ink-900">{m.name}</span>
            <StatusBadge status={m.stale ? "draft" : m.status} />
          </span>
          <span className="mt-0.5 block text-sm text-ink-600">
            {m.main_title ? t("models.row.main", { sheet: m.main_title, n: c?.sources ?? 0 }) : t("models.row.reading")}
          </span>
        </span>
        {c && (
          <span className="hidden w-44 sm:block">
            <span className="mb-1 block text-xs text-ink-600">{t("models.row.reviewed", { done: c.confirmed, total: c.columns })}</span>
            <span className="block h-1.5 overflow-hidden rounded-full bg-ink-100"><span className="block h-full rounded-full bg-ink-900" style={{ width: `${c.columns ? (100 * c.confirmed) / c.columns : 0}%` }} /></span>
          </span>
        )}
        <span className="hidden w-36 md:block">{v?.score !== undefined && v?.score !== null ? <MatchMeter rate={v.score} label={t("models.row.matchesExcel")} /> : <span className="text-xs text-ink-400">{t("models.row.notBuilt")}</span>}</span>
        <span className="w-24 text-right text-xs text-ink-600">{fmtRelative(m.updated_at)}</span>
        <ArrowRight className="h-4 w-4 text-ink-300 transition group-hover:translate-x-0.5 group-hover:text-ink-700" />
      </Link>
    </li>
  );
}

export function CreateModelModal({ open, onClose, preselect }: { open: boolean; onClose: () => void; preselect?: string[] }) {
  const { wsId } = useWorkspace();
  const t = useT();
  const toast = useToast();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [mode, setMode] = useState<"upload" | "existing">(preselect?.length ? "existing" : "upload");
  const [files, setFiles] = useState<File[]>([]);
  const [picked, setPicked] = useState<string[]>(preselect || []);
  const [name, setName] = useState("");
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const docs = useQuery({ queryKey: ["documents", wsId, "sheets"], queryFn: () => api.documents(wsId, { page_size: 200 }), enabled: open });
  const sheets = useMemo(() => (docs.data?.items || []).filter((d) => ["xlsx", "csv"].includes(d.source_type) && !d.deleted_at), [docs.data]);
  const mut = useMutation({
    mutationFn: async () => {
      let ids = picked;
      if (mode === "upload") {
        const up = await api.upload(wsId, files, {});
        ids = up.jobs.map((j) => j.document_id);
      }
      return api.createModel(wsId, { document_ids: ids, name: name.trim() || undefined });
    },
    onSuccess: (m) => {
      void qc.invalidateQueries({ queryKey: ["models", wsId] });
      void qc.invalidateQueries({ queryKey: ["documents", wsId] });
      toast.ok(t("models.create.started"));
      onClose();
      nav(`/w/${wsId}/models/${m.id}`);
    },
    onError: toast.err,
  });
  const ready = mode === "upload" ? files.length > 0 : picked.length > 0;
  return (
    <Modal open={open} onClose={onClose} title={t("models.create.title")} width="max-w-2xl"
      footer={<><button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button>
        <button className="btn-primary" disabled={!ready || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />} {t("models.create.submit")}</button></>}>
      <p className="mb-4 text-sm leading-6 text-ink-700">{t("models.create.intro")}</p>
      <div className="seg mb-4 w-fit">
        <button aria-pressed={mode === "upload"} onClick={() => setMode("upload")}>{t("models.create.upload")}</button>
        <button aria-pressed={mode === "existing"} onClick={() => setMode("existing")}>{t("models.create.existing", { n: sheets.length })}</button>
      </div>
      {mode === "upload" ? (
        <>
          <button type="button" className={clsx("flex w-full flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition",
            drag ? "border-brand-500 bg-brand-50" : "border-ink-100 bg-paper hover:border-ink-300")}
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); setFiles((x) => [...x, ...Array.from(e.dataTransfer.files)]); }}
            onClick={() => input.current?.click()}>
            <Sheet className="h-8 w-8 text-ink-400" />
            <span className="mt-3 font-medium text-ink-900">{t("models.create.drop")}</span>
            <span className="mt-1 text-xs text-ink-600">{t("models.create.dropHint")}</span>
          </button>
          <input ref={input} type="file" multiple accept=".xlsx,.xlsm,.csv" className="hidden" onChange={(e) => { setFiles((x) => [...x, ...Array.from(e.target.files || [])]); e.target.value = ""; }} />
          {files.length > 0 && (
            <ul className="mt-3 divide-y divide-ink-100 rounded-xl border border-ink-100">
              {files.map((f, i) => (
                <li key={i} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <SourceIcon type={f.name.endsWith(".csv") ? "csv" : "xlsx"} size="sm" />
                  <span className="flex-1 truncate">{f.name}</span><span className="text-xs text-ink-600">{fmtBytes(f.size)}</span>
                  <button className="btn-ghost btn-sm" onClick={() => setFiles((x) => x.filter((_, j) => j !== i))}>{t("action.delete")}</button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <div className="max-h-72 overflow-y-auto rounded-xl border border-ink-100">
          {docs.isLoading && <Loading />}
          {sheets.length === 0 && !docs.isLoading && <div className="p-6 text-center text-sm text-ink-600">{t("models.create.noSheets")}</div>}
          {sheets.map((d) => {
            const on = picked.includes(d.document_id);
            return (
              <button key={d.document_id} className={clsx("flex w-full items-center gap-3 border-b border-ink-100 px-3 py-2.5 text-left text-sm last:border-0", on ? "bg-brand-50/60" : "hover:bg-paper")}
                onClick={() => setPicked((x) => (on ? x.filter((y) => y !== d.document_id) : [...x, d.document_id]))}>
                <span className={clsx("flex h-5 w-5 items-center justify-center rounded-md border", on ? "border-brand-600 bg-brand-600 text-white" : "border-ink-300")}>{on && <Check className="h-3.5 w-3.5" />}</span>
                <SourceIcon type={d.source_type} size="sm" />
                <span className="min-w-0 flex-1"><span className="block truncate font-medium text-ink-900">{d.title}</span><span className="block truncate text-xs text-ink-600">{d.filename}</span></span>
                <span className="text-xs text-ink-600">{fmtRelative(d.updated_at)}</span>
              </button>
            );
          })}
        </div>
      )}
      <div className="mt-4"><Field label={t("models.create.name")} hint={t("models.create.nameHint")}><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("models.create.namePh")} /></Field></div>
    </Modal>
  );
}
