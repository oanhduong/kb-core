// Chi tiết báo cáo dạng trình soạn tài liệu: cột đọc gọn ở giữa; block heading/markdown/chart/table/kpi/sources.
// Chế độ sửa: sửa chữ tại chỗ, nút điều khiển block (lên/xuống/xoá) hiện khi rê chuột hoặc focus, chèn mục/đoạn giữa các
// block; ⌘/Ctrl+S để lưu. Sinh lại từ mô tả gốc, menu xuất PDF/DOCX/XLSX/HTML, theo dõi tiến độ khi đang soạn.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowDown, ArrowLeft, ArrowUp, Check, ChevronDown, Download, FileCode2, FileSpreadsheet, FileText, FileType2, Heading2, Pencil,
  Pilcrow, RefreshCw, Trash2, X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { DataGrid } from "@/components/DataGrid";
import { Markdown } from "@/components/Markdown";
import { SourceDrawer } from "@/components/SourceDrawer";
import { Badge, Confirm, ErrorBox, Loading, Spinner, StatusBadge, useToast } from "@/components/ui";
import { ChartView, KpiValue } from "@/components/VegaChart";
import { useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api, download } from "@/lib/api";
import { fmtDate, fmtRelative } from "@/lib/format";
import type { Citation, Report, ReportBlock } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

const newId = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

