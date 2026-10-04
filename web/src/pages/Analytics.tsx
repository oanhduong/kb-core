// Phân tích số liệu: hỏi bằng ngôn ngữ tự nhiên -> SQL (hiển thị để kiểm tra) + kết quả + chart; SQL editor read-only
// trên DuckDB; truy vấn đã lưu; export CSV/XLSX.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { BookmarkPlus, Download, Play, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DataGrid } from "@/components/DataGrid";
import { Badge, Confirm, CopyButton, ErrorBox, Field, Loading, Modal, PageHeader, Spinner, useToast } from "@/components/ui";
import { ChartView } from "@/components/VegaChart";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import type { Chart, QueryResult } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

interface Outcome { sql: string; result: QueryResult | null; chart: Chart | null; reason?: string; assumptions?: string[]; error?: string | null; tables_used?: string[] }

export function AnalyticsPage() {
  const { wsId, can } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const qc = useQueryClient();
  const [question, setQuestion] = useState(params.get("q") || "");
  const [sql, setSql] = useState(params.get("sql") || "");
  const [out, setOut] = useState<Outcome | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [delId, setDelId] = useState<string | null>(null);
  const [view, setView] = useState<"chart" | "table">("chart");
  const tables = useQuery({ queryKey: ["tables", wsId], queryFn: () => api.tables(wsId) });
  const saved = useQuery({ queryKey: ["saved", wsId], queryFn: () => api.savedQueries(wsId) });

  const ask = useMutation({
    mutationFn: (q: string) => api.askData(wsId, { question: q }),
    onSuccess: (r) => { setOut({ sql: r.sql || "", result: r.result, chart: r.chart, reason: r.reason, assumptions: r.assumptions, error: r.error, tables_used: r.tables_used }); if (r.sql) setSql(r.sql); setView("chart"); },
    onError: toast.err,
  });
  const run = useMutation({
    mutationFn: (s: string) => api.runSql(wsId, { sql: s }),
    onSuccess: (r, s) => { setOut({ sql: s, result: r.result, chart: r.chart }); setView("chart"); },
    onError: toast.err,
  });
  const runSaved = useMutation({
    mutationFn: (id: string) => api.runSaved(wsId, id),
    onSuccess: (r, id) => { const sq = saved.data?.find((x) => x.id === id); setOut({ sql: r.result.sql, result: r.result, chart: r.chart }); setSql(sq?.sql || r.result.sql); if (sq?.question) setQuestion(sq.question); setView("chart"); },
    onError: toast.err,
  });
  const del = useMutation({ mutationFn: (id: string) => api.deleteSaved(wsId, id), onSuccess: () => { setDelId(null); void qc.invalidateQueries({ queryKey: ["saved", wsId] }); }, onError: toast.err });

  useEffect(() => {
    const q = params.get("q"), s = params.get("sql");
    if (q) ask.mutate(q);
    else if (s) run.mutate(s);
    if (q || s) { params.delete("q"); params.delete("sql"); setParams(params, { replace: true }); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasChart = out?.chart && (out.chart.vega_lite || out.chart.type === "kpi");
  const busy = ask.isPending || run.isPending || runSaved.isPending;

  return (
    <Page wide>
      <PageHeader title="Phân tích số liệu" subtitle="Đặt câu hỏi bằng tiếng Việt; hệ thống sinh SQL (chỉ đọc) trên các bảng đã trích, chạy và vẽ biểu đồ. Bạn luôn xem được SQL để kiểm chứng." />
      <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
        <div className="space-y-4">
          <form className="card flex items-center gap-2 p-2 pl-4" onSubmit={(e) => { e.preventDefault(); if (question.trim()) ask.mutate(question.trim()); }}>
            <Sparkles className="h-5 w-5 text-brand-600" />
            <input className="flex-1 bg-transparent py-2 text-[15px] outline-none" placeholder="Ví dụ: Tổng tồn kho theo ngành hàng? Top 10 SKU giá cao nhất?" value={question} onChange={(e) => setQuestion(e.target.value)} />
            <button className="btn-primary" disabled={!question.trim() || busy}>{ask.isPending ? <Spinner className="text-white" /> : <Sparkles className="h-4 w-4" />} Phân tích</button>
          </form>

          <div className="card">
            <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
              <span className="text-sm font-semibold text-slate-800">SQL (DuckDB, chỉ đọc)</span>
              <div className="flex gap-1">
                {sql && <CopyButton text={sql} />}
                {out?.sql && can("editor") && <button className="btn-ghost btn-sm" onClick={() => setSaveOpen(true)}><BookmarkPlus className="h-3.5 w-3.5" /> Lưu</button>}
                <button className="btn-primary btn-sm" disabled={!sql.trim() || busy} onClick={() => run.mutate(sql)}>{run.isPending ? <Spinner className="text-white" /> : <Play className="h-3.5 w-3.5" />} Chạy</button>
              </div>
            </div>
            <textarea className="w-full resize-y bg-slate-900 p-3 font-mono text-[13px] leading-5 text-slate-100 outline-none" rows={6} spellCheck={false} value={sql} onChange={(e) => setSql(e.target.value)}
              onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && sql.trim()) run.mutate(sql); }} placeholder={"SELECT ... FROM <tên_bảng>  (⌘/Ctrl+Enter để chạy)"} />
          </div>

          {out && (
            <div className="card">
              <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
                <span className="text-sm font-semibold text-slate-800">Kết quả</span>
                {out.result && <Badge>{out.result.row_count} dòng{out.result.truncated ? " (đã cắt)" : ""} · {out.result.elapsed_ms} ms</Badge>}
                {out.tables_used?.map((t) => <Badge key={t} tone="brand">{t}</Badge>)}
                <span className="flex-1" />
                {hasChart && out.result && out.result.rows.length > 0 && (
                  <div className="flex rounded-lg border border-slate-300 p-0.5 text-xs"><button className={clsx("rounded-md px-2 py-1", view === "chart" ? "bg-brand-600 text-white" : "text-slate-600")} onClick={() => setView("chart")}>Biểu đồ</button><button className={clsx("rounded-md px-2 py-1", view === "table" ? "bg-brand-600 text-white" : "text-slate-600")} onClick={() => setView("table")}>Bảng</button></div>
                )}
                {out.result && out.result.rows.length > 0 && <>
                  <button className="btn-secondary btn-sm" onClick={() => api.exportQuery(wsId, out.sql, "xlsx").catch(toast.err)}><Download className="h-3.5 w-3.5" /> XLSX</button>
                  <button className="btn-secondary btn-sm" onClick={() => api.exportQuery(wsId, out.sql, "csv").catch(toast.err)}><Download className="h-3.5 w-3.5" /> CSV</button>
                </>}
              </div>
              <div className="p-3">
                {out.error && <ErrorBox error={out.error} />}
                {out.reason && <p className="mb-3 text-sm text-slate-600">{out.reason}</p>}
                {out.assumptions && out.assumptions.length > 0 && <ul className="mb-3 list-disc pl-5 text-xs text-slate-500">{out.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>}
                {out.result && (hasChart && view === "chart" ? <ChartView chart={out.chart} height={320} /> : <DataGrid columns={out.result.columns} rows={out.result.rows} maxHeight={520} />)}
                {out.result && out.result.rows.length === 0 && <div className="text-sm text-slate-500">Không có dòng nào.</div>}
              </div>
            </div>
          )}
          {!out && !busy && <div className="card p-8 text-center text-sm text-slate-500">Đặt câu hỏi hoặc viết SQL để bắt đầu. Danh mục bảng ở bên phải — bấm vào tên bảng để chèn vào SQL.</div>}
          {busy && !out && <Loading label="Đang phân tích…" />}
        </div>

        <aside className="space-y-4">
          <section className="card">
            <div className="border-b border-slate-100 px-3 py-2 text-sm font-semibold text-slate-800">Bảng có thể truy vấn</div>
            <ul className="max-h-80 divide-y divide-slate-100 overflow-y-auto text-sm">
              {tables.data?.length === 0 && <li className="px-3 py-3 text-xs text-slate-500">Chưa có bảng nào — tải Excel/CSV lên trước.</li>}
              {tables.data?.map((t) => (
                <li key={t.id} className="px-3 py-2">
                  <button className="w-full text-left" onClick={() => setSql((s) => (s ? `${s}\n` : "") + `SELECT * FROM ${t.name} LIMIT 100`)} title={t.description || ""}>
                    <div className="truncate font-mono text-xs font-medium text-slate-800">{t.name}</div>
                    <div className="truncate text-xs text-slate-500">{t.title} · {t.row_count} dòng</div>
                  </button>
                  <div className="mt-1 flex flex-wrap gap-1">{t.columns.slice(0, 6).map((c) => <button key={c.name} className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[10px] text-slate-600 hover:bg-brand-50" onClick={() => setSql((s) => s + c.name)}>{c.name}</button>)}{t.columns.length > 6 && <span className="text-[10px] text-slate-400">+{t.columns.length - 6}</span>}</div>
                </li>
              ))}
            </ul>
          </section>
          <section className="card">
            <div className="border-b border-slate-100 px-3 py-2 text-sm font-semibold text-slate-800">Truy vấn đã lưu</div>
            <ul className="max-h-72 divide-y divide-slate-100 overflow-y-auto text-sm">
              {saved.data?.length === 0 && <li className="px-3 py-3 text-xs text-slate-500">Chưa có. Chạy truy vấn rồi bấm Lưu.</li>}
              {saved.data?.map((s) => (
                <li key={s.id} className="group flex items-center gap-1 px-3 py-2">
                  <button className="min-w-0 flex-1 text-left" onClick={() => runSaved.mutate(s.id)}><div className="truncate font-medium text-slate-800">{s.title}</div>{s.question && <div className="truncate text-xs text-slate-500">{s.question}</div>}</button>
                  {can("editor") && <button className="btn-ghost btn-sm invisible group-hover:visible" onClick={() => setDelId(s.id)}><Trash2 className="h-3.5 w-3.5" /></button>}
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
      <SaveModal open={saveOpen} onClose={() => setSaveOpen(false)} sql={out?.sql || sql} question={question} chart={out?.chart} />
      <Confirm open={!!delId} onClose={() => setDelId(null)} onConfirm={() => delId && del.mutate(delId)} title="Xoá truy vấn đã lưu" message="Truy vấn sẽ bị xoá khỏi danh sách chung của workspace." danger />
    </Page>
  );
}

function SaveModal({ open, onClose, sql, question, chart }: { open: boolean; onClose: () => void; sql: string; question: string; chart: Chart | null | undefined }) {
  const { wsId } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState(question || "");
  useEffect(() => { if (open) setTitle(question || ""); }, [open, question]);
  const mut = useMutation({
    mutationFn: () => api.saveQuery(wsId, { title: title.trim(), sql, question: question || undefined, chart_spec: chart?.type ? { type: chart.type, title: chart.title } : undefined }),
    onSuccess: () => { toast.ok("Đã lưu truy vấn"); void qc.invalidateQueries({ queryKey: ["saved", wsId] }); onClose(); }, onError: toast.err,
  });
  return (
    <Modal open={open} onClose={onClose} title="Lưu truy vấn" footer={<><button className="btn-secondary" onClick={onClose}>Huỷ</button><button className="btn-primary" disabled={!title.trim() || mut.isPending} onClick={() => mut.mutate()}>Lưu</button></>}>
      <Field label="Tên"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">{sql}</pre>
    </Modal>
  );
}
