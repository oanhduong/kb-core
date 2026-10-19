// Khám phá số liệu: một trang, hai chế độ.
//  - Hỏi: câu hỏi tự nhiên -> câu trả lời có giải thích (lý do, giả định), biểu đồ/bảng, xuất Excel/CSV, lưu câu hỏi.
//    SQL, thời gian chạy, bảng đã dùng là chi tiết kỹ thuật -> chỉ hiện khi bật usePrefs().advanced ("Sửa truy vấn").
//  - Xem bảng: danh sách bảng (tên thân thiện + mô tả) -> cột (tên gốc, loại bằng lời, độ đầy, giá trị khác nhau, khoảng)
//    và dữ liệu, xuất file. Tên SQL chỉ hiện ở chế độ kỹ thuật.
// Hỗ trợ ?q=<câu hỏi> và ?sql=<truy vấn> (tự chạy rồi xoá khỏi URL), ?view=tables&table=<id> để mở thẳng chế độ xem bảng.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowRight, BarChart3, Bookmark, BookmarkPlus, ChevronDown, Code2, Download, Lightbulb, Link2, MessageSquareText, Play, Search,
  Sparkles, Table2, Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { columnKind, columnLabel, DataGrid, type ColumnKind } from "@/components/DataGrid";
import {
  Badge, Confirm, CopyButton, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Pagination, Segmented, Spinner, Tabs, useToast,
} from "@/components/ui";
import { TableLinksPane } from "@/components/TableLinks";
import { ChartView } from "@/components/VegaChart";
import { useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api, download } from "@/lib/api";
import { fmtCell, fmtMs, fmtNumber, fmtPct } from "@/lib/format";
import type { Chart, QueryResult, TableColumn, TableInfo } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { Advanced, usePrefs } from "@/state/prefs";

export type ExploreMode = "ask" | "browse" | "links";

interface Outcome {
  title: string;
  sql: string;
  result: QueryResult | null;
  chart: Chart | null;
  reason?: string;
  assumptions?: string[];
  error?: string | null;
  tables_used?: string[];
}

export function AnalyticsPage() {
  const [params] = useSearchParams();
  const [initial] = useState(() => ({
    mode: (params.get("view") === "links" ? "links" : params.get("view") === "tables" && !params.get("q") && !params.get("sql") ? "browse" : "ask") as ExploreMode,
    table: params.get("table"),
  }));
  const [tableId, setTableId] = useState<string | null>(initial.table);
  return <ExploreView initialMode={initial.mode} tableId={tableId} onSelectTable={setTableId} />;
}