export function ReportDetailPage() {
  const t = useT();
  const { wsId, can } = useWorkspace();
  const { reportId = "" } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const rep = useQuery({
    queryKey: ["report", wsId, reportId],
    queryFn: () => api.report(wsId, reportId),
    refetchInterval: (q) => (q.state.data?.status === "generating" ? 3000 : false),
  });
  const [editing, setEditing] = useState(false);
  const [blocks, setBlocks] = useState<ReportBlock[]>([]);
  const [title, setTitle] = useState("");
  const [cite, setCite] = useState<Citation | null>(null);
  const [regen, setRegen] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [discard, setDiscard] = useState(false);
  useEffect(() => { if (rep.data && !editing) { setBlocks(rep.data.blocks || []); setTitle(rep.data.title); setDirty(false); } }, [rep.data, editing]);

  const save = useMutation({
    mutationFn: () => api.updateReport(wsId, reportId, { title: title.trim() || undefined, blocks }),
    onSuccess: () => {
      toast.ok(t("reports.detail.saved"));
      setEditing(false);
      setDirty(false);
      void qc.invalidateQueries({ queryKey: ["report", wsId, reportId] });
      void qc.invalidateQueries({ queryKey: ["reports", wsId] });
    },
    onError: toast.err,
  });
  const regenMut = useMutation({
    mutationFn: () => api.regenerateReport(wsId, reportId),
    onSuccess: () => { setRegen(false); toast.ok(t("reports.detail.regenStarted")); void qc.invalidateQueries({ queryKey: ["report", wsId, reportId] }); },
    onError: toast.err,
  });

  // ⌘/Ctrl+S lưu khi đang sửa
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (!saveRef.current.isPending) saveRef.current.mutate(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing]);

  const edit = useCallback((fn: (xs: ReportBlock[]) => ReportBlock[]) => { setBlocks(fn); setDirty(true); }, []);

  if (rep.isLoading) return <Page><Loading /></Page>;
  if (rep.error || !rep.data) return <Page><ErrorBox error={rep.error || t("reports.detail.missing")} retry={() => rep.refetch()} /></Page>;
  const r: Report = rep.data;
  const generating = r.status === "generating";
  const editor = can("editor");

  const move = (i: number, d: -1 | 1) => edit((xs) => { const j = i + d; if (j < 0 || j >= xs.length) return xs; const ys = [...xs]; [ys[i], ys[j]] = [ys[j], ys[i]]; return ys; });
  const remove = (i: number) => edit((xs) => xs.filter((_, k) => k !== i));
  const update = (i: number, p: Partial<ReportBlock>) => edit((xs) => xs.map((b, k) => (k === i ? { ...b, ...p } : b)));
  /** Chèn block mới ngay sau vị trí i (i = -1: đầu báo cáo). */
  const insert = (i: number, type: "heading" | "markdown") => edit((xs) => {
    const ys = [...xs];
    ys.splice(i + 1, 0, type === "heading" ? { id: newId(), type, text: t("reports.block.newHeading"), level: 2 } : { id: newId(), type, text: "" });
    return ys;
  });
  const cancel = () => { setEditing(false); setDirty(false); setBlocks(r.blocks || []); setTitle(r.title); };
  const progress = r.job?.progress;

  return (
    <Page>
      <div className="mx-auto max-w-[860px]">
        {/* thanh công cụ */}
        <div className={clsx("mb-5 flex flex-wrap items-center gap-2", editing && "sticky top-0 z-30 -mx-4 border-b border-ink-100 bg-paper/90 px-4 py-3 backdrop-blur sm:-mx-8 sm:px-8")}>
          {editing ? (
            <>
              <span className="inline-flex items-center gap-2 text-sm font-medium text-ink-800">
                <Pencil className="h-4 w-4 text-brand-600" /> {t("reports.detail.editing")}
                {dirty && <span className="text-xs font-normal text-ink-600">{t("reports.detail.unsaved")}</span>}
              </span>
              <span className="flex-1" />
              <span className="hidden text-xs text-ink-400 sm:inline">{t("reports.detail.saveShortcut")}</span>
              <button className="btn-secondary" onClick={() => (dirty ? setDiscard(true) : cancel())}><X className="h-4 w-4" /> {t("action.cancel")}</button>
              <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>
                {save.isPending ? <Spinner className="text-white" /> : <Check className="h-4 w-4" />} {t("reports.detail.save")}
              </button>
            </>
          ) : (
            <>
              <Link to={`/w/${wsId}/reports`} className="btn-ghost -ml-2.5"><ArrowLeft className="h-4 w-4" /> {t("reports.title")}</Link>
              <span className="flex-1" />
              {editor && !generating && <button className="btn-secondary" onClick={() => setEditing(true)}><Pencil className="h-4 w-4" /> {t("reports.detail.edit")}</button>}
              {editor && r.prompt && !generating && <button className="btn-secondary" onClick={() => setRegen(true)}><RefreshCw className="h-4 w-4" /> {t("reports.detail.regenerate")}</button>}
              <ExportMenu disabled={generating} reportId={reportId} title={r.title} />
            </>
          )}
        </div>

        {generating && (
          <div className="panel mb-5 flex flex-wrap items-center gap-3 px-5 py-4 text-sm text-ink-800" role="status">
            <Spinner />
            <span className="flex-1">
              {progress?.total
                ? t("reports.progress.writing", { n: Math.min(progress.done + 1, progress.total), total: progress.total })
                : t("reports.progress.planning")}
            </span>
            {progress?.percent !== null && progress?.percent !== undefined && (
              <span className="h-1.5 w-40 overflow-hidden rounded-full bg-ink-100" aria-hidden>
                <span className="block h-full rounded-full bg-brand-500 transition-all" style={{ width: `${progress.percent}%` }} />
              </span>
            )}
          </div>
        )}
        {r.status === "error" && <div className="mb-5"><ErrorBox error={t("reports.detail.failed", { error: r.job?.error || t("reports.detail.failedUnknown") })} /></div>}

        <article className={clsx("panel px-6 py-9 sm:px-14 sm:py-12", editing && "ring-1 ring-brand-100")}>
          <header className="mb-8">
            {editing ? (
              <>
                <label htmlFor="report-title" className="sr-only">{t("reports.detail.titleLabel")}</label>
                <input id="report-title" value={title} onChange={(e) => { setTitle(e.target.value); setDirty(true); }}
                  className="-mx-2 w-[calc(100%+1rem)] rounded-lg border border-dashed border-ink-300 bg-transparent px-2 py-1 font-display text-[32px] font-semibold leading-tight text-ink-900 focus:border-brand-400 focus:outline-none focus:shadow-focus" />
              </>
            ) : (
              <h1 className="font-display text-[32px] font-semibold leading-tight tracking-tight text-ink-900">{r.title}</h1>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-600">
              {r.status !== "ready" && <StatusBadge status={r.status} />}
              <span>{t("reports.detail.updated", { when: fmtRelative(r.updated_at) })}</span>
              <span className="text-ink-300" aria-hidden>|</span>
              <span>{t("reports.detail.sections", { n: r.block_count })}</span>
            </div>
            {r.prompt && <Brief text={r.prompt} />}
          </header>

          {blocks.length === 0 && !generating && !editing && (
            <div className="rounded-xl border border-dashed border-ink-100 px-6 py-10 text-center">
              <div className="font-display text-lg font-semibold text-ink-900">{t("reports.detail.emptyTitle")}</div>
              <p className="mx-auto mt-1 max-w-md text-sm leading-6 text-ink-600">{t("reports.detail.emptyHint")}</p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                <Link to={`/w/${wsId}/chat`} className="btn-secondary">{t("reports.detail.goAsk")}</Link>
                {editor && <button className="btn-primary" onClick={() => setEditing(true)}><Pencil className="h-4 w-4" /> {t("reports.detail.startWriting")}</button>}
              </div>
            </div>
          )}

          {editing && <InsertBar onInsert={(type) => insert(-1, type)} always={blocks.length === 0} />}
          {blocks.map((b, i) => (
            <div key={b.id}>
              {editing ? (
                <div className="group relative -mx-3 rounded-xl px-3 py-1.5 transition-colors hover:bg-paper/80 focus-within:bg-paper/80">
                  <BlockControls index={i} count={blocks.length} onMove={(d) => move(i, d)} onRemove={() => remove(i)} />
                  <BlockView b={b} editing onChange={(p) => update(i, p)} onCite={() => undefined} />
                </div>
              ) : (
                <BlockView b={b} editing={false} onChange={() => undefined} onCite={(c) => c && setCite(c)} />
              )}
              {editing && <InsertBar onInsert={(type) => insert(i, type)} />}
            </div>
          ))}
        </article>
      </div>

      <SourceDrawer citation={cite} onClose={() => setCite(null)} />
      <Confirm open={regen} onClose={() => setRegen(false)} onConfirm={() => regenMut.mutate()} busy={regenMut.isPending}
        title={t("reports.detail.regenTitle")} message={t("reports.detail.regenMessage")} confirmLabel={t("reports.detail.regenConfirm")} />
      <Confirm open={discard} onClose={() => setDiscard(false)} onConfirm={() => { setDiscard(false); cancel(); }} danger
        title={t("reports.detail.discardTitle")} message={t("reports.detail.discardMessage")} confirmLabel={t("reports.detail.discardConfirm")} />
    </Page>
  );
}

function Brief({ text }: { text: string }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const long = text.length > 220;
  return (
    <div className="mt-4 border-l-2 border-brand-200 pl-3 text-sm leading-6 text-ink-600">
      <span className="font-medium text-ink-700">{t("reports.detail.brief")} </span>
      {long && !open ? `${text.slice(0, 220)}…` : text}
      {long && <button type="button" className="link ml-1.5 text-sm" onClick={() => setOpen((v) => !v)}>{t(open ? "action.showLess" : "action.showMore")}</button>}
    </div>
  );
}

// ---------------------------------------------------------------- điều khiển khi sửa

function BlockControls({ index, count, onMove, onRemove }: { index: number; count: number; onMove: (d: -1 | 1) => void; onRemove: () => void }) {
  const t = useT();
  return (
    <div className={clsx("absolute -top-3 right-2 z-10 flex items-center gap-0.5 rounded-lg border border-ink-100 bg-white p-0.5 shadow-card transition",
      "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100")}>
      <button type="button" className="btn-ghost btn-sm px-1.5" onClick={() => onMove(-1)} disabled={index === 0} aria-label={t("reports.block.moveUp")} title={t("reports.block.moveUp")}><ArrowUp className="h-3.5 w-3.5" /></button>
      <button type="button" className="btn-ghost btn-sm px-1.5" onClick={() => onMove(1)} disabled={index === count - 1} aria-label={t("reports.block.moveDown")} title={t("reports.block.moveDown")}><ArrowDown className="h-3.5 w-3.5" /></button>
      <span className="mx-0.5 h-4 w-px bg-ink-100" aria-hidden />
      <button type="button" className="btn-ghost btn-sm px-1.5 text-alert-700 hover:bg-alert-50 hover:text-alert-700" onClick={onRemove} aria-label={t("reports.block.remove")} title={t("reports.block.remove")}><Trash2 className="h-3.5 w-3.5" /></button>
    </div>
  );
}

function InsertBar({ onInsert, always }: { onInsert: (type: "heading" | "markdown") => void; always?: boolean }) {
  const t = useT();
  return (
    <div className={clsx("group/ins relative flex h-7 items-center justify-center", always && "h-auto py-4")}>
      <span className={clsx("absolute inset-x-0 top-1/2 h-px bg-brand-200 transition", always ? "opacity-0" : "opacity-0 group-hover/ins:opacity-100 group-focus-within/ins:opacity-100")} aria-hidden />
      <div className={clsx("relative flex gap-1 bg-white px-2 transition",
        always ? "opacity-100" : "opacity-0 group-hover/ins:opacity-100 group-focus-within/ins:opacity-100 [@media(hover:none)]:opacity-100")}>
        <button type="button" className="btn-secondary btn-sm" onClick={() => onInsert("heading")}><Heading2 className="h-3.5 w-3.5" /> {t("reports.block.addHeading")}</button>
        <button type="button" className="btn-secondary btn-sm" onClick={() => onInsert("markdown")}><Pilcrow className="h-3.5 w-3.5" /> {t("reports.block.addParagraph")}</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- menu xuất file

const FORMATS: { fmt: string; key: string; icon: ReactNode }[] = [
  { fmt: "pdf", key: "reports.export.pdf", icon: <FileType2 className="h-4 w-4 text-alert-700" /> },
  { fmt: "docx", key: "reports.export.docx", icon: <FileText className="h-4 w-4 text-brand-700" /> },
  { fmt: "xlsx", key: "reports.export.xlsx", icon: <FileSpreadsheet className="h-4 w-4 text-verify-700" /> },
  { fmt: "html", key: "reports.export.html", icon: <FileCode2 className="h-4 w-4 text-ink-600" /> },
];

function ExportMenu({ disabled, reportId, title }: { disabled?: boolean; reportId: string; title: string }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btnRef.current?.focus(); } };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [open]);
  const exp = async (fmt: string) => {
    setOpen(false);
    setBusy(fmt);
    try {
      await download(api.exportReportPath(wsId, reportId, fmt), `${title || "report"}.${fmt}`);
    } catch (e) {
      toast.err(e);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="relative" ref={ref}>
      <button ref={btnRef} type="button" className="btn-ink" disabled={disabled || !!busy} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {busy ? <Spinner className="text-white" /> : <Download className="h-4 w-4" />} {t("reports.export.button")} <ChevronDown className="h-3.5 w-3.5 opacity-70" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-40 mt-2 w-64 animate-rise rounded-xl border border-ink-100 bg-white p-1.5 shadow-lift">
          {FORMATS.map((f) => (
            <button key={f.fmt} role="menuitem" type="button" onClick={() => exp(f.fmt)}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-ink-800 hover:bg-paper focus-visible:bg-paper">
              {f.icon}
              <span className="flex-1">{t(f.key)}</span>
              <span className="text-xs text-ink-400">.{f.fmt}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- block

const editInput = "w-full rounded-lg border border-dashed border-ink-300 bg-white/70 px-2 py-1 focus:border-brand-400 focus:bg-white focus:outline-none focus:shadow-focus";

function BlockView({ b, editing, onChange, onCite }: { b: ReportBlock; editing: boolean; onChange: (p: Partial<ReportBlock>) => void; onCite: (c: Citation | null, n: number) => void }) {
  const t = useT();
  switch (b.type) {
    case "heading": {
      const main = (b.level ?? 2) <= 1;
      if (editing) {
        return (
          <input aria-label={t("reports.block.headingLabel")} value={b.text || ""} onChange={(e) => onChange({ text: e.target.value })}
            className={clsx(editInput, "-mx-2 w-[calc(100%+1rem)] font-display font-semibold text-ink-900", main ? "mt-4 text-2xl" : "mt-3 text-xl")} />
        );
      }
      return main
        ? <h2 className="mb-3 mt-10 font-display text-2xl font-semibold tracking-tight text-ink-900 first:mt-0">{b.text}</h2>
        : <h3 className="mb-2 mt-8 font-display text-xl font-semibold text-ink-900 first:mt-0">{b.text}</h3>;
    }
    case "markdown":
      if (editing) {
        return (
          <textarea aria-label={t("reports.block.paragraphLabel")} placeholder={t("reports.block.paragraphPlaceholder")}
            rows={Math.min(24, Math.max(3, (b.text || "").split("\n").length + 1))} value={b.text || ""} onChange={(e) => onChange({ text: e.target.value })}
            className={clsx(editInput, "-mx-2 w-[calc(100%+1rem)] resize-y text-[15px] leading-7 text-ink-900")} />
        );
      }
      return (
        <div className="my-3">
          {b.question && <div className="mb-1 text-xs text-ink-400">{t("reports.block.question", { q: b.question })}</div>}
          <Markdown text={b.text || ""} citations={b.citations} onCite={onCite} />
          {b.citations && b.citations.length > 0 && (
            <ol className="mt-2 space-y-0.5 text-xs text-ink-600">
              {b.citations.map((c) => (
                <li key={c.n}>
                  <button type="button" className="inline-flex items-center gap-1.5 text-left hover:text-ink-900" onClick={() => onCite(c, c.n)}>
                    <span className="cite-chip mx-0">{c.n}</span><span className="hover:underline">{c.label}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </div>
      );
    case "chart":
      return (
        <figure className="my-6">
          {editing && <input aria-label={t("reports.block.chartTitle")} className={clsx(editInput, "mb-2 text-sm")} value={b.title || ""} onChange={(e) => onChange({ title: e.target.value })} placeholder={t("reports.block.chartTitle")} />}
          <ChartView chart={b.chart} height={300} />
          {!editing && (b.title || b.citation) && (
            <figcaption className="mt-2 text-center text-xs leading-5 text-ink-600">
              {b.title}{b.title && b.citation ? <span className="text-ink-400">, </span> : null}{b.citation && <span className="text-ink-400">{b.citation}</span>}
            </figcaption>
          )}
        </figure>
      );
    case "kpi":
      return <div className="my-4"><KpiValue label={b.title || b.kpi?.label} value={b.kpi?.value} /></div>;
    case "table":
      return (
        <div className="my-6">
          {editing
            ? <input aria-label={t("reports.block.tableTitle")} className={clsx(editInput, "mb-2 text-sm")} value={b.title || ""} onChange={(e) => onChange({ title: e.target.value })} placeholder={t("reports.block.tableTitle")} />
            : b.title && <h4 className="mb-2 text-sm font-semibold text-ink-900">{b.title}</h4>}
          <DataGrid columns={b.columns || []} rows={b.rows || []} maxHeight={360} compact />
          {b.citation && <div className="mt-1.5 text-xs text-ink-400">{b.citation}</div>}
        </div>
      );
    case "sources":
      if (!b.items?.length) return null;
      return (
        <section className="mt-12 border-t border-ink-100 pt-6">
          <h2 className="mb-3 font-display text-base font-semibold text-ink-900">{t("reports.block.sources")}</h2>
          <ol className="list-decimal space-y-1 pl-5 text-sm leading-6 text-ink-700 marker:text-ink-400">
            {b.items.map((it, i) => (
              <li key={i}>{it.label}{it.effective_date ? <span className="text-ink-400"> {t("reports.block.effective", { date: fmtDate(it.effective_date) })}</span> : null}</li>
            ))}
          </ol>
        </section>
      );
    default:
      return <Badge>{t("reports.block.unknown", { type: b.type })}</Badge>;
  }
}
