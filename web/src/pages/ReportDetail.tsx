// Chi tiết báo cáo: render block (heading/markdown/chart/table/kpi/sources), chỉnh sửa (sửa text, sắp xếp, xoá, thêm
// heading/markdown), sinh lại, xuất PDF/DOCX/XLSX/HTML.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowDown, ArrowLeft, ArrowUp, Download, Pencil, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DataGrid } from "@/components/DataGrid";
import { Markdown } from "@/components/Markdown";
import { SourceDrawer } from "@/components/SourceDrawer";
import { Badge, Confirm, ErrorBox, Loading, PageHeader, Spinner, StatusBadge, useToast } from "@/components/ui";
import { ChartView } from "@/components/VegaChart";
import { Page } from "@/layout/AppShell";
import { api, download } from "@/lib/api";
import { fmtDate, fmtNumber, fmtRelative } from "@/lib/format";
import type { Citation, Report, ReportBlock } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

const newId = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

export function ReportDetailPage() {
  const { wsId, can } = useWorkspace();
  const { reportId = "" } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const rep = useQuery({ queryKey: ["report", wsId, reportId], queryFn: () => api.report(wsId, reportId), refetchInterval: (q) => (q.state.data?.status === "generating" ? 3000 : false) });
  const [editing, setEditing] = useState(false);
  const [blocks, setBlocks] = useState<ReportBlock[]>([]);
  const [title, setTitle] = useState("");
  const [cite, setCite] = useState<Citation | null>(null);
  const [regen, setRegen] = useState(false);
  useEffect(() => { if (rep.data && !editing) { setBlocks(rep.data.blocks || []); setTitle(rep.data.title); } }, [rep.data, editing]);

  const save = useMutation({
    mutationFn: () => api.updateReport(wsId, reportId, { title: title.trim() || undefined, blocks }),
    onSuccess: () => { toast.ok("Đã lưu báo cáo"); setEditing(false); void qc.invalidateQueries({ queryKey: ["report", wsId, reportId] }); void qc.invalidateQueries({ queryKey: ["reports", wsId] }); },
    onError: toast.err,
  });
  const regenMut = useMutation({ mutationFn: () => api.regenerateReport(wsId, reportId), onSuccess: () => { setRegen(false); toast.ok("Đang sinh lại (chạy nền)"); void qc.invalidateQueries({ queryKey: ["report", wsId, reportId] }); }, onError: toast.err });
  const exp = (fmt: string) => download(api.exportReportPath(wsId, reportId, fmt), `report.${fmt}`).catch(toast.err);

  if (rep.isLoading) return <Page><Loading /></Page>;
  if (rep.error || !rep.data) return <Page><ErrorBox error={rep.error || "Không có báo cáo"} /></Page>;
  const r: Report = rep.data;
  const move = (i: number, d: -1 | 1) => setBlocks((xs) => { const ys = [...xs]; const j = i + d; if (j < 0 || j >= ys.length) return xs; [ys[i], ys[j]] = [ys[j], ys[i]]; return ys; });
  const remove = (i: number) => setBlocks((xs) => xs.filter((_, k) => k !== i));
  const update = (i: number, patch: Partial<ReportBlock>) => setBlocks((xs) => xs.map((b, k) => (k === i ? { ...b, ...patch } : b)));
  const insert = (i: number, type: "heading" | "markdown") => setBlocks((xs) => { const ys = [...xs]; ys.splice(i + 1, 0, type === "heading" ? { id: newId(), type, text: "Mục mới", level: 2 } : { id: newId(), type, text: "" }); return ys; });

  return (
    <Page>
      <Link to={`/w/${wsId}/reports`} className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" /> Báo cáo</Link>
      <PageHeader
        title={editing ? <input className="input text-lg font-semibold" value={title} onChange={(e) => setTitle(e.target.value)} /> : <span className="flex items-center gap-2">{r.title}<StatusBadge status={r.status} /></span>}
        subtitle={<span>{r.block_count} block · cập nhật {fmtRelative(r.updated_at)}{r.prompt ? <> · <span className="italic">“{r.prompt.slice(0, 120)}{r.prompt.length > 120 ? "…" : ""}”</span></> : null}</span>}
        actions={editing ? (
          <><button className="btn-secondary" onClick={() => { setEditing(false); setBlocks(r.blocks || []); setTitle(r.title); }}><X className="h-4 w-4" /> Huỷ</button><button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? <Spinner className="text-white" /> : <Save className="h-4 w-4" />} Lưu</button></>
        ) : (
          <>
            {can("editor") && r.status !== "generating" && <button className="btn-secondary" onClick={() => setEditing(true)}><Pencil className="h-4 w-4" /> Chỉnh sửa</button>}
            {can("editor") && r.prompt && r.status !== "generating" && <button className="btn-secondary" onClick={() => setRegen(true)}><RefreshCw className="h-4 w-4" /> Sinh lại</button>}
            <div className="flex rounded-lg border border-slate-300 bg-white">
              {["pdf", "docx", "xlsx", "html"].map((f) => <button key={f} className="px-2.5 py-2 text-xs font-medium uppercase text-slate-700 hover:bg-slate-50 first:rounded-l-lg last:rounded-r-lg" onClick={() => exp(f)} disabled={r.status === "generating"}><Download className="mr-1 inline h-3.5 w-3.5" />{f}</button>)}
            </div>
          </>
        )} />

      {r.status === "generating" && (
        <div className="card mb-4 flex items-center gap-3 p-4 text-sm text-slate-700">
          <Spinner /> Đang sinh báo cáo: {r.job?.progress.total ? `mục ${Math.min(r.job.progress.done + 1, r.job.progress.total)}/${r.job.progress.total}` : "lập dàn ý…"}
          {r.job?.progress.percent !== null && r.job?.progress.percent !== undefined && <div className="ml-auto h-1.5 w-40 overflow-hidden rounded-full bg-slate-200"><div className="h-full bg-brand-500" style={{ width: `${r.job.progress.percent}%` }} /></div>}
        </div>
      )}
      {r.status === "error" && <ErrorBox error={r.job?.error || "Sinh báo cáo thất bại"} />}

      <article className="card px-8 py-6">
        {blocks.length === 0 && r.status !== "generating" && <p className="py-8 text-center text-sm text-slate-500">Báo cáo trống. Ghim câu trả lời từ trang Hỏi đáp (nút “Ghim vào báo cáo”) hoặc Chỉnh sửa để thêm nội dung.</p>}
        {blocks.map((b, i) => (
          <div key={b.id} className={clsx("group relative", editing && "rounded-lg border border-dashed border-transparent p-2 hover:border-slate-300")}>
            {editing && (
              <div className="absolute -right-2 top-1 z-10 hidden flex-col gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5 shadow group-hover:flex">
                <button className="btn-ghost btn-sm" onClick={() => move(i, -1)} disabled={i === 0}><ArrowUp className="h-3.5 w-3.5" /></button>
                <button className="btn-ghost btn-sm" onClick={() => move(i, 1)} disabled={i === blocks.length - 1}><ArrowDown className="h-3.5 w-3.5" /></button>
                <button className="btn-ghost btn-sm" title="Thêm mục" onClick={() => insert(i, "heading")}><Plus className="h-3.5 w-3.5" /> H</button>
                <button className="btn-ghost btn-sm" title="Thêm đoạn" onClick={() => insert(i, "markdown")}><Plus className="h-3.5 w-3.5" /> ¶</button>
                <button className="btn-ghost btn-sm text-red-600" onClick={() => remove(i)}><Trash2 className="h-3.5 w-3.5" /></button>
              </div>
            )}
            <BlockView b={b} editing={editing} onChange={(p) => update(i, p)} onCite={(c) => c && setCite(c)} />
          </div>
        ))}
        {editing && <div className="mt-4 flex gap-2"><button className="btn-secondary btn-sm" onClick={() => insert(blocks.length - 1, "heading")}><Plus className="h-3.5 w-3.5" /> Mục</button><button className="btn-secondary btn-sm" onClick={() => insert(blocks.length - 1, "markdown")}><Plus className="h-3.5 w-3.5" /> Đoạn văn</button></div>}
      </article>
      <SourceDrawer citation={cite} onClose={() => setCite(null)} />
      <Confirm open={regen} onClose={() => setRegen(false)} onConfirm={() => regenMut.mutate()} title="Sinh lại báo cáo" message="Nội dung hiện tại (kể cả phần bạn đã sửa/ghim) sẽ bị thay bằng bản sinh mới từ prompt gốc." busy={regenMut.isPending} />
    </Page>
  );
}

