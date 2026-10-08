// Studio mô hình dữ liệu: 5 bước tuần tự — Sheet -> Liên kết -> Cách tính từng cột -> Dựng & đối chiếu -> Chia sẻ.
// Mọi thứ hiển thị bằng câu nghiệp vụ (song ngữ, sinh từ chính quy tắc); SQL/mã định danh chỉ hiện ở chế độ kỹ thuật.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertTriangle, ArrowLeft, Check, CheckCheck, ChevronRight, Cog, Copy, Download, FileText, Hammer, Link2, MessageCircle, Pencil,
  RefreshCw, Search, Share2, Table2, X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { LineageMap } from "@/components/LineageMap";
import { ColumnEditor } from "@/components/model/ColumnEditor";
import { CheckLine, EvidenceChips, KindTag, SamplesTable, Section, typeLabel } from "@/components/model/shared";
import { Confirm, Empty, ErrorBox, Field, Loading, MatchMeter, Modal, Spinner, StatusBadge, useToast } from "@/components/ui";
import { useI18n } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api, download } from "@/lib/api";
import { fmtCell, fmtDate, fmtNumber, fmtPct, fmtRelative } from "@/lib/format";
import type { DataModelDetail, KeyPairSpec, OutputColumnSpec, RelationshipSpec, TableSpecView } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { Advanced, usePrefs } from "@/state/prefs";

type Step = "sheets" | "connections" | "calculations" | "build" | "share";
const STEPS: Step[] = ["sheets", "connections", "calculations", "build", "share"];
const ANALYZE_STEPS = ["read", "main", "relationships", "formulas", "search", "assemble", "ai", "done"];