/** Trang "Khám phá số liệu" dùng chung cho /analytics và /tables(/:tableId). */
export function ExploreView({ initialMode = "ask", tableId, onSelectTable }: {
  initialMode?: ExploreMode;
  tableId: string | null;
  onSelectTable: (id: string) => void;
}) {
  const t = useT();
  const { wsId, can } = useWorkspace();
  const { advanced } = usePrefs();
  const toast = useToast();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [mode, setMode] = useState<ExploreMode>(initialMode);
  const [question, setQuestion] = useState("");
  const [sql, setSql] = useState("");
  const [sqlOpen, setSqlOpen] = useState(false);
  const [out, setOut] = useState<Outcome | null>(null);
  const [view, setView] = useState<"chart" | "table">("chart");
  const [saveOpen, setSaveOpen] = useState(false);
  const [delId, setDelId] = useState<string | null>(null);

  const tables = useQuery({ queryKey: ["tables", wsId], queryFn: () => api.tables(wsId) });
  const saved = useQuery({ queryKey: ["saved", wsId], queryFn: () => api.savedQueries(wsId) });

  // Tên cột SQL -> tên gốc trong file, để bảng kết quả hiển thị tên quen thuộc.
  const labels = useMemo(() => {
    const m: Record<string, string> = {};
    for (const tb of tables.data || []) for (const c of tb.columns) if (c.original && c.original !== c.name && !m[c.name]) m[c.name] = c.original;
    return m;
  }, [tables.data]);
  const suggestions = useMemo(() => {
    const seen = new Set<string>();
    const xs: string[] = [];
    // lấy xen kẽ giữa các bảng để gợi ý đa dạng
    const lists = (tables.data || []).map((tb) => tb.suggested_questions || []);
    for (let i = 0; xs.length < 6 && lists.some((l) => l.length > i); i++) {
      for (const l of lists) {
        const q = l[i];
        if (q && !seen.has(q) && xs.length < 6) { seen.add(q); xs.push(q); }
      }
    }
    return xs;
  }, [tables.data]);

  const ask = useMutation({
    mutationFn: (q: string) => api.askData(wsId, { question: q }),
    onSuccess: (r, q) => {
      setOut({ title: q, sql: r.sql || "", result: r.result, chart: r.chart, reason: r.reason, assumptions: r.assumptions, error: r.error, tables_used: r.tables_used });
      if (r.sql) setSql(r.sql);
      setView("chart");
    },
    onError: toast.err,
  });
  const run = useMutation({
    mutationFn: (v: { sql: string; title: string }) => api.runSql(wsId, { sql: v.sql }),
    onSuccess: (r, v) => { setOut({ title: v.title, sql: v.sql, result: r.result, chart: r.chart }); setView("chart"); },
    onError: toast.err,
  });
  const runSaved = useMutation({
    mutationFn: (id: string) => api.runSaved(wsId, id),
    onSuccess: (r, id) => {
      const sq = saved.data?.find((x) => x.id === id);
      setOut({ title: sq?.title || sq?.question || t("explore.result.savedTitle"), sql: r.result.sql, result: r.result, chart: r.chart });
      setSql(sq?.sql || r.result.sql);
      setQuestion(sq?.question || "");
      setView("chart");
    },
    onError: toast.err,
  });
  const del = useMutation({
    mutationFn: (id: string) => api.deleteSaved(wsId, id),
    onSuccess: () => { setDelId(null); toast.ok(t("explore.saved.deleted")); void qc.invalidateQueries({ queryKey: ["saved", wsId] }); },
    onError: toast.err,
  });

  const askQuestion = (q: string) => {
    const s = q.trim();
    if (!s) return;
    setMode("ask");
    setQuestion(s);
    ask.mutate(s);
  };
  const runQuery = (s: string, title?: string) => {
    if (!s.trim()) return;
    setMode("ask");
    setSql(s);
    setSqlOpen(true);
    run.mutate({ sql: s, title: title || t("explore.result.queryTitle") });
  };

  // ?q= / ?sql= : chạy một lần rồi xoá khỏi URL.
  const handled = useRef<string | null>(null);
  useEffect(() => {
    const q = params.get("q"), s = params.get("sql");
    if (!q && !s) { handled.current = null; return; }
    const key = `${q}|${s}`;
    if (handled.current !== key) {
      handled.current = key;
      if (q) askQuestion(q);
      else if (s) runQuery(s);
    }
    const next = new URLSearchParams(params);
    next.delete("q");
    next.delete("sql");
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const busy = ask.isPending || run.isPending || runSaved.isPending;
  const noTables = tables.data && tables.data.length === 0;

  return (
    <Page wide>
      <PageHeader title={t("explore.title")} subtitle={t(`explore.subtitle.${mode}`)}
        actions={!noTables && (
          <Segmented value={mode} onChange={setMode} className="text-sm [&>button]:px-3.5 [&>button]:py-1.5"
            options={[
              { key: "ask", label: <span className="inline-flex items-center gap-1.5"><MessageSquareText className="h-4 w-4" />{t("explore.mode.ask")}</span> },
              { key: "browse", label: <span className="inline-flex items-center gap-1.5"><Table2 className="h-4 w-4" />{t("explore.mode.browse")}</span> },
              ...((tables.data?.length ?? 0) > 1 ? [{ key: "links" as const, label: <span className="inline-flex items-center gap-1.5"><Link2 className="h-4 w-4" />{t("explore.mode.links")}</span> }] : []),
            ]} />
        )} />

      {tables.isLoading && <Loading />}
      {tables.error && <ErrorBox error={tables.error} retry={() => tables.refetch()} />}
      {noTables && (
        <div className="panel">
          <Empty icon={<Table2 />} title={t("explore.noTables.title")} hint={t("explore.noTables.hint")}
            action={<Link to={`/w/${wsId}/documents`} className="btn-primary">{t("explore.noTables.action")}</Link>} />
        </div>
      )}

      {tables.data && tables.data.length > 0 && mode === "links" && <TableLinksPane />}

      {tables.data && tables.data.length > 0 && mode === "browse" && (
        <BrowsePane tables={tables.data} tableId={tableId} onSelect={onSelectTable} onAsk={askQuestion}
          onOpenSql={(s, title) => runQuery(s, title)} />
      )}

      {tables.data && tables.data.length > 0 && mode === "ask" && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
          <div className="min-w-0 space-y-5">
            <form className="panel p-3 transition focus-within:border-brand-300 focus-within:shadow-focus sm:p-4"
              onSubmit={(e) => { e.preventDefault(); askQuestion(question); }}>
              <label htmlFor="explore-question" className="sr-only">{t("explore.ask.label")}</label>
              <div className="flex items-start gap-3 px-1">
                <Sparkles className="mt-3 h-5 w-5 shrink-0 text-brand-600" aria-hidden />
                <textarea id="explore-question" rows={2} value={question} onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); askQuestion(question); } }}
                  placeholder={t("explore.ask.placeholder")}
                  className="min-h-[60px] flex-1 resize-none bg-transparent py-2.5 text-[17px] leading-7 text-ink-900 placeholder:text-ink-400 focus:outline-none focus-visible:shadow-none" />
              </div>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-3 border-t border-ink-100 px-1 pt-3">
                <span className="text-xs text-ink-600">{t("explore.ask.hint")}</span>
                <button className="btn-primary" disabled={!question.trim() || busy}>
                  {ask.isPending ? <Spinner className="text-white" /> : <Sparkles className="h-4 w-4" />} {t("explore.ask.submit")}
                </button>
              </div>
            </form>

            {suggestions.length > 0 && (
              <div>
                <div className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink-700"><Lightbulb className="h-4 w-4 text-ink-400" />{t("explore.ask.suggested")}</div>
                <div className="flex flex-wrap gap-2">
                  {suggestions.map((s) => (
                    <button key={s} type="button" disabled={busy} onClick={() => askQuestion(s)}
                      className="rounded-full border border-ink-100 bg-white px-3.5 py-1.5 text-left text-sm text-ink-800 transition hover:border-brand-300 hover:text-brand-700 disabled:opacity-50">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {advanced && (
              <section className="panel overflow-hidden">
                <button type="button" className="flex w-full items-center gap-3 px-5 py-3.5 text-left" aria-expanded={sqlOpen} onClick={() => setSqlOpen((v) => !v)}>
                  <Code2 className="h-[18px] w-[18px] text-ink-600" />
                  <span className="flex-1">
                    <span className="block text-sm font-semibold text-ink-900">{t("explore.sql.title")}</span>
                    <span className="block text-xs text-ink-600">{t("explore.sql.hint")}</span>
                  </span>
                  <ChevronDown className={clsx("h-4 w-4 text-ink-400 transition", sqlOpen && "rotate-180")} />
                </button>
                {sqlOpen && (
                  <div className="border-t border-ink-100">
                    <label htmlFor="explore-sql" className="sr-only">{t("explore.sql.title")}</label>
                    <textarea id="explore-sql" rows={7} spellCheck={false} value={sql} onChange={(e) => setSql(e.target.value)}
                      onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") runQuery(sql); }}
                      placeholder={t("explore.sql.placeholder")}
                      className="block w-full resize-y bg-ink-950 p-4 font-mono text-[13px] leading-5 text-ink-100 placeholder:text-ink-400 focus:outline-none" />
                    <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                      <span className="text-xs text-ink-600">{t("explore.sql.shortcut")}</span>
                      <div className="flex gap-1.5">
                        {sql && <CopyButton text={sql} />}
                        <button className="btn-ink btn-sm" disabled={!sql.trim() || busy} onClick={() => runQuery(sql)}>
                          {run.isPending ? <Spinner className="text-white" /> : <Play className="h-3.5 w-3.5" />} {t("explore.sql.run")}
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </section>
            )}

            {busy && (
              <div className="panel"><Loading label={t(ask.isPending ? "explore.ask.working" : "explore.sql.working")} /></div>
            )}
            {!busy && out && (
              <ResultPanel out={out} view={view} setView={setView} labels={labels} canSave={can("editor")} onSave={() => setSaveOpen(true)} />
            )}
            {!busy && !out && (
              <div className="panel">
                <Empty icon={<BarChart3 />} title={t("explore.empty.title")} hint={t("explore.empty.hint")} />
              </div>
            )}
          </div>

          <aside className="space-y-5">
            <section className="panel p-2">
              <h2 className="px-3 pb-1 pt-2 font-display text-base font-semibold text-ink-900">{t("explore.saved.title")}</h2>
              {saved.isLoading && <div className="px-3 py-3"><Spinner /></div>}
              {saved.data?.length === 0 && <p className="px-3 pb-3 pt-1 text-sm leading-6 text-ink-600">{t("explore.saved.empty")}</p>}
              <ul className="max-h-[420px] overflow-y-auto">
                {saved.data?.map((s) => (
                  <li key={s.id} className="group relative">
                    <button type="button" className="flex w-full items-start gap-2.5 rounded-xl px-3 py-2.5 pr-10 text-left hover:bg-paper" onClick={() => runSaved.mutate(s.id)} disabled={busy}>
                      <Bookmark className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" />
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-ink-900">{s.title}</span>
                        {s.question && s.question !== s.title && <span className="block truncate text-xs text-ink-600">{s.question}</span>}
                      </span>
                    </button>
                    {can("editor") && (
                      <button type="button" className="btn-ghost btn-sm absolute right-1.5 top-2 opacity-0 focus:opacity-100 group-hover:opacity-100"
                        aria-label={t("explore.saved.delete")} title={t("explore.saved.delete")} onClick={() => setDelId(s.id)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
            <section className="well p-4">
              <div className="text-sm font-medium text-ink-900">{t("explore.side.dataTitle")}</div>
              <p className="mt-1 text-sm leading-6 text-ink-600">{t("explore.side.dataHint", { n: tables.data.length })}</p>
              <button type="button" className="btn-secondary btn-sm mt-3" onClick={() => setMode("browse")}>
                <Table2 className="h-3.5 w-3.5" /> {t("explore.side.browse")}
              </button>
            </section>
          </aside>
        </div>
      )}

      <SaveModal open={saveOpen} onClose={() => setSaveOpen(false)} sql={out?.sql || sql} question={question || out?.title || ""} chart={out?.chart} />
      <Confirm open={!!delId} onClose={() => setDelId(null)} onConfirm={() => delId && del.mutate(delId)} danger busy={del.isPending}
        title={t("explore.saved.deleteTitle")} message={t("explore.saved.deleteMessage")} confirmLabel={t("action.delete")} />
    </Page>
  );
}

// ---------------------------------------------------------------- kết quả câu hỏi

function ResultPanel({ out, view, setView, labels, canSave, onSave }: {
  out: Outcome;
  view: "chart" | "table";
  setView: (v: "chart" | "table") => void;
  labels: Record<string, string>;
  canSave: boolean;
  onSave: () => void;
}) {
  const t = useT();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const r = out.result;
  const hasRows = !!r && r.rows.length > 0;
  const hasChart = !!out.chart && (!!out.chart.vega_lite || out.chart.type === "kpi");
  const showChart = hasChart && hasRows && view === "chart";
  const exp = (fmt: "xlsx" | "csv") => api.exportQuery(wsId, out.sql, fmt).catch(toast.err);
  return (
    <section className="panel animate-rise overflow-hidden" aria-live="polite">
      <header className="flex flex-wrap items-start gap-x-4 gap-y-3 border-b border-ink-100 px-5 py-4 sm:px-6">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-xl font-semibold leading-snug text-ink-900">{out.title}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-600">
            {r && <span>{t("rows", { n: fmtNumber(r.row_count) })}</span>}
            {r?.truncated && <span>{t("explore.result.truncated")}</span>}
            <Advanced>
              {r && <span className="tabular-nums">{fmtMs(r.elapsed_ms)}</span>}
              {out.tables_used?.map((tb) => <Badge key={tb} tone="brand" className="font-mono">{tb}</Badge>)}
            </Advanced>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {hasChart && hasRows && (
            <Segmented value={view} onChange={setView} options={[
              { key: "chart", label: <span className="inline-flex items-center gap-1"><BarChart3 className="h-3.5 w-3.5" />{t("explore.result.chart")}</span> },
              { key: "table", label: <span className="inline-flex items-center gap-1"><Table2 className="h-3.5 w-3.5" />{t("explore.result.table")}</span> },
            ]} />
          )}
          {hasRows && out.sql && <>
            <button type="button" className="btn-secondary btn-sm" onClick={() => exp("xlsx")} title={t("explore.export.xlsxHint")}><Download className="h-3.5 w-3.5" /> {t("explore.export.xlsx")}</button>
            <button type="button" className="btn-secondary btn-sm" onClick={() => exp("csv")} title={t("explore.export.csvHint")}><Download className="h-3.5 w-3.5" /> {t("explore.export.csv")}</button>
          </>}
          {canSave && out.sql && !out.error && (
            <button type="button" className="btn-secondary btn-sm" onClick={onSave}><BookmarkPlus className="h-3.5 w-3.5" /> {t("explore.saved.save")}</button>
          )}
        </div>
      </header>
      <div className="space-y-5 px-5 py-5 sm:px-6">
        {out.reason && <p className="max-w-3xl text-[15px] leading-7 text-ink-800">{out.reason}</p>}
        {/* not_answerable: mã nội bộ, lý do dễ đọc đã nằm ở out.reason */}
        {out.error && out.error !== "not_answerable" && <ErrorBox error={t("explore.result.error", { error: out.error })} />}
        {r && (showChart
          ? <ChartView chart={out.chart} height={320} />
          : hasRows && <DataGrid columns={r.columns} rows={r.rows} maxHeight={520} labels={labels} total={r.row_count} />)}
        {r && !hasRows && !out.error && <p className="rounded-xl border border-dashed border-ink-100 px-4 py-6 text-center text-sm text-ink-600">{t("explore.result.noRows")}</p>}
        {out.assumptions && out.assumptions.length > 0 && (
          <div className="well p-4">
            <div className="text-sm font-medium text-ink-900">{t("explore.result.assumptions")}</div>
            <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm leading-6 text-ink-700 marker:text-ink-400">{out.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
          </div>
        )}
        <Advanced>
          {out.sql && (
            <details className="group rounded-xl border border-ink-100">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm font-medium text-ink-700">
                <Code2 className="h-4 w-4 text-ink-400" /> {t("explore.result.sql")}
                <ChevronDown className="ml-auto h-4 w-4 text-ink-400 transition group-open:rotate-180" />
              </summary>
              <div className="relative border-t border-ink-100">
                <pre className="overflow-x-auto bg-ink-950 p-4 font-mono text-[12.5px] leading-5 text-ink-100">{out.sql}</pre>
                <CopyButton text={out.sql} className="absolute right-2 top-2 bg-white/10 text-ink-100 hover:bg-white/20 hover:text-white" />
              </div>
            </details>
          )}
        </Advanced>
      </div>
    </section>
  );
}

function SaveModal({ open, onClose, sql, question, chart }: { open: boolean; onClose: () => void; sql: string; question: string; chart: Chart | null | undefined }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState(question || "");
  useEffect(() => { if (open) setTitle(question || ""); }, [open, question]);
  const mut = useMutation({
    mutationFn: () => api.saveQuery(wsId, { title: title.trim(), sql, question: question || undefined, chart_spec: chart?.type ? { type: chart.type, title: chart.title } : undefined }),
    onSuccess: () => { toast.ok(t("explore.saved.done")); void qc.invalidateQueries({ queryKey: ["saved", wsId] }); onClose(); },
    onError: toast.err,
  });
  return (
    <Modal open={open} onClose={onClose} title={t("explore.saved.modalTitle")}
      footer={<>
        <button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button>
        <button className="btn-primary" disabled={!title.trim() || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />} {t("explore.saved.save")}</button>
      </>}>
      <p className="mb-4 text-sm leading-6 text-ink-600">{t("explore.saved.modalHint")}</p>
      <Field label={t("explore.saved.name")}><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus /></Field>
      <Advanced><pre className="mt-4 max-h-40 overflow-auto rounded-xl bg-ink-950 p-3 font-mono text-xs text-ink-100">{sql}</pre></Advanced>
    </Modal>
  );
}

// ---------------------------------------------------------------- xem bảng

function BrowsePane({ tables, tableId, onSelect, onAsk, onOpenSql }: {
  tables: TableInfo[];
  tableId: string | null;
  onSelect: (id: string) => void;
  onAsk: (q: string) => void;
  onOpenSql: (sql: string, title: string) => void;
}) {
  const t = useT();
  const { advanced } = usePrefs();
  const [q, setQ] = useState("");
  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return tables;
    return tables.filter((tb) => `${tb.title} ${tb.name} ${tb.document_title} ${tb.description || ""} ${tb.columns.map((c) => c.original || c.name).join(" ")}`.toLowerCase().includes(s));
  }, [tables, q]);
  const selected = tableId && tables.some((tb) => tb.id === tableId) ? tableId : tables[0]?.id ?? null;
  return (
    <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
      <aside className="panel flex flex-col overflow-hidden lg:sticky lg:top-6 lg:max-h-[calc(100vh-150px)] lg:self-start">
        <div className="border-b border-ink-100 p-3">
          <label className="relative block">
            <span className="sr-only">{t("explore.browse.search")}</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
            <input className="input pl-9" placeholder={t("explore.browse.search")} value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
          <div className="mt-2 px-1 text-xs text-ink-600">{t("explore.browse.count", { n: list.length })}</div>
        </div>
        <ul className="max-h-[50vh] flex-1 overflow-y-auto p-1.5 lg:max-h-none">
          {list.length === 0 && <li className="px-3 py-6 text-center text-sm text-ink-600">{t("explore.browse.noMatch")}</li>}
          {list.map((tb) => {
            const on = selected === tb.id;
            return (
              <li key={tb.id}>
                <button type="button" aria-current={on ? "true" : undefined} onClick={() => onSelect(tb.id)}
                  className={clsx("relative w-full rounded-xl px-3 py-2.5 text-left transition", on ? "bg-brand-50" : "hover:bg-paper")}>
                  {on && <span className="absolute inset-y-2.5 left-0 w-[3px] rounded-full bg-brand-600" aria-hidden />}
                  <span className={clsx("block truncate text-sm font-medium", on ? "text-brand-800" : "text-ink-900")}>{tb.title}</span>
                  {tb.description && <span className="mt-0.5 line-clamp-2 block text-[13px] leading-5 text-ink-600">{tb.description}</span>}
                  <span className="mt-1 block truncate text-xs text-ink-400">{t("rows", { n: fmtNumber(tb.row_count) })}, {tb.document_title}</span>
                  {advanced && <span className="mt-1 block truncate font-mono text-[11px] text-ink-600">{tb.name}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      </aside>
      <div className="min-w-0">
        {selected
          ? <TableDetailView key={selected} tableId={selected} onAsk={onAsk} onOpenSql={onOpenSql} />
          : <div className="panel"><Empty icon={<Table2 />} title={t("explore.browse.pick")} /></div>}
      </div>
    </div>
  );
}

const KIND_KEY: Record<ColumnKind, string> = {
  number: "explore.kind.number", date: "explore.kind.date", text: "explore.kind.text", bool: "explore.kind.bool", other: "explore.kind.other",
};

function TableDetailView({ tableId, onAsk, onOpenSql }: { tableId: string; onAsk: (q: string) => void; onOpenSql: (sql: string, title: string) => void }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const [tab, setTab] = useState<"columns" | "rows">("columns");
  const [page, setPage] = useState(1);
  const PAGE = 100;
  const q = useQuery({ queryKey: ["table", wsId, tableId], queryFn: () => api.table(wsId, tableId) });
  const rows = useQuery({
    queryKey: ["table-rows", wsId, tableId, page],
    queryFn: () => api.tableRows(wsId, tableId, { limit: PAGE, offset: (page - 1) * PAGE }),
    enabled: tab === "rows",
  });
  const labels = useMemo(() => {
    const m: Record<string, string> = {};
    for (const c of q.data?.columns || []) m[c.name] = columnLabel(c);
    return m;
  }, [q.data]);
  if (q.isLoading) return <div className="panel"><Loading /></div>;
  if (q.error || !q.data) return <ErrorBox error={q.error || t("explore.table.missing")} retry={() => q.refetch()} />;
  const d = q.data;
  const exp = (fmt: "xlsx" | "csv") => download(api.exportTablePath(wsId, d.id, fmt), `${d.title || d.name}.${fmt}`).catch(toast.err);

  return (
    <div className="space-y-5">
      <section className="panel p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-2xl font-semibold leading-tight text-ink-900">{d.title}</h2>
            <p className="mt-1.5 text-sm text-ink-600">
              {t("explore.table.from")}{" "}
              <Link className="link" to={`/w/${wsId}/documents/${d.document_id}`}>{d.document_title}</Link>
              {d.source_location ? <span>, {d.source_location}</span> : null}
            </p>
            <Advanced><code className="mt-2 inline-block rounded-md bg-ink-100/60 px-1.5 py-0.5 font-mono text-xs text-ink-700">{d.name}</code></Advanced>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary btn-sm" onClick={() => exp("xlsx")}><Download className="h-3.5 w-3.5" /> {t("explore.export.xlsx")}</button>
            <button type="button" className="btn-secondary btn-sm" onClick={() => exp("csv")}><Download className="h-3.5 w-3.5" /> {t("explore.export.csv")}</button>
            <Advanced>
              <button type="button" className="btn-secondary btn-sm" onClick={() => onOpenSql(`SELECT * FROM ${d.name} LIMIT 100`, d.title)}><Code2 className="h-3.5 w-3.5" /> {t("explore.table.openSql")}</button>
            </Advanced>
          </div>
        </div>
        <dl className="mt-5 flex flex-wrap gap-x-10 gap-y-3">
          <div><dt className="text-[13px] text-ink-600">{t("explore.table.rows")}</dt><dd className="font-display text-2xl font-semibold tabular-nums text-ink-900">{fmtNumber(d.row_count)}</dd></div>
          <div><dt className="text-[13px] text-ink-600">{t("explore.table.columns")}</dt><dd className="font-display text-2xl font-semibold tabular-nums text-ink-900">{fmtNumber(d.columns.length)}</dd></div>
        </dl>
        {d.description && <p className="mt-5 max-w-3xl text-[15px] leading-7 text-ink-800">{d.description}</p>}
        {d.suggested_questions?.length > 0 && (
          <div className="mt-5">
            <div className="mb-2 text-sm font-medium text-ink-700">{t("explore.table.askAbout")}</div>
            <ul className="space-y-1">
              {d.suggested_questions.map((s) => (
                <li key={s}>
                  <button type="button" onClick={() => onAsk(s)} className="group flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-brand-700 hover:bg-brand-50">
                    <MessageSquareText className="h-4 w-4 shrink-0 text-brand-400" />
                    <span className="flex-1">{s}</span>
                    <ArrowRight className="h-4 w-4 shrink-0 opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <Tabs value={tab} onChange={setTab} tabs={[
        { key: "columns", label: t("explore.table.tabColumns"), count: d.columns.length },
        { key: "rows", label: t("explore.table.tabRows") },
      ]} />

      {tab === "columns" && (
        <div className="panel overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse">
            <thead>
              <tr>
                <th className="th rounded-tl-2xl pl-5">{t("explore.col.name")}</th>
                <th className="th">{t("explore.col.kind")}</th>
                <th className="th">{t("explore.col.filled")}</th>
                <th className="th text-right">{t("explore.col.distinct")}</th>
                <th className="th">{t("explore.col.range")}</th>
                <th className="th rounded-tr-2xl pr-5 text-right">{t("explore.col.avg")}</th>
              </tr>
            </thead>
            <tbody>
              {d.columns.map((c) => <ColumnRow key={c.name} c={c} stats={(d.stats || {})[c.name] || {}} rowCount={d.row_count} />)}
            </tbody>
          </table>
        </div>
      )}

      {tab === "rows" && (
        <div>
          {rows.isLoading && <div className="panel"><Loading /></div>}
          {rows.error && <ErrorBox error={rows.error} retry={() => rows.refetch()} />}
          {rows.data && <>
            <DataGrid columns={rows.data.columns} rows={rows.data.rows} maxRows={PAGE} maxHeight={560} compact labels={labels} />
            <Pagination page={page} pageSize={PAGE} total={d.row_count} onChange={setPage} />
          </>}
        </div>
      )}
    </div>
  );
}

function ColumnRow({ c, stats, rowCount }: { c: TableColumn; stats: Record<string, unknown>; rowCount: number }) {
  const t = useT();
  const kind = columnKind(c.type);
  const filled = typeof stats.count === "number" ? stats.count : undefined;
  const rate = filled !== undefined && rowCount ? filled / rowCount : undefined;
  const hasRange = stats.min !== undefined && stats.min !== null;
  return (
    <tr className="align-top transition-colors hover:bg-paper">
      <td className="td pl-5">
        <div className="font-medium text-ink-900">{columnLabel(c)}</div>
        {c.description && <div className="mt-0.5 max-w-sm text-xs leading-5 text-ink-600">{c.description}</div>}
        {!c.description && c.examples && c.examples.length > 0 && (
          <div className="mt-0.5 max-w-sm truncate text-xs text-ink-600">{t("explore.col.examples", { list: c.examples.slice(0, 3).map((x) => fmtCell(x, c.type)).join(", ") })}</div>
        )}
        <Advanced><div className="mt-1 font-mono text-[11px] text-ink-400">{c.name} · {c.type}</div></Advanced>
      </td>
      <td className="td"><Badge>{t(KIND_KEY[kind])}</Badge></td>
      <td className="td">
        {rate !== undefined ? (
          <span className="inline-flex items-center gap-2" title={t("explore.col.filledOf", { n: fmtNumber(filled), total: fmtNumber(rowCount) })}>
            <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-ink-100"><span className="absolute inset-y-0 left-0 rounded-full bg-brand-400" style={{ width: `${Math.max(2, Math.min(100, rate * 100))}%` }} /></span>
            <span className="text-xs tabular-nums text-ink-800">{fmtPct(rate)}</span>
          </span>
        ) : <span className="text-ink-300">—</span>}
      </td>
      <td className="td text-right tabular-nums">{stats.distinct !== undefined && stats.distinct !== null ? fmtNumber(stats.distinct) : <span className="text-ink-300">—</span>}</td>
      <td className="td whitespace-nowrap text-sm tabular-nums">
        {hasRange ? <>{fmtCell(stats.min, c.type)} <span className="text-ink-400">–</span> {fmtCell(stats.max, c.type)}</> : <span className="text-ink-300">—</span>}
      </td>
      <td className="td pr-5 text-right tabular-nums">{kind === "number" && typeof stats.avg === "number" ? fmtNumber(Math.round(stats.avg * 100) / 100) : <span className="text-ink-300">—</span>}</td>
    </tr>
  );
}