function BlockView({ b, editing, onChange, onCite }: { b: ReportBlock; editing: boolean; onChange: (p: Partial<ReportBlock>) => void; onCite: (c: Citation | null, n: number) => void }) {
  switch (b.type) {
    case "heading":
      if (editing) return <input className={clsx("input font-semibold", b.level === 1 ? "text-2xl" : "text-lg")} value={b.text || ""} onChange={(e) => onChange({ text: e.target.value })} />;
      return b.level === 1 ? <h1 className="mb-2 mt-2 text-2xl font-bold tracking-tight text-slate-900">{b.text}</h1> : <h2 className="mb-2 mt-6 border-b border-slate-200 pb-1 text-lg font-semibold text-brand-800">{b.text}</h2>;
    case "markdown":
      if (editing) return <textarea className="input font-mono text-[13px] leading-5" rows={Math.min(20, Math.max(3, (b.text || "").split("\n").length + 1))} value={b.text || ""} onChange={(e) => onChange({ text: e.target.value })} />;
      return (
        <div>
          {b.question && <div className="mb-1 text-xs text-slate-400">Câu hỏi: {b.question}</div>}
          <Markdown text={b.text || ""} citations={b.citations} onCite={onCite} />
          {b.citations && b.citations.length > 0 && <div className="mt-1 flex flex-wrap gap-1 text-xs text-slate-500">{b.citations.map((c) => <button key={c.n} className="hover:text-brand-700 hover:underline" onClick={() => onCite(c, c.n)}>[{c.n}] {c.label}</button>)}</div>}
        </div>
      );
    case "chart":
      return (
        <figure className="my-4">
          {editing ? <input className="input mb-2 text-sm" value={b.title || ""} onChange={(e) => onChange({ title: e.target.value })} placeholder="Tiêu đề biểu đồ" /> : null}
          <ChartView chart={b.chart} height={300} />
          {!editing && b.title && <figcaption className="mt-1 text-center text-xs text-slate-500">{b.title}{b.citation ? ` · ${b.citation}` : ""}</figcaption>}
        </figure>
      );
    case "kpi":
      return <div className="my-3 inline-flex flex-col rounded-xl border border-slate-200 px-5 py-3"><span className="text-xs uppercase tracking-wide text-slate-500">{b.title || b.kpi?.label}</span><span className="text-3xl font-semibold text-brand-700">{fmtNumber(b.kpi?.value)}</span></div>;
    case "table":
      return (
        <div className="my-4">
          {editing ? <input className="input mb-2 text-sm" value={b.title || ""} onChange={(e) => onChange({ title: e.target.value })} placeholder="Tiêu đề bảng" /> : b.title && <h4 className="mb-1 text-sm font-semibold text-slate-800">{b.title}</h4>}
          <DataGrid columns={b.columns || []} rows={b.rows || []} maxHeight={360} compact />
          {b.citation && <div className="mt-1 text-xs text-slate-500">{b.citation}</div>}
        </div>
      );
    case "sources":
      if (!b.items?.length) return null;
      return (
        <section className="mt-8 border-t border-slate-200 pt-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Nguồn tham chiếu</h2>
          <ol className="list-decimal space-y-0.5 pl-5 text-sm text-slate-700">{b.items.map((it, i) => <li key={i}>{it.label}{it.effective_date ? <span className="text-slate-400"> (hiệu lực {fmtDate(it.effective_date)})</span> : null}</li>)}</ol>
        </section>
      );
    default:
      return <Badge>Block {b.type}</Badge>;
  }
}