export function ModelStudioPage() {
  const { wsId, can } = useWorkspace();
  const { modelId = "", step } = useParams();
  const { t } = useI18n();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["model", wsId, modelId];
  const q = useQuery({
    queryKey: key, queryFn: () => api.model(wsId, modelId),
    refetchInterval: (query) => (["analyzing", "building"].includes(query.state.data?.status || "") ? 2500 : false),
  });
  const set = (m: DataModelDetail) => { qc.setQueryData(key, m); void qc.invalidateQueries({ queryKey: ["models", wsId] }); };
  const build = useMutation({ mutationFn: () => api.buildModel(wsId, modelId), onSuccess: () => { void q.refetch(); nav(`/w/${wsId}/models/${modelId}/build`); }, onError: toast.err });
  const [renaming, setRenaming] = useState(false);

  if (q.isLoading) return <Page><Loading /></Page>;
  if (q.error || !q.data) return <Page><ErrorBox error={q.error || "—"} retry={() => q.refetch()} /></Page>;
  const m = q.data;
  const ready = !!m.spec?.main_table && m.status !== "analyzing";
  const current: Step = (STEPS as string[]).includes(step || "") ? (step as Step)
    : m.status === "ready" ? "build" : (m.counts?.confirmed ? "calculations" : "sheets");
  const editor = can("editor");

  return (
    <Page wide>
      <Link to={`/w/${wsId}/models`} className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-600 hover:text-ink-900"><ArrowLeft className="h-4 w-4" />{t("nav.models")}</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-display text-[30px] font-semibold leading-tight text-ink-900">{m.name}</h1>
            <StatusBadge status={m.status} />
            {editor && <button className="btn-ghost btn-sm" onClick={() => setRenaming(true)} aria-label={t("action.edit")}><Pencil className="h-3.5 w-3.5" /></button>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-600">
            {m.documents?.map((d) => <Link key={d.id} to={`/w/${wsId}/documents/${d.id}`} className="inline-flex items-center gap-1.5 hover:text-ink-900"><FileText className="h-3.5 w-3.5" />{d.filename}</Link>)}
            {m.spec?.analyzed_at && <span>{t("models.studio.analyzed", { when: fmtRelative(m.spec.analyzed_at) })}</span>}
            <Advanced><span className="font-mono text-xs">{m.schema_name}.{m.view_name}</span></Advanced>
          </div>
        </div>
        {ready && editor && (
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" disabled={build.isPending || m.status === "building"} onClick={() => build.mutate()}>
              {build.isPending || m.status === "building" ? <Spinner className="text-white" /> : <Hammer className="h-4 w-4" />}
              {m.built_at ? t("models.studio.rebuild") : t("models.studio.build")}
            </button>
          </div>
        )}
      </div>

      {m.status === "analyzing" && <Analyzing m={m} />}
      {m.status === "error" && (
        <div className="mb-6 space-y-3">
          <ErrorBox error={t(m.job?.type === "model_build" ? "models.studio.buildFailed" : "models.studio.failed")} />
          <Advanced>{m.error && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-ink-950 p-3 text-xs text-ink-100">{m.error}</pre>}</Advanced>
          {editor && m.job?.type !== "model_build" && <ReanalyzeButton m={m} onDone={() => q.refetch()} />}
        </div>
      )}

      {ready && (
        <>
          <Notices m={m} />
          <Stepper m={m} current={current} />
          <div className="mt-6">
            {current === "sheets" && <SheetsStep m={m} onChanged={set} refetch={() => q.refetch()} />}
            {current === "connections" && <ConnectionsStep m={m} onChanged={set} />}
            {current === "calculations" && <CalculationsStep m={m} onChanged={set} />}
            {current === "build" && <BuildStep m={m} onBuild={() => build.mutate()} building={build.isPending || m.status === "building"} />}
            {current === "share" && <ShareStep m={m} />}
          </div>
        </>
      )}
      <RenameModal m={m} open={renaming} onClose={() => setRenaming(false)} onSaved={() => q.refetch()} />
    </Page>
  );
}

// ------------------------------------------------------------------ trạng thái đang phân tích
function Analyzing({ m }: { m: DataModelDetail }) {
  const { t } = useI18n();
  const note = String((m.job?.result as Record<string, unknown> | null)?.note || "read");
  const idx = Math.max(0, ANALYZE_STEPS.indexOf(note));
  return (
    <div className="panel grid gap-8 p-8 md:grid-cols-[1.1fr_1fr]">
      <div>
        <h2 className="font-display text-2xl font-semibold">{t("models.analyzing.title")}</h2>
        <p className="mt-2 max-w-md text-[15px] leading-7 text-ink-600">{t("models.analyzing.hint")}</p>
      </div>
      <ol className="space-y-2.5">
        {ANALYZE_STEPS.slice(0, -1).map((s, i) => (
          <li key={s} className={clsx("flex items-center gap-3 text-sm", i > idx ? "text-ink-400" : "text-ink-900")}>
            <span className={clsx("flex h-6 w-6 items-center justify-center rounded-full", i < idx ? "bg-verify-600 text-white" : i === idx ? "bg-ink-900 text-white" : "bg-ink-100")}>
              {i < idx ? <Check className="h-3.5 w-3.5" /> : i === idx ? <Spinner className="h-3.5 w-3.5 text-white" /> : <span className="text-[11px]">{i + 1}</span>}
            </span>
            {t(`models.analyzing.${s}`)}
          </li>
        ))}
      </ol>
    </div>
  );
}

function ReanalyzeButton({ m, onDone, mainTable, label }: { m: DataModelDetail; onDone: () => void; mainTable?: string; label?: string }) {
  const { wsId } = useWorkspace();
  const { t } = useI18n();
  const toast = useToast();
  const mut = useMutation({ mutationFn: () => api.reanalyzeModel(wsId, m.id, mainTable), onSuccess: onDone, onError: toast.err });
  return <button className="btn-secondary" disabled={mut.isPending} onClick={() => mut.mutate()}>{mut.isPending ? <Spinner /> : <RefreshCw className="h-4 w-4" />}{label || t("models.studio.reanalyze")}</button>;
}

function Notices({ m }: { m: DataModelDetail }) {
  const { pick } = useI18n();
  const [hide, setHide] = useState(false);
  const items = m.texts.notices.filter((n) => n.code !== "pivots_found" || true);
  if (hide || !items.length) return null;
  return (
    <div className="mb-6 rounded-2xl border border-ink-100 bg-white p-4">
      <div className="flex items-start gap-3">
        <ul className="flex-1 space-y-1.5 text-sm leading-6">
          {items.map((n, i) => (
            <li key={i} className="flex gap-2">
              <span className={clsx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", n.level === "warning" ? "bg-marker-300" : n.level === "error" ? "bg-alert-500" : "bg-brand-400")} />
              <span className="text-ink-800">{pick(n)}</span>
            </li>
          ))}
        </ul>
        <button className="btn-ghost btn-sm" onClick={() => setHide(true)}><X className="h-3.5 w-3.5" /></button>
      </div>
    </div>
  );
}

function Stepper({ m, current }: { m: DataModelDetail; current: Step }) {
  const { t } = useI18n();
  const { wsId } = useWorkspace();
  const c = m.counts;
  const meta: Record<Step, string> = {
    sheets: t("models.step.sheetsMeta", { n: c?.sources ?? 0 }),
    connections: t("models.step.connectionsMeta", { n: c?.relationships ?? 0 }),
    calculations: c?.attention ? t("models.step.calcAttention", { n: c.attention }) : t("models.step.calcDone"),
    build: m.built_at ? (m.validation_summary?.score !== null && m.validation_summary?.score !== undefined ? t("models.step.buildScore", { pct: fmtPct(m.validation_summary.score) }) : t("models.step.built")) : t("models.step.notBuilt"),
    share: t("models.step.shareMeta"),
  };
  return (
    <nav className="grid grid-cols-2 gap-2 sm:grid-cols-5" aria-label={t("models.step.aria")}>
      {STEPS.map((s, i) => {
        const on = s === current;
        return (
          <Link key={s} to={`/w/${wsId}/models/${m.id}/${s}`} aria-current={on ? "step" : undefined}
            className={clsx("group rounded-xl px-3.5 py-3 transition", on ? "bg-ink-900 text-white shadow-lift" : "bg-white text-ink-900 ring-1 ring-ink-100 hover:ring-ink-300")}>
            <span className="flex items-center gap-2">
              <span className={clsx("font-display text-sm font-semibold tabular-nums", on ? "text-marker-200" : "text-ink-400")}>{i + 1}</span>
              <span className="text-sm font-semibold">{t(`models.step.${s}`)}</span>
            </span>
            <span className={clsx("mt-0.5 block truncate text-xs", on ? "text-ink-300" : s === "calculations" && c?.attention ? "text-marker-700" : "text-ink-600")}>{meta[s]}</span>
          </Link>
        );
      })}
    </nav>
  );
}

// ------------------------------------------------------------------ 1. sheet
function SheetsStep({ m, onChanged, refetch }: { m: DataModelDetail; onChanged: (m: DataModelDetail) => void; refetch: () => void }) {
  const { t } = useI18n();
  const { wsId, can } = useWorkspace();
  const toast = useToast();
  const [sel, setSel] = useState<string | null>(null);
  const spec = m.spec;
  const relBySource = useMemo(() => {
    const r: Record<string, RelationshipSpec[]> = {};
    spec.relationships.forEach((x) => (r[x.source_table] ||= []).push(x));
    return r;
  }, [spec.relationships]);
  const role = useMutation({ mutationFn: ({ k, r }: { k: string; r: "source" | "ignored" }) => api.patchModelTable(wsId, m.id, k, r), onSuccess: onChanged, onError: toast.err });
  const selected = spec.tables.find((x) => x.key === sel);
  const sorted = [...spec.tables].sort((a, b) => (a.role === "main" ? -1 : b.role === "main" ? 1 : a.role === b.role ? b.row_count - a.row_count : a.role === "source" ? -1 : 1));
  return (
    <>
      <Section title={t("models.sheets.title")} hint={t("models.sheets.hint")}>
        <LineageMap spec={spec} selected={sel} onSelect={setSel} viewName={spec.view_name} built={!!m.built_at && !m.stale} />
      </Section>
      {selected && <SheetDetail m={m} table={selected} rels={relBySource[selected.key] || []} onClose={() => setSel(null)}
        onRole={(r) => role.mutate({ k: selected.key, r })} refetch={refetch} canEdit={can("editor")} />}
      <div className="panel overflow-hidden">
        <table className="w-full">
          <thead><tr>
            <th className="th">{t("models.sheets.sheet")}</th><th className="th">{t("models.sheets.role")}</th>
            <th className="th text-right">{t("models.sheets.rows")}</th><th className="th hidden md:table-cell">{t("models.sheets.linkedBy")}</th>
            <th className="th hidden lg:table-cell">{t("models.sheets.found")}</th>
          </tr></thead>
          <tbody>
            {sorted.map((tb) => {
              const rel = (relBySource[tb.key] || []).find((r) => r.status !== "rejected");
              return (
                <tr key={tb.key} className={clsx("cursor-pointer hover:bg-paper", sel === tb.key && "bg-brand-50/50")} onClick={() => setSel(tb.key)}>
                  <td className="td font-medium">{tb.title}</td>
                  <td className="td"><span className={clsx("chip", tb.role === "main" ? "bg-ink-900 text-white" : tb.role === "source" ? "bg-verify-50 text-verify-700" : "bg-ink-100/70 text-ink-600")}>{t(`models.role.${tb.role}`)}</span></td>
                  <td className="td text-right tabular-nums">{fmtNumber(tb.row_count)}</td>
                  <td className="td hidden text-ink-700 md:table-cell">{rel ? m.texts.relationships[rel.id] && <LinkKeys m={m} rel={rel} /> : "—"}</td>
                  <td className="td hidden text-xs text-ink-600 lg:table-cell">
                    {[tb.formula_count ? t("models.sheets.formulas", { n: tb.formula_count }) : null, tb.pivots.length ? t("models.sheets.pivots", { n: tb.pivots.length }) : null].filter(Boolean).join(", ") || "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function LinkKeys({ m, rel }: { m: DataModelDetail; rel: RelationshipSpec }) {
  const { pick } = useI18n();
  return <span className="text-sm">{pick(m.texts.relationships[rel.id]?.keys)}</span>;
}

function SheetDetail({ m, table, rels, onClose, onRole, refetch, canEdit }: {
  m: DataModelDetail; table: TableSpecView; rels: RelationshipSpec[]; onClose: () => void; onRole: (r: "source" | "ignored") => void; refetch: () => void; canEdit: boolean;
}) {
  const { t, pick } = useI18n();
  const { wsId } = useWorkspace();
  const [rows, setRows] = useState(false);
  const data = useQuery({ queryKey: ["model-rows", wsId, m.id, table.key], queryFn: () => api.modelTableRows(wsId, m.id, table.key, { limit: 30 }), enabled: rows });
  const labels = new Map(table.columns.map((c) => [c.name, c.label]));
  return (
    <div className="panel mb-6 animate-rise p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-xl font-semibold">{table.title}</h3>
          <p className="mt-1 text-sm text-ink-600">{t("models.sheet.summary", { rows: fmtNumber(table.row_count), cols: table.columns.length, range: table.data_range || "" })}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canEdit && table.role !== "main" && <ReanalyzeButton m={m} onDone={refetch} mainTable={table.key} label={t("models.sheet.makeMain")} />}
          {canEdit && table.role !== "main" && (table.role === "ignored"
            ? <button className="btn-secondary" onClick={() => onRole("source")}>{t("models.sheet.use")}</button>
            : <button className="btn-ghost" onClick={() => onRole("ignored")}>{t("models.sheet.ignore")}</button>)}
          <button className="btn-ghost btn-sm" onClick={onClose}><X className="h-4 w-4" /></button>
        </div>
      </div>
      {rels.map((r) => <p key={r.id} className="mb-3 text-sm leading-6 text-ink-800">{pick(m.texts.relationships[r.id]?.text)}</p>)}
      {table.pivots.length > 0 && (
        <div className="mb-4">
          <div className="mb-1.5 text-[13px] font-medium text-ink-700">{t("models.sheet.pivots")}</div>
          <ul className="space-y-1 text-sm text-ink-700">
            {table.pivots.map((p) => (
              <li key={p.name} className="flex flex-wrap gap-1.5">
                <span className="chip bg-ink-900 text-white">{p.name}</span>
                <span>{p.values.map((v) => t(`models.func.${v.func}`) + " " + v.field).join(", ")} — {t("models.sheet.by")} {p.rows.join(", ")}</span>
                {p.filters.filter((f) => f.selected || f.excluded.length).map((f) => <span key={f.field} className="text-ink-600">({f.field}: {f.selected ? f.selected.join(", ") : `≠ ${f.excluded.join(", ")}`})</span>)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        {table.columns.map((c) => (
          <span key={c.name} className="chip bg-paper text-ink-800 ring-1 ring-ink-100" title={`${typeLabel(c.type, t)}${c.examples.length ? " — " + c.examples.join(", ") : ""}${c.formula ? " — " + c.formula : ""}`}>
            {c.label}{c.formula_count > 0 && <span className="text-brand-600">ƒ</span>}
          </span>
        ))}
      </div>
      <button className="btn-ghost btn-sm mt-4" onClick={() => setRows((v) => !v)}><Table2 className="h-3.5 w-3.5" />{rows ? t("models.sheet.hideRows") : t("models.sheet.showRows")}</button>
      {rows && (data.isLoading ? <Loading /> : data.data && (
        <div className="mt-2 max-h-80 overflow-auto rounded-xl border border-ink-100">
          <table className="w-full text-sm"><thead className="sticky top-0"><tr>{data.data.columns.map((c) => <th key={c} className="th">{c === "_row" ? t("models.samples.row") : labels.get(c) || c}</th>)}</tr></thead>
            <tbody>{data.data.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className="td whitespace-nowrap">{v === null ? <span className="text-ink-300">—</span> : fmtCell(v, table.columns.find((c) => c.name === data.data!.columns[j])?.type)}</td>)}</tr>)}</tbody></table>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ 2. liên kết
const NORMS: Record<string, KeyPairSpec["base_transforms"]> = {
  exact: [], digits: [{ op: "number_part", args: [] }], token: [{ op: "first_token", args: [] }],
  token_digits: [{ op: "first_token", args: [] }, { op: "number_part", args: [] }],
};
const normOf = (ts: KeyPairSpec["base_transforms"]) => Object.keys(NORMS).find((k) => JSON.stringify(NORMS[k].map((x) => x.op)) === JSON.stringify(ts.map((x) => x.op))) || "exact";

function ConnectionsStep({ m, onChanged }: { m: DataModelDetail; onChanged: (m: DataModelDetail) => void }) {
  const { t, pick } = useI18n();
  const { wsId, can } = useWorkspace();
  const toast = useToast();
  const [edit, setEdit] = useState<RelationshipSpec | "new" | null>(null);
  const spec = m.spec;
  const status = useMutation({ mutationFn: ({ id, s }: { id: string; s: string }) => api.patchModelRelationship(wsId, m.id, id, { status: s }), onSuccess: onChanged, onError: toast.err });
  const title = (k: string) => spec.tables.find((x) => x.key === k)?.title || k;
  const label = (n: string) => spec.columns.find((c) => c.name === n)?.label || n;
  const rels = [...spec.relationships].sort((a, b) => (a.status === "rejected" ? 1 : 0) - (b.status === "rejected" ? 1 : 0) || (m.texts.relationships[b.id]?.used_by.length || 0) - (m.texts.relationships[a.id]?.used_by.length || 0));
  return (
    <Section title={t("models.conn.title")} hint={t("models.conn.hint")}
      aside={can("editor") && <button className="btn-secondary" onClick={() => setEdit("new")}><Link2 className="h-4 w-4" />{t("models.conn.add")}</button>}>
      <ul className="space-y-3">
        {rels.map((r) => {
          const tx = m.texts.relationships[r.id];
          return (
            <li key={r.id} className={clsx("card p-5", r.status === "rejected" && "opacity-55")}>
              <div className="flex flex-wrap items-start gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 font-display text-[17px] font-semibold">
                    {title(r.source_table)} <ChevronRight className="h-4 w-4 text-ink-400" /> {title(r.target_table)}
                    {r.status === "confirmed" && <span className="chip bg-verify-50 text-verify-700"><Check className="h-3 w-3" />{t("models.status.confirmed")}</span>}
                    {r.status === "rejected" && <span className="chip bg-alert-50 text-alert-700">{t("models.status.rejected")}</span>}
                  </div>
                  <p className="mt-1.5 text-sm leading-6 text-ink-800">{pick(tx?.text)}</p>
                  <div className="mt-2.5 flex flex-wrap items-center gap-3">
                    {r.coverage !== null && <MatchMeter rate={r.coverage} label={t("models.conn.coverage")} />}
                    <EvidenceChips items={tx?.evidence || []} />
                  </div>
                  {tx?.used_by.length ? (
                    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs text-ink-600">{t("models.conn.feeds")}
                      {tx.used_by.map((n) => <span key={n} className="chip bg-paper text-ink-800 ring-1 ring-ink-100">{label(n)}</span>)}
                    </div>
                  ) : <div className="mt-3 text-xs text-ink-400">{t("models.conn.unused")}</div>}
                </div>
                {can("editor") && (
                  <div className="flex shrink-0 gap-1.5">
                    {r.status !== "confirmed" && <button className="btn-secondary btn-sm" onClick={() => status.mutate({ id: r.id, s: "confirmed" })}><Check className="h-3.5 w-3.5" />{t("models.conn.approve")}</button>}
                    <button className="btn-ghost btn-sm" onClick={() => setEdit(r)}><Pencil className="h-3.5 w-3.5" />{t("action.edit")}</button>
                    {r.status !== "rejected"
                      ? <button className="btn-ghost btn-sm text-alert-700" onClick={() => status.mutate({ id: r.id, s: "rejected" })}><X className="h-3.5 w-3.5" /></button>
                      : <button className="btn-ghost btn-sm" onClick={() => status.mutate({ id: r.id, s: "proposed" })}>{t("models.conn.restore")}</button>}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {edit && <KeyEditor m={m} rel={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={(x) => { onChanged(x); setEdit(null); }} />}
    </Section>
  );
}

function KeyEditor({ m, rel, onClose, onSaved }: { m: DataModelDetail; rel: RelationshipSpec | null; onClose: () => void; onSaved: (m: DataModelDetail) => void }) {
  const { t } = useI18n();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const spec = m.spec;
  const main = spec.tables.find((x) => x.key === spec.main_table)!;
  const [source, setSource] = useState(rel?.source_table || spec.tables.find((x) => x.key !== spec.main_table)?.key || "");
  const kp = rel?.keys[0];
  const [base, setBase] = useState(kp?.base_column || main.columns[0]?.name || "");
  const [srcCol, setSrcCol] = useState(kp?.source_column || "");
  const [bn, setBn] = useState(kp ? normOf(kp.base_transforms) : "exact");
  const [sn, setSn] = useState(kp ? normOf(kp.source_transforms) : "exact");
  const src = spec.tables.find((x) => x.key === source);
  useEffect(() => { if (!rel && src && !src.columns.find((c) => c.name === srcCol)) setSrcCol(src.columns[0]?.name || ""); }, [source]); // eslint-disable-line react-hooks/exhaustive-deps
  const keys = [{ base_column: base, source_column: srcCol, base_transforms: NORMS[bn], source_transforms: NORMS[sn] }];
  const mut = useMutation({
    mutationFn: () => (rel ? api.patchModelRelationship(wsId, m.id, rel.id, { keys }) : api.addModelRelationship(wsId, m.id, { source_table: source, keys })),
    onSuccess: onSaved, onError: toast.err,
  });
  return (
    <Modal open onClose={onClose} title={rel ? t("models.conn.editTitle") : t("models.conn.addTitle")} width="max-w-xl"
      footer={<><button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button><button className="btn-primary" disabled={!base || !srcCol || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />}{t("action.save")}</button></>}>
      <div className="space-y-4">
        {!rel && (
          <Field label={t("models.conn.sheet")}>
            <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
              {spec.tables.filter((x) => x.key !== spec.main_table).map((x) => <option key={x.key} value={x.key}>{x.title}</option>)}
            </select>
          </Field>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("models.conn.inMain", { sheet: main.title })}>
            <select className="input" value={base} onChange={(e) => setBase(e.target.value)}>{main.columns.map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}</select>
          </Field>
          <Field label={t("models.conn.inSource", { sheet: src?.title || "" })}>
            <select className="input" value={srcCol} onChange={(e) => setSrcCol(e.target.value)}>{(src?.columns || []).map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}</select>
          </Field>
          <Field label={t("models.conn.compare")}>
            <select className="input" value={bn} onChange={(e) => setBn(e.target.value)}>{Object.keys(NORMS).map((k) => <option key={k} value={k}>{t(`models.norm.${k}`)}</option>)}</select>
          </Field>
          <Field label={t("models.conn.compare")}>
            <select className="input" value={sn} onChange={(e) => setSn(e.target.value)}>{Object.keys(NORMS).map((k) => <option key={k} value={k}>{t(`models.norm.${k}`)}</option>)}</select>
          </Field>
        </div>
        <p className="text-xs leading-5 text-ink-600">{t("models.conn.normHint")}</p>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ 3. cách tính
function CalculationsStep({ m, onChanged }: { m: DataModelDetail; onChanged: (m: DataModelDetail) => void }) {
  const { t, pick } = useI18n();
  const { wsId, can } = useWorkspace();
  const toast = useToast();
  const spec = m.spec;
  const [filter, setFilter] = useState<"attention" | "all" | "confirmed">(m.counts?.attention ? "attention" : "all");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [dateOpen, setDateOpen] = useState(false);
  const confirmAll = useMutation({ mutationFn: () => api.confirmModel(wsId, m.id), onSuccess: (r) => { onChanged(r.model); toast.ok(t("models.calc.confirmedN", { n: r.confirmed })); }, onError: toast.err });
  const approve = useMutation({ mutationFn: (name: string) => api.patchModelColumn(wsId, m.id, name, { status: "confirmed" }), onSuccess: onChanged, onError: toast.err });
  const label = (n: string) => spec.columns.find((c) => c.name === n)?.label || n;
  const cols = spec.columns.filter((c) => {
    const att = m.texts.columns[c.name]?.attention;
    if (filter === "attention" && !att) return false;
    if (filter === "confirmed" && c.status !== "confirmed") return false;
    return !search || c.label.toLowerCase().includes(search.toLowerCase());
  });
  const groups = useMemo(() => {
    const g: { name: string | null; cols: OutputColumnSpec[] }[] = [];
    for (const c of cols) {
      const last = g[g.length - 1];
      if (last && last.name === c.group) last.cols.push(c);
      else g.push({ name: c.group, cols: [c] });
    }
    return g;
  }, [cols]);
  const column = spec.columns.find((c) => c.name === open) || null;
  const asOf = spec.parameters.as_of_date;
  const usesDate = spec.columns.some((c) => (c.rule.expression || "").includes("AS_OF") || (c.rule.filters || []).some((f) => f.as_of_offset_days !== null && f.as_of_offset_days !== undefined));

  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="seg">
          <button aria-pressed={filter === "attention"} onClick={() => setFilter("attention")}>{t("models.calc.attention", { n: m.counts?.attention ?? 0 })}</button>
          <button aria-pressed={filter === "all"} onClick={() => setFilter("all")}>{t("models.calc.all", { n: spec.columns.length })}</button>
          <button aria-pressed={filter === "confirmed"} onClick={() => setFilter("confirmed")}>{t("models.calc.confirmed", { n: m.counts?.confirmed ?? 0 })}</button>
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-ink-400" />
          <input className="input w-56 pl-8" placeholder={t("models.calc.search")} value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <span className="flex-1" />
        {(usesDate || asOf) && (
          <button className="btn-secondary" onClick={() => setDateOpen(true)} title={t("models.calc.reportDateHint")}>
            {t("models.calc.reportDate")}: <b className="font-semibold">{asOf === "today" || !asOf ? t("models.calc.today") : fmtDate(asOf)}</b>
          </button>
        )}
        {can("editor") && <button className="btn-ink" disabled={confirmAll.isPending} onClick={() => confirmAll.mutate()}>{confirmAll.isPending ? <Spinner className="text-white" /> : <CheckCheck className="h-4 w-4 text-marker-200" />}{t("models.calc.confirmStrong")}</button>}
      </div>
      {cols.length === 0 && <div className="panel"><Empty icon={<CheckCheck />} title={t("models.calc.emptyTitle")} hint={t("models.calc.emptyHint")} /></div>}
      <div className="space-y-6">
        {groups.map((g, gi) => (
          <section key={gi}>
            {g.name && <h3 className="mb-2 px-1 font-display text-[15px] font-semibold text-ink-700">{g.name}</h3>}
            <ul className="panel divide-y divide-ink-100 overflow-hidden">
              {g.cols.map((c) => {
                const tx = m.texts.columns[c.name];
                return (
                  <li key={c.name} className={clsx("grid cursor-pointer gap-3 px-5 py-4 transition hover:bg-paper md:grid-cols-[minmax(170px,1.1fr)_2.4fr_minmax(150px,auto)_auto] md:items-center", tx?.attention && "bg-marker-50/60")}
                    onClick={() => setOpen(c.name)}>
                    <div className="min-w-0">
                      <div className="truncate font-semibold text-ink-900">{c.label}</div>
                      <div className="mt-1"><KindTag kind={c.rule.kind} /></div>
                    </div>
                    <div className="min-w-0 text-sm leading-6 text-ink-800">
                      <p className="line-clamp-2">{pick(tx?.rule)}</p>
                      <div className="mt-1"><EvidenceChips items={tx?.evidence || []} /></div>
                    </div>
                    <div>{c.rule.kind === "input" ? <span className="text-xs text-ink-400">{t("models.calc.input")}</span> : <CheckLine check={c.check} columnLabel={label} />}</div>
                    <div className="flex items-center justify-end gap-2" onClick={(e) => e.stopPropagation()}>
                      {c.status === "confirmed"
                        ? <span className="chip bg-verify-50 text-verify-700"><Check className="h-3 w-3" />{t("models.status.confirmed")}</span>
                        : can("editor") && c.rule.kind !== "unresolved" && c.status !== "rejected" && (c.rule.kind === "input" || (c.check?.rate ?? 0) >= 0.9 || c.check?.basis === "isolated" && (c.check.rate ?? 0) >= 0.9)
                          ? <button className="btn-secondary btn-sm" onClick={() => approve.mutate(c.name)}><Check className="h-3.5 w-3.5" />{t("models.calc.approve")}</button>
                          : <button className="btn-secondary btn-sm" onClick={() => setOpen(c.name)}>{t("models.calc.fix")}</button>}
                      <ChevronRight className="h-4 w-4 text-ink-300" />
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
      <ColumnEditor model={m} column={column} onClose={() => setOpen(null)} onSaved={onChanged} canEdit={can("editor")} />
      <ReportDateModal m={m} open={dateOpen} onClose={() => setDateOpen(false)} onSaved={onChanged} />
    </>
  );
}

function ReportDateModal({ m, open, onClose, onSaved }: { m: DataModelDetail; open: boolean; onClose: () => void; onSaved: (m: DataModelDetail) => void }) {
  const { t } = useI18n();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const cur = m.spec.parameters.as_of_date;
  const [mode, setMode] = useState<"fixed" | "today">(cur && cur !== "today" ? "fixed" : "today");
  const [date, setDate] = useState(cur && cur !== "today" ? cur : new Date().toISOString().slice(0, 10));
  const mut = useMutation({ mutationFn: () => api.setModelParams(wsId, m.id, mode === "today" ? "today" : date), onSuccess: (x) => { onSaved(x); onClose(); }, onError: toast.err });
  return (
    <Modal open={open} onClose={onClose} title={t("models.date.title")}
      footer={<><button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button><button className="btn-primary" disabled={mut.isPending} onClick={() => mut.mutate()}>{t("action.save")}</button></>}>
      <p className="mb-4 text-sm leading-6 text-ink-700">{t("models.date.hint")}</p>
      {m.spec.parameters.as_of_source === "inferred" && <p className="mb-4 rounded-lg bg-marker-100 px-3 py-2 text-sm text-ink-800">{t("models.date.inferred", { date: cur ? fmtDate(cur) : "" })}</p>}
      <div className="space-y-2 text-sm">
        <label className="flex items-center gap-2"><input type="radio" checked={mode === "fixed"} onChange={() => setMode("fixed")} />{t("models.date.fixed")}<input type="date" className="input w-44" value={date} onChange={(e) => { setDate(e.target.value); setMode("fixed"); }} /></label>
        <label className="flex items-center gap-2"><input type="radio" checked={mode === "today"} onChange={() => setMode("today")} />{t("models.date.today")}</label>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ 4. dựng & đối chiếu
function BuildStep({ m, onBuild, building }: { m: DataModelDetail; onBuild: () => void; building: boolean }) {
  const { t } = useI18n();
  const { wsId, can } = useWorkspace();
  const { advanced } = usePrefs();
  const [page, setPage] = useState(0);
  const [openCol, setOpenCol] = useState<string | null>(null);
  const v = m.validation;
  const built = !!m.built_at;
  const prev = useQuery({ queryKey: ["model-preview", wsId, m.id, page, m.built_at], queryFn: () => api.modelPreview(wsId, m.id, { limit: 25, offset: page * 25 }), enabled: built && m.status !== "building" });
  const labels = new Map(m.spec.columns.map((c) => [c.name, c.label]));
  const types = new Map(m.spec.columns.map((c) => [c.name, c.type]));

  if (!built && m.status !== "building") {
    return (
      <div className="panel grid gap-8 p-8 md:grid-cols-[1.2fr_1fr] md:items-center">
        <div>
          <h2 className="font-display text-2xl font-semibold">{t("models.build.introTitle")}</h2>
          <ul className="mt-4 space-y-2.5 text-[15px] leading-6 text-ink-700">
            {["1", "2", "3", "4"].map((k) => <li key={k} className="flex gap-2.5"><Check className="mt-1 h-4 w-4 shrink-0 text-verify-600" />{t(`models.build.intro${k}`)}</li>)}
          </ul>
          {m.counts?.unresolved ? <p className="mt-4 rounded-lg bg-marker-100 px-3 py-2 text-sm text-ink-800">{t("models.build.unresolvedWarn", { n: m.counts.unresolved })}</p> : null}
        </div>
        <div className="text-center">
          {can("editor") && <button className="btn-primary px-6 py-3 text-base" disabled={building} onClick={onBuild}>{building ? <Spinner className="text-white" /> : <Hammer className="h-5 w-5" />}{t("models.studio.build")}</button>}
        </div>
      </div>
    );
  }
  if (m.status === "building") return <div className="panel p-10"><Loading label={t("models.build.building")} /></div>;
  const s = v.summary || {};
  const colRows = m.spec.columns.map((c) => ({ c, v: v.columns?.[c.name] })).filter((x) => x.v && x.v.kind !== "input")
    .sort((a, b) => (a.v?.rate ?? -1) - (b.v?.rate ?? -1));
  return (
    <>
      {m.stale && (
        <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-marker-300 bg-marker-100 px-4 py-3 text-sm text-ink-900">
          <AlertTriangle className="h-4 w-4 text-marker-700" /><span className="flex-1">{t("models.build.stale")}</span>
          {can("editor") && <button className="btn-ink btn-sm" onClick={onBuild}><Hammer className="h-3.5 w-3.5" />{t("models.studio.rebuild")}</button>}
        </div>
      )}
      <div className="panel mb-6 grid gap-6 p-7 lg:grid-cols-[auto_1fr] lg:items-center">
        <div className="pr-6 lg:border-r lg:border-ink-100">
          <div className="font-display text-[64px] font-semibold leading-none tracking-tight text-ink-900">{fmtPct(v.score ?? null)}</div>
          <p className="mt-2 max-w-[16rem] text-sm leading-6 text-ink-600">{t("models.build.scoreLabel")}</p>
        </div>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Stat n={s.matched ?? 0} label={t("models.build.match")} tone="text-verify-700" />
          <Stat n={s.partial ?? 0} label={t("models.build.partial")} tone="text-marker-700" />
          <Stat n={s.mismatch ?? 0} label={t("models.build.mismatch")} tone="text-alert-700" />
          <Stat n={s.not_calculated ?? 0} label={t("models.build.notCalculated")} tone="text-ink-600" />
          <Stat n={s.inputs ?? 0} label={t("models.build.inputs")} tone="text-ink-600" />
          <div className="col-span-2 text-sm text-ink-700 sm:col-span-5">
            {v.rows_aligned ? <span className="inline-flex items-center gap-1.5"><Check className="h-4 w-4 text-verify-600" />{t("models.build.rowsAligned", { n: fmtNumber(v.view_rows) })}</span>
              : <span className="inline-flex items-center gap-1.5 text-alert-700"><AlertTriangle className="h-4 w-4" />{t("models.build.rowsNotAligned", { view: v.view_rows ?? 0, excel: v.excel_rows ?? 0 })}</span>}
            <span className="ml-3 text-ink-600">{t("models.build.builtAt", { when: fmtRelative(m.built_at) })}</span>
          </div>
        </div>
      </div>

      <Section title={t("models.build.perColumn")} hint={t("models.build.perColumnHint")}>
        <ul className="panel divide-y divide-ink-100 overflow-hidden">
          {colRows.map(({ c, v: cv }) => (
            <li key={c.name} className="px-5 py-3">
              <button className="flex w-full flex-wrap items-center gap-3 text-left" onClick={() => setOpenCol(openCol === c.name ? null : c.name)}>
                <span className="min-w-0 flex-1 font-medium text-ink-900">{c.label}</span>
                {cv?.kind === "not_calculated" ? <span className="chip bg-ink-100 text-ink-700">{t("models.build.notCalculated")}</span>
                  : <MatchMeter rate={cv?.rate ?? null} label={t("models.build.ofRows", { m: cv?.matched ?? 0, n: cv?.compared ?? 0 })} />}
                <ChevronRight className={clsx("h-4 w-4 text-ink-300 transition", openCol === c.name && "rotate-90")} />
              </button>
              {openCol === c.name && cv && <div className="mt-3"><SamplesTable check={{ compared: cv.compared || 0, matched: cv.matched || 0, rate: cv.rate ?? null, expected_nulls: 0, samples: cv.samples || [] }} type={c.type} />
                {!cv.samples?.length && <p className="text-sm text-ink-600">{cv.kind === "not_calculated" ? t("models.build.notCalcHint") : t("models.build.noDiff")}</p>}</div>}
            </li>
          ))}
        </ul>
      </Section>

      <Section title={t("models.build.preview")} hint={prev.data?.source === "warehouse" ? t("models.build.previewHint") : undefined}>
        {prev.isLoading && <Loading />}
        {prev.data && (
          <>
            <div className="max-h-[480px] overflow-auto rounded-2xl border border-ink-100 bg-white">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10"><tr>{prev.data.columns.map((c) => <th key={c} className="th">{c === "source_row" ? t("models.samples.row") : advanced ? c : labels.get(c) || c}</th>)}</tr></thead>
                <tbody>{prev.data.rows.map((r, i) => <tr key={i} className="hover:bg-paper">{r.map((x, j) => {
                  const ty = types.get(prev.data!.columns[j]) || "";
                  return <td key={j} className={clsx("td whitespace-nowrap", ["integer", "double"].includes(ty) && "text-right tabular-nums")}>{x === null ? <span className="text-ink-300">—</span> : fmtCell(x, ty)}</td>;
                })}</tr>)}</tbody>
              </table>
            </div>
            <div className="mt-2 flex items-center justify-between text-sm text-ink-600">
              <span>{t("pagination.range", { from: page * 25 + 1, to: Math.min((page + 1) * 25, prev.data.total), total: prev.data.total })}</span>
              <div className="flex gap-1"><button className="btn-secondary btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>{t("action.previous")}</button><button className="btn-secondary btn-sm" disabled={(page + 1) * 25 >= prev.data.total} onClick={() => setPage(page + 1)}>{t("action.next")}</button></div>
            </div>
          </>
        )}
      </Section>
      <Advanced>
        {m.build.sql && <Section title={t("models.build.sql")}><pre className="max-h-96 overflow-auto rounded-2xl bg-ink-950 p-4 text-xs leading-5 text-ink-100">{m.build.sql}</pre></Section>}
      </Advanced>
    </>
  );
}

function Stat({ n, label, tone }: { n: number; label: string; tone: string }) {
  return <div><div className={clsx("font-display text-3xl font-semibold tabular-nums", tone)}>{n}</div><div className="text-xs text-ink-600">{label}</div></div>;
}

// ------------------------------------------------------------------ 5. chia sẻ
function ShareStep({ m }: { m: DataModelDetail }) {
  const { t, lang } = useI18n();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const nav = useNavigate();
  const [docLang, setDocLang] = useState(lang);
  const conn = useQuery({ queryKey: ["model-conn", wsId, m.id], queryFn: () => api.modelConnection(wsId, m.id) });
  const dl = (fmt: string) => download(api.modelDocumentPath(wsId, m.id, fmt, docLang), `${m.view_name}.${fmt}`).catch(toast.err);
  const copy = (s: string) => { void navigator.clipboard.writeText(s); toast.ok(t("action.copied")); };
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="panel p-6">
        <div className="mb-1 flex items-center gap-2"><FileText className="h-5 w-5 text-ink-400" /><h3 className="font-display text-lg font-semibold">{t("models.share.docTitle")}</h3></div>
        <p className="mb-4 text-sm leading-6 text-ink-600">{t("models.share.docHint")}</p>
        <div className="mb-4 flex items-center gap-3 text-sm"><span className="text-ink-700">{t("menu.language")}</span>
          <div className="seg"><button aria-pressed={docLang === "vi"} onClick={() => setDocLang("vi")}>Tiếng Việt</button><button aria-pressed={docLang === "en"} onClick={() => setDocLang("en")}>English</button></div></div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" onClick={() => dl("docx")}><Download className="h-4 w-4" />Word</button>
          <button className="btn-secondary" onClick={() => dl("pdf")}><Download className="h-4 w-4" />PDF</button>
          <button className="btn-secondary" onClick={() => dl("md")}><Download className="h-4 w-4" />Markdown</button>
        </div>
        {!m.built_at && <p className="mt-3 text-xs text-ink-600">{t("models.share.docNotBuilt")}</p>}
      </section>

      <section className="panel p-6">
        <div className="mb-1 flex items-center gap-2"><Share2 className="h-5 w-5 text-ink-400" /><h3 className="font-display text-lg font-semibold">{t("models.share.biTitle")}</h3></div>
        <p className="mb-4 text-sm leading-6 text-ink-600">{t("models.share.biHint")}</p>
        {conn.data && (
          <dl className="space-y-3 text-sm">
            {[["models.share.dataset", conn.data.qualified], ["models.share.connection", conn.data.dsn || "—"]].map(([k, val]) => (
              <div key={k}>
                <dt className="mb-1 text-[13px] font-medium text-ink-700">{t(k)}</dt>
                <dd className="flex items-center gap-2 rounded-lg bg-paper px-3 py-2 font-mono text-[13px] text-ink-900"><span className="flex-1 break-all">{val}</span><button className="btn-ghost btn-sm" onClick={() => copy(val)}><Copy className="h-3.5 w-3.5" /></button></dd>
              </div>
            ))}
            {!conn.data.built && <p className="text-xs text-marker-700">{t("models.share.biNotBuilt")}</p>}
            <p className="text-xs leading-5 text-ink-600">{conn.data.reader_role ? t("models.share.readerRole", { role: conn.data.reader_role }) : t("models.share.noReaderRole")}</p>
            <details className="text-sm"><summary className="cursor-pointer text-ink-700">{t("models.share.sampleSql")}</summary>
              <pre className="mt-2 overflow-auto rounded-lg bg-ink-950 p-3 text-xs text-ink-100">{conn.data.sample_sql}</pre></details>
          </dl>
        )}
      </section>

      <section className="panel p-6 lg:col-span-2">
        <div className="flex flex-wrap items-center gap-4">
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-lg font-semibold">{t("models.share.askTitle")}</h3>
            <p className="mt-1 text-sm leading-6 text-ink-600">{t("models.share.askHint")}</p>
          </div>
          <button className="btn-secondary" disabled={!m.build.data_table_id} onClick={() => nav(`/w/${wsId}/tables/${m.build.data_table_id}`)}><Table2 className="h-4 w-4" />{t("models.share.explore")}</button>
          <button className="btn-primary" disabled={!m.build.data_table_id} onClick={() => nav(`/w/${wsId}/chat?q=${encodeURIComponent(t("models.share.askSample", { name: m.name }))}`)}><MessageCircle className="h-4 w-4" />{t("models.share.ask")}</button>
        </div>
      </section>
      <Advanced><DangerZone m={m} /></Advanced>
    </div>
  );
}

function DangerZone({ m }: { m: DataModelDetail }) {
  const { t } = useI18n();
  const { wsId, can } = useWorkspace();
  const nav = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const del = useMutation({ mutationFn: () => api.deleteModel(wsId, m.id), onSuccess: () => nav(`/w/${wsId}/models`), onError: toast.err });
  if (!can("editor")) return null;
  return (
    <section className="panel border-alert-500/20 p-6 lg:col-span-2">
      <div className="flex flex-wrap items-center gap-3"><Cog className="h-5 w-5 text-ink-400" /><span className="flex-1 text-sm text-ink-700">{t("models.share.deleteHint")}</span>
        <button className="btn-danger" onClick={() => setOpen(true)}>{t("models.share.delete")}</button></div>
      <Confirm open={open} onClose={() => setOpen(false)} onConfirm={() => del.mutate()} danger busy={del.isPending} title={t("models.share.delete")} message={t("models.share.deleteConfirm")} />
    </section>
  );
}

function RenameModal({ m, open, onClose, onSaved }: { m: DataModelDetail; open: boolean; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const [name, setName] = useState(m.name);
  const [desc, setDesc] = useState(m.description || "");
  useEffect(() => { if (open) { setName(m.name); setDesc(m.description || ""); } }, [open, m.name, m.description]);
  const mut = useMutation({ mutationFn: () => api.updateModel(wsId, m.id, { name, description: desc }), onSuccess: () => { onSaved(); onClose(); }, onError: toast.err });
  return (
    <Modal open={open} onClose={onClose} title={t("models.rename.title")}
      footer={<><button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button><button className="btn-primary" disabled={!name.trim() || mut.isPending} onClick={() => mut.mutate()}>{t("action.save")}</button></>}>
      <div className="space-y-3">
        <Field label={t("models.create.name")}><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label={t("models.rename.description")} hint={t("models.rename.descriptionHint")}><textarea className="input" rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}
