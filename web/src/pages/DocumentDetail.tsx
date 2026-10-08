// Chi tiết 1 nguồn: "Những gì chúng tôi tìm thấy" (tóm tắt, chủ đề, bảng, đối tượng, đoạn nội dung, thời lượng, lưu ý)
// + các tab chi tiết + hành động vòng đời. Việc xử lý nền (job) chỉ hiện khi bật "Chi tiết kỹ thuật".
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowLeft, ArrowRight, Check, Clock, History, Info, MessageSquare, Network, Pencil, RefreshCw, RotateCcw, ShieldAlert, Table2,
  TextQuote, Trash2, TriangleAlert, Upload,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DocType, docTypeLabel, entityTypeLabel, fmtDuration } from "@/components/SourceDrawer";
import {
  Badge, Confirm, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Pagination, SourceIcon, Spinner, StatusBadge, Tabs, useToast,
} from "@/components/ui";
import { translate, useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { docStatus, fmtBytes, fmtDate, fmtNumber, fmtRelative, sourceLabel } from "@/lib/format";
import type { DocumentDetail } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { Advanced, usePrefs } from "@/state/prefs";
import { JobProgress, UploadModal } from "./Documents";

type Tab = "overview" | "tables" | "entities" | "records" | "jobs";
type Action = "approve" | "reprocess" | "delete" | "restore" | "reindex";

/** Dịch nếu có key, không thì trả null (giá trị lạ từ API). */
function tMaybe(key: string): string | null {
  const out = translate(key);
  return out === key ? null : out;
}

function durationOf(d: DocumentDetail): number | null {
  if (d.source_type !== "audio" && d.source_type !== "video") return null;
  const s = d.semantic_profile?.stats || {};
  const v = s.duration ?? s.duration_sec;
  return typeof v === "number" && v > 0 ? v : null;
}

export function DocumentDetailPage() {
  const t = useT();
  const { advanced } = usePrefs();
  const { wsId, ws, can } = useWorkspace();
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
    mutationFn: async (a: Action) => {
      if (a === "approve") return api.approveDocument(wsId, documentId);
      if (a === "reprocess") return api.reprocessDocument(wsId, documentId);
      if (a === "delete") return api.deleteDocument(wsId, documentId);
      if (a === "restore") return api.restoreDocument(wsId, documentId);
      return api.reindex(wsId, { document_id: documentId });
    },
    onSuccess: (_r, a) => { toast.ok(t(`sources.detail.done.${a}`)); setDel(false); invalidate(); },
    onError: toast.err,
  });

  if (doc.isLoading) return <Page><Loading /></Page>;
  if (doc.error || !doc.data) return <Page><ErrorBox error={doc.error || t("sources.detail.notFound")} retry={() => doc.refetch()} /></Page>;
  const d = doc.data;
  const running = d.jobs.find((j) => j.status === "queued" || j.status === "processing");
  const failedJob = d.jobs.find((j) => j.status === "blocked" || j.status === "error");
  const editor = can("editor");
  const activeTab: Tab = tab === "jobs" && !advanced ? "overview" : tab;
  const scopeLabel = ws.member_attributes[0]?.label || t("sources.detail.scope");

  const tabs: { key: Tab; label: ReactNode; count?: number }[] = [
    { key: "overview", label: t("sources.tab.overview") },
    { key: "tables", label: t("sources.tab.tables"), count: d.tables.length },
    { key: "entities", label: t("sources.tab.entities"), count: d.entities.length },
    { key: "records", label: t("sources.tab.passages"), count: d.record_count_live },
  ];
  if (advanced) tabs.push({ key: "jobs", label: t("sources.tab.jobs"), count: d.jobs.length });

  return (
    <Page wide>
      <Link to={`/w/${wsId}/documents`} className="mb-4 inline-flex items-center gap-1.5 rounded-lg text-sm text-ink-600 hover:text-ink-900">
        <ArrowLeft className="h-4 w-4" /> {t("sources.title")}
      </Link>
      <PageHeader
        title={<span className="flex flex-wrap items-center gap-3"><SourceIcon type={d.source_type} size="lg" /><span className="min-w-0 break-words">{d.title}</span><StatusBadge status={d.deleted_at ? "deleted" : docStatus(d.status)} /></span>}
        subtitle={<span className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          <span>{t("sources.fileAndSize", { name: d.filename, size: fmtBytes(d.size_bytes) })}</span>
          <span className="font-medium text-ink-700"><DocType code={d.doc_type} /></span>
          {d.effective_date && <span>{d.valid_to ? t("sources.inEffectRange", { from: fmtDate(d.effective_date), to: fmtDate(d.valid_to) }) : t("sources.inEffectFrom", { date: fmtDate(d.effective_date) })}</span>}
          {d.version && <span>{t("sources.versionN", { v: d.version })}</span>}
          {d.scope && <span>{scopeLabel}: {d.scope}</span>}
          <span>{t("sources.updatedAgo", { when: fmtRelative(d.updated_at) })}</span>
        </span>}
        actions={<>
          <button className="btn-primary" onClick={() => nav(`/w/${wsId}/chat?q=${encodeURIComponent(t("sources.detail.askPrompt", { title: d.title }))}`)}>
            <MessageSquare className="h-4 w-4" /> {t("sources.detail.ask")}
          </button>
          {editor && !d.deleted_at && <>
            <button className="btn-secondary" onClick={() => setEdit(true)}><Pencil className="h-4 w-4" /> {t("action.edit")}</button>
            <button className="btn-secondary" onClick={() => setReplace(true)}><Upload className="h-4 w-4" /> {t("sources.detail.newVersion")}</button>
            <button className="btn-secondary" disabled={!!running || act.isPending} onClick={() => act.mutate("reprocess")} title={t("sources.detail.reprocessHint")}>
              <RefreshCw className="h-4 w-4" /> {t("sources.detail.reprocess")}
            </button>
            <button className="btn-danger" onClick={() => setDel(true)} aria-label={t("sources.detail.delete")} title={t("sources.detail.delete")}><Trash2 className="h-4 w-4" /></button>
          </>}
          {editor && d.deleted_at && <button className="btn-secondary" disabled={act.isPending} onClick={() => act.mutate("restore")}><RotateCcw className="h-4 w-4" /> {t("sources.detail.restore")}</button>}
        </>} />

      <div className="mb-6 space-y-3 empty:hidden">
        {running && (
          <Notice tone="brand" icon={<Spinner />} title={t("sources.detail.processingTitle")}>
            <p>{t("sources.detail.processingHint")}</p>
            <JobProgress job={running} />
          </Notice>
        )}
        {!running && (d.status === "blocked" || d.status === "error") && (
          <Notice tone="alert" icon={<TriangleAlert className="h-[18px] w-[18px]" />} title={t("sources.detail.failedTitle")}
            action={editor && !d.deleted_at && <button className="btn-secondary btn-sm" disabled={act.isPending} onClick={() => act.mutate("reprocess")}><RefreshCw className="h-3.5 w-3.5" /> {t("sources.detail.reprocess")}</button>}>
            {failedJob?.error && <p className="break-words">{failedJob.error}</p>}
            <p>{t("sources.detail.failedHint")}</p>
          </Notice>
        )}
        {d.status === "pending_review" && !d.deleted_at && (
          <Notice tone="ink" icon={<Info className="h-[18px] w-[18px]" />} title={t("sources.detail.reviewTitle")}
            action={editor && <button className="btn-ink btn-sm" disabled={act.isPending} onClick={() => act.mutate("approve")}><Check className="h-3.5 w-3.5" /> {t("sources.detail.approve")}</button>}>
            <p>{t("sources.detail.reviewHint")}</p>
          </Notice>
        )}
        {d.superseded_by_id && (
          <Notice tone="ink" icon={<History className="h-[18px] w-[18px]" />} title={t("sources.detail.supersededTitle")}
            action={<Link className="btn-secondary btn-sm" to={`/w/${wsId}/documents/${d.superseded_by_id}`}>{t("sources.detail.openNewer")}</Link>}>
            <p>{t("sources.detail.supersededHint")}</p>
          </Notice>
        )}
        {d.deleted_at && (
          <Notice tone="ink" icon={<Trash2 className="h-[18px] w-[18px]" />} title={t("sources.detail.deletedTitle")}>
            <p>{t("sources.detail.deletedHint")}</p>
          </Notice>
        )}
      </div>

      <FoundStrip d={d} onTab={setTab} />

      <div className="mt-8">
        <Tabs value={activeTab} onChange={setTab} tabs={tabs} />
        <div className="mt-5">
          {activeTab === "overview" && <OverviewTab d={d} />}
          {activeTab === "tables" && <TablesTab d={d} />}
          {activeTab === "entities" && <EntitiesTab d={d} busy={!!running} />}
          {activeTab === "records" && <RecordsTab documentId={documentId} />}
          {activeTab === "jobs" && <JobsTab d={d} />}
        </div>
      </div>

      {edit && <EditModal onClose={() => setEdit(false)} d={d} onSaved={invalidate} />}
      <UploadModal open={replace} onClose={() => setReplace(false)} replaceOf={{ series_key: d.series_key, title: d.title }} />
      <Confirm open={del} onClose={() => setDel(false)} onConfirm={() => act.mutate("delete")} danger busy={act.isPending}
        title={t("sources.detail.deleteTitle")} message={t("sources.detail.deleteMessage")} confirmLabel={t("sources.detail.delete")} />
    </Page>
  );
}

const NOTICE_TONE = {
  brand: "border-brand-100 bg-brand-50/70",
  alert: "border-alert-500/25 bg-alert-50",
  ink: "border-ink-100 bg-white",
};

function Notice({ tone, icon, title, children, action }: { tone: keyof typeof NOTICE_TONE; icon: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={clsx("flex flex-col gap-3 rounded-xl border px-4 py-3.5 sm:flex-row sm:items-start", NOTICE_TONE[tone])} role="status">
      <span className={clsx("mt-0.5 shrink-0", tone === "alert" ? "text-alert-700" : "text-ink-600")}>{icon}</span>
      <div className="min-w-0 flex-1 space-y-1 text-sm leading-6 text-ink-700">
        <div className={clsx("font-medium", tone === "alert" ? "text-alert-700" : "text-ink-900")}>{title}</div>
        {children}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ what we found

function FoundItem({ icon, value, label, children, onMore, moreLabel }: { icon: ReactNode; value: ReactNode; label: string; children?: ReactNode; onMore?: () => void; moreLabel?: string }) {
  return (
    <li className="flex flex-col gap-2 bg-white p-4 sm:p-5">
      <div className="flex items-center gap-2 text-ink-600 [&>svg]:h-[18px] [&>svg]:w-[18px] [&>svg]:text-ink-400">{icon}<span className="text-sm">{label}</span></div>
      <div className="font-display text-2xl font-semibold tracking-tight text-ink-900">{value}</div>
      {children}
      {onMore && moreLabel && (
        <button className="link mt-auto inline-flex items-center gap-1 self-start rounded text-sm" onClick={onMore}>{moreLabel} <ArrowRight className="h-3.5 w-3.5" /></button>
      )}
    </li>
  );
}

const SENSITIVITY: Record<string, { tone: "slate" | "red"; icon: boolean }> = {
  none: { tone: "slate", icon: false }, internal: { tone: "slate", icon: true }, confidential: { tone: "red", icon: true }, personal: { tone: "red", icon: true },
};

function FoundStrip({ d, onTab }: { d: DocumentDetail; onTab: (t: Tab) => void }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const p = d.semantic_profile || {};
  const topics = p.topics || [];
  const duration = durationOf(d);
  const topEntities = [...d.entities].sort((a, b) => b.mentions - a.mentions).slice(0, 5);
  const sens = p.sensitivity && p.sensitivity !== "none" ? p.sensitivity : null;
  const sensMeta = sens ? SENSITIVITY[sens] || { tone: "slate" as const, icon: true } : null;

  return (
    <section className="panel p-5 sm:p-7" aria-labelledby="found-title">
      <h2 id="found-title" className="font-display text-xl font-semibold text-ink-900">{t("sources.found.title")}</h2>
      <div className="mt-3 max-w-3xl">
        {d.summary
          ? <p className="whitespace-pre-wrap text-[15px] leading-7 text-ink-800">{d.summary}</p>
          : <p className="text-sm leading-6 text-ink-600">{t("sources.found.noSummary")}</p>}
        {topics.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-sm text-ink-600">{t("sources.found.topics")}</span>
            {topics.map((tp) => <Badge key={tp} tone="brand">{tp}</Badge>)}
          </div>
        )}
      </div>

      <ul className={clsx("mt-6 grid gap-px overflow-hidden rounded-xl border border-ink-100 bg-ink-100 sm:grid-cols-2", duration ? "lg:grid-cols-4" : "lg:grid-cols-3")}>
        <FoundItem icon={<Table2 />} value={fmtNumber(d.tables.length)} label={t("sources.found.tables")}
          onMore={d.tables.length > 3 ? () => onTab("tables") : undefined} moreLabel={t("sources.found.seeAll")}>
          {d.tables.length === 0
            ? <p className="text-sm text-ink-600">{t("sources.found.noTables")}</p>
            : <ul className="space-y-1">{d.tables.slice(0, 3).map((tb) => (
              <li key={tb.id} className="truncate text-sm"><Link className="link" to={`/w/${wsId}/tables/${tb.id}`}>{tb.title}</Link></li>
            ))}</ul>}
        </FoundItem>
        <FoundItem icon={<Network />} value={fmtNumber(d.entities.length)} label={t("sources.found.entities")}
          onMore={d.entities.length > 0 ? () => onTab("entities") : undefined} moreLabel={t("sources.found.seeAll")}>
          {d.entities.length === 0
            ? <p className="text-sm text-ink-600">{t("sources.found.noEntities")}</p>
            : <div className="flex flex-wrap gap-1.5">{topEntities.map((e) => (
              <Link key={e.id} to={`/w/${wsId}/graph/${e.id}`} className="chip bg-ink-100/70 text-ink-800 hover:bg-brand-50 hover:text-brand-700">{e.name}</Link>
            ))}</div>}
        </FoundItem>
        <FoundItem icon={<TextQuote />} value={fmtNumber(d.record_count_live)} label={t("sources.found.passages")}
          onMore={d.record_count_live > 0 ? () => onTab("records") : undefined} moreLabel={t("sources.found.readPassages")}>
          <p className="text-sm leading-6 text-ink-600">{t("sources.found.passagesHint")}</p>
        </FoundItem>
        {duration && (
          <FoundItem icon={<Clock />} value={fmtDuration(duration)} label={t("sources.found.duration")}>
            <p className="text-sm leading-6 text-ink-600">{t("sources.found.durationHint")}</p>
          </FoundItem>
        )}
      </ul>

      {(p.quality_notes || sensMeta) && (
        <div className="mt-5 space-y-3">
          {sens && sensMeta && (
            <div className="flex flex-wrap items-start gap-3 text-sm leading-6 text-ink-700">
              <Badge tone={sensMeta.tone}>{sensMeta.icon && <ShieldAlert className="h-3 w-3" />}{tMaybe(`sources.sensitivity.${sens}`) || sens}</Badge>
              <span className="min-w-0 flex-1">{tMaybe(`sources.sensitivity.${sens}.hint`)}</span>
            </div>
          )}
          {p.quality_notes && (
            <div className="well flex items-start gap-3 p-4 text-sm leading-6 text-ink-700">
              <Info className="mt-1 h-4 w-4 shrink-0 text-ink-400" />
              <div><span className="font-medium text-ink-900">{t("sources.found.qualityNote")}</span> {p.quality_notes}</div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ tabs

const STAT_KEYS = ["pages", "sheets", "rows", "table_rows", "headings", "sections", "articles", "segments", "words"];
const STAT_HIDDEN = new Set(["records", "tables", "duration", "duration_sec"]);

function OverviewTab({ d }: { d: DocumentDetail }) {
  const t = useT();
  const { advanced } = usePrefs();
  const { wsId } = useWorkspace();
  const nav = useNavigate();
  const p = d.semantic_profile || {};
  const sq = p.suggested_questions || [];
  const stats = p.stats || {};
  const keyEntities = p.key_entities || [];
  const sens = p.sensitivity;
  const kind = p.kind ? (p.generated_by === "stats" ? docTypeLabel(p.kind) : p.kind) : null;
  const tr = p.time_range;
  const covers = tr && (tr.from || tr.to)
    ? tr.from && tr.to ? t("sources.about.range", { from: fmtDate(tr.from), to: fmtDate(tr.to) }) : tr.from ? t("sources.about.since", { date: fmtDate(tr.from) }) : t("sources.about.until", { date: fmtDate(tr.to) })
    : tr?.note || null;
  const langLabel = p.language ? tMaybe(`sources.lang.${p.language}`) || p.language : null;
  const statRows = Object.entries(stats).filter(([k, v]) => !STAT_HIDDEN.has(k) && v !== null && v !== undefined && v !== 0 && (STAT_KEYS.includes(k) || advanced));
  const hasLeft = sq.length > 0 || !!p.structure || keyEntities.length > 0;

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        {sq.length > 0 && (
          <section className="panel p-5 sm:p-6">
            <h3 className="font-display text-base font-semibold text-ink-900">{t("sources.overview.questions")}</h3>
            <p className="mt-1 text-sm text-ink-600">{t("sources.overview.questionsHint")}</p>
            <ul className="mt-3 divide-y divide-ink-100">
              {sq.map((s) => (
                <li key={s}>
                  <button className="flex w-full items-start gap-3 rounded-lg px-2 py-2.5 text-left text-sm text-ink-800 hover:bg-paper hover:text-brand-700" onClick={() => nav(`/w/${wsId}/chat?q=${encodeURIComponent(s)}`)}>
                    <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" /><span>{s}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {p.structure && (
          <section className="panel p-5 sm:p-6">
            <h3 className="font-display text-base font-semibold text-ink-900">{t("sources.overview.structure")}</h3>
            <p className="mt-2 text-sm leading-6 text-ink-700">{p.structure}</p>
          </section>
        )}
        {keyEntities.length > 0 && (
          <section className="panel p-5 sm:p-6">
            <h3 className="font-display text-base font-semibold text-ink-900">{t("sources.overview.keyEntities")}</h3>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {keyEntities.map((e, i) => (
                <Link key={`${e.name}-${i}`} to={`/w/${wsId}/graph?q=${encodeURIComponent(e.name)}`} title={entityTypeLabel(e.type)}
                  className="chip bg-ink-100/70 text-ink-800 hover:bg-brand-50 hover:text-brand-700">
                  {e.name}<span className="text-ink-400">{entityTypeLabel(e.type)}</span>
                </Link>
              ))}
            </div>
          </section>
        )}
        {!hasLeft && (
          <div className="panel"><Empty icon={<MessageSquare />} title={t("sources.overview.emptyTitle")} hint={t("sources.overview.emptyHint")} /></div>
        )}
      </div>

      <div className="space-y-6">
        <section className="panel p-5 sm:p-6">
          <h3 className="font-display text-base font-semibold text-ink-900">{t("sources.about.title")}</h3>
          <dl className="mt-3 space-y-2.5 text-sm">
            {kind && <Row k={t("sources.about.kind")} v={kind} />}
            {p.audience && <Row k={t("sources.about.audience")} v={p.audience} />}
            {langLabel && <Row k={t("sources.about.language")} v={langLabel} />}
            {covers && <Row k={t("sources.about.covers")} v={covers} />}
            {sens && <Row k={t("sources.about.sensitivity")} v={tMaybe(`sources.sensitivity.${sens}`) || sens} />}
            <Row k={t("sources.about.format")} v={sourceLabel(d.source_type)} />
            {statRows.map(([k, v]) => {
              return <Row key={k} k={tMaybe(`sources.stat.${k}`) || k} v={fmtNumber(v)} />;
            })}
            <Advanced>{p.generated_by && <Row k={t("sources.about.generatedBy")} v={t(p.generated_by === "llm" ? "sources.about.byAi" : "sources.about.byStats")} />}</Advanced>
          </dl>
        </section>
        <section className="panel p-5 sm:p-6">
          <h3 className="font-display text-base font-semibold text-ink-900">{t("sources.origin.title")}</h3>
          <dl className="mt-3 space-y-2.5 text-sm">
            <Row k={t("sources.origin.uploadedBy")} v={d.uploaded_by || "—"} />
            <Row k={t("sources.origin.uploadedOn")} v={fmtDate(d.created_at, true)} />
            {d.supersedes_id && <Row k={t("sources.origin.replaces")} v={<Link className="link" to={`/w/${wsId}/documents/${d.supersedes_id}`}>{t("sources.origin.previous")}</Link>} />}
            <Advanced>
              {d.series_key && <Row k={t("sources.tech.seriesKey")} v={<code className="break-all font-mono text-xs">{d.series_key}</code>} />}
              <Row k={t("sources.tech.documentId")} v={<code className="break-all font-mono text-xs">{d.document_id}</code>} />
              <Row k={t("sources.tech.docType")} v={<code className="font-mono text-xs">{d.doc_type}</code>} />
              <Row k={t("sources.tech.replaceMode")} v={<code className="font-mono text-xs">{d.replace_mode}</code>} />
            </Advanced>
          </dl>
        </section>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="shrink-0 text-ink-600">{k}</dt>
      <dd className="min-w-0 text-right text-ink-900">{v}</dd>
    </div>
  );
}

function TablesTab({ d }: { d: DocumentDetail }) {
  const t = useT();
  const { wsId } = useWorkspace();
  if (!d.tables.length) return <div className="panel"><Empty icon={<Table2 />} title={t("sources.tables.emptyTitle")} hint={t("sources.tables.emptyHint")} /></div>;
  return (
    <div className="panel overflow-hidden">
      <ul className="divide-y divide-ink-100">
        {d.tables.map((tb) => (
          <li key={tb.id}>
            <Link to={`/w/${wsId}/tables/${tb.id}`} className="group flex gap-4 px-4 py-4 transition hover:bg-paper focus-visible:bg-paper focus-visible:rounded-none focus-visible:shadow-[inset_0_0_0_3px_rgba(91,76,245,.35)] sm:px-5">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-verify-50 text-verify-700"><Table2 className="h-[18px] w-[18px]" /></span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <span className="font-medium text-ink-900 group-hover:text-brand-700">{tb.title}</span>
                  <span className="text-xs text-ink-600">{t("rows", { n: fmtNumber(tb.row_count) })}, {t("columns", { n: fmtNumber(tb.columns.length) })}</span>
                </div>
                {tb.source_location && <div className="mt-0.5 text-xs text-ink-600">{t("sources.tables.foundAt", { where: tb.source_location })}</div>}
                <Advanced><div className="mt-0.5 font-mono text-[11px] text-ink-400">{tb.name}</div></Advanced>
                {tb.description && <p className="mt-2 text-sm leading-6 text-ink-700">{tb.description}</p>}
                <div className="mt-2 flex flex-wrap gap-1">
                  {tb.columns.slice(0, 8).map((c) => <span key={c.name} className="rounded-md bg-ink-100/60 px-1.5 py-0.5 text-[11px] text-ink-700">{c.original || c.name}</span>)}
                  {tb.columns.length > 8 && <span className="px-1 text-[11px] text-ink-600">{t("sources.tables.moreColumns", { n: tb.columns.length - 8 })}</span>}
                </div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EntitiesTab({ d, busy }: { d: DocumentDetail; busy: boolean }) {
  const t = useT();
  const { wsId } = useWorkspace();
  if (!d.entities.length) {
    return <div className="panel"><Empty icon={<Network />} title={t("sources.entities.emptyTitle")} hint={t(busy ? "sources.entities.emptyBusy" : "sources.entities.emptyHint")} /></div>;
  }
  const byType = d.entities.reduce<Record<string, typeof d.entities>>((acc, e) => { (acc[e.type] ||= []).push(e); return acc; }, {});
  const groups = Object.entries(byType).sort((a, b) => b[1].length - a[1].length);
  return (
    <div className="panel p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-sm leading-6 text-ink-600">{t("sources.entities.intro")}</p>
        <Link to={`/w/${wsId}/graph`} className="btn-secondary btn-sm"><Network className="h-3.5 w-3.5" /> {t("sources.entities.openMap")}</Link>
      </div>
      <div className="mt-5 divide-y divide-ink-100">
        {groups.map(([type, list]) => (
          <section key={type} className="py-4 first:pt-0 last:pb-0">
            <h3 className="mb-2.5 text-sm font-semibold text-ink-900">
              {entityTypeLabel(type)} <span className="font-normal text-ink-600">({list.length})</span>
              <Advanced><code className="ml-2 font-mono text-[11px] font-normal text-ink-400">{type}</code></Advanced>
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {[...list].sort((a, b) => b.mentions - a.mentions).map((e) => (
                <Link key={e.id} to={`/w/${wsId}/graph/${e.id}`} title={e.description || undefined}
                  className="chip bg-ink-100/70 text-ink-800 hover:bg-brand-50 hover:text-brand-700">
                  {e.name}
                  {e.mentions > 1 && <span className="text-ink-400" aria-label={t("sources.entities.mentions", { n: e.mentions })}>×{e.mentions}</span>}
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function RecordsTab({ documentId }: { documentId: string }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const [page, setPage] = useState(1);
  const q = useQuery({ queryKey: ["doc-records", wsId, documentId, page], queryFn: () => api.documentRecords(wsId, documentId, { page, page_size: 25 }) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  if (!q.data?.items.length) return <div className="panel"><Empty icon={<TextQuote />} title={t("sources.passages.emptyTitle")} hint={t("sources.passages.emptyHint")} /></div>;
  return (
    <div className="space-y-3">
      <p className="max-w-3xl text-sm leading-6 text-ink-600">{t("sources.passages.intro")}</p>
      <div className="panel overflow-hidden">
        <ul className="divide-y divide-ink-100">
          {q.data.items.map((r) => (
            <li key={r.record_id} className="px-4 py-4 sm:px-5">
              <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-ink-600">
                <span className="font-medium text-ink-700">{r.location || docTypeLabel(r.doc_type)}</span>
                {r.citation && r.citation !== r.location && <span>{r.citation}</span>}
                {!r.is_active && <StatusBadge status="superseded" />}
                {r.suspicious_content && <Badge tone="red" title={t("sources.passages.suspiciousHint")}>{t("sources.passages.suspicious")}</Badge>}
                <Advanced><code className="font-mono text-[11px] text-ink-400">{r.record_id}</code></Advanced>
              </div>
              <div className="whitespace-pre-wrap text-sm leading-6 text-ink-800">{r.content}</div>
            </li>
          ))}
        </ul>
        <div className="border-t border-ink-100 px-5"><Pagination page={page} pageSize={25} total={q.data.total} onChange={setPage} /></div>
      </div>
    </div>
  );
}

function JobsTab({ d }: { d: DocumentDetail }) {
  const t = useT();
  if (!d.jobs.length) return <div className="panel"><Empty title={t("sources.jobs.empty")} /></div>;
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full min-w-[720px]">
        <thead><tr>
          <th className="th">{t("sources.jobs.type")}</th><th className="th">{t("sources.jobs.status")}</th><th className="th">{t("sources.jobs.progress")}</th>
          <th className="th">{t("sources.jobs.started")}</th><th className="th">{t("sources.jobs.finished")}</th><th className="th">{t("sources.jobs.result")}</th>
        </tr></thead>
        <tbody>{d.jobs.map((j) => (
          <tr key={j.job_id}>
            <td className="td font-mono text-xs">{j.type}</td>
            <td className="td"><StatusBadge status={j.status} /></td>
            <td className="td text-xs">{j.progress.percent !== null ? `${j.progress.percent}%` : "—"}</td>
            <td className="td whitespace-nowrap text-xs">{fmtDate(j.started_at, true)}</td>
            <td className="td whitespace-nowrap text-xs">{fmtDate(j.finished_at, true)}</td>
            <td className="td max-w-md text-xs">{j.error ? <span className="text-alert-700">{j.error}</span> : j.result ? <code className="break-all text-ink-600">{JSON.stringify(j.result).slice(0, 200)}</code> : "—"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function EditModal({ onClose, d, onSaved }: { onClose: () => void; d: DocumentDetail; onSaved: () => void }) {
  const t = useT();
  const { advanced } = usePrefs();
  const { wsId, ws } = useWorkspace();
  const toast = useToast();
  const [form, setForm] = useState({ title: d.title, doc_type: d.doc_type, effective_date: d.effective_date || "", version: d.version || "" });
  const packs = useQuery({ queryKey: ["domain-packs"], queryFn: api.domainPacks });
  const packTypes = packs.data?.find((p) => p.key === ws.domain_pack)?.doc_types || [];
  const docTypes = packTypes.length ? [...new Set([...packTypes, d.doc_type])] : [];
  const mut = useMutation({
    mutationFn: () => api.updateDocument(wsId, d.document_id, { title: form.title, doc_type: form.doc_type, effective_date: form.effective_date || null, version: form.version || null }),
    onSuccess: () => { toast.ok(t("action.saved")); onSaved(); onClose(); }, onError: toast.err,
  });
  return (
    <Modal open onClose={onClose} title={t("sources.edit.title")}
      footer={<><button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button><button className="btn-primary" disabled={mut.isPending || !form.title.trim()} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />} {t("action.save")}</button></>}>
      <div className="space-y-4">
        <Field label={t("sources.field.title")}><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
        <Field label={t("sources.field.docType")}>
          {docTypes.length ? (
            <select className="input" value={form.doc_type} onChange={(e) => setForm({ ...form, doc_type: e.target.value })}>
              {docTypes.map((dt) => <option key={dt} value={dt}>{docTypeLabel(dt)}{advanced && docTypeLabel(dt) !== dt ? ` (${dt})` : ""}</option>)}
            </select>
          ) : <input className="input" value={form.doc_type} onChange={(e) => setForm({ ...form, doc_type: e.target.value })} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("sources.field.effectiveDate")}><input className="input" type="date" value={form.effective_date} onChange={(e) => setForm({ ...form, effective_date: e.target.value })} /></Field>
          <Field label={t("sources.field.version")}><input className="input" value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} placeholder={t("sources.field.versionPlaceholder")} /></Field>
        </div>
      </div>
    </Modal>
  );
}
