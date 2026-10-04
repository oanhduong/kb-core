// Trang tổng quan workspace: KPI sức khoẻ KB, ô hỏi nhanh, tài liệu gần đây, câu hỏi gợi ý, việc đang chạy.
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, FileText, MessageSquare, Network, Table2, Upload } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorBox, KPI, Loading, PageHeader, StatusBadge } from "@/components/ui";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtRelative, SOURCE_ICON, truncate } from "@/lib/format";
import { useWorkspace } from "@/state/auth";

export function OverviewPage() {
  const { ws, wsId, can } = useWorkspace();
  const nav = useNavigate();
  const health = useQuery({ queryKey: ["health", wsId], queryFn: () => api.health(wsId, 7), refetchInterval: 30_000 });
  const docs = useQuery({ queryKey: ["documents", wsId, "recent"], queryFn: () => api.documents(wsId, { page_size: 8 }) });
  const jobs = useQuery({ queryKey: ["jobs", wsId, "active"], queryFn: () => api.jobs(wsId, { limit: 20 }), refetchInterval: 10_000 });
  const [q, setQ] = useState("");
  const active = (jobs.data || []).filter((j) => j.status === "queued" || j.status === "processing");
  const h = health.data;
  const suggestions = [
    ...new Set([
      ...(docs.data?.items.flatMap((d) => d.semantic_profile?.suggested_questions || []) || []),
      ...ws.suggested_questions,
    ]),
  ].slice(0, 6);
  const ask = (question: string) => nav(`/w/${wsId}/chat?q=${encodeURIComponent(question)}`);

  return (
    <Page>
      <PageHeader title={ws.name} subtitle={ws.description || "Hỏi đáp, thống kê, tìm mối liên hệ và lập báo cáo từ dữ liệu đã tải lên."}
        actions={can("editor") && <Link to="documents?upload=1" className="btn-primary"><Upload className="h-4 w-4" /> Tải tài liệu</Link>} />

      <form className="card mb-6 flex items-center gap-2 p-2 pl-4" onSubmit={(e) => { e.preventDefault(); if (q.trim()) ask(q.trim()); }}>
        <MessageSquare className="h-5 w-5 text-brand-600" />
        <input className="flex-1 bg-transparent py-2 text-[15px] outline-none placeholder:text-slate-400" placeholder="Hỏi bất kỳ điều gì về dữ liệu trong workspace…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn-primary" disabled={!q.trim()}>Hỏi <ArrowRight className="h-4 w-4" /></button>
      </form>

      {health.error && <ErrorBox error={health.error} retry={() => health.refetch()} />}
      {h && (
        <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <KPI label="Tài liệu" value={h.totals.documents} hint={`${Object.values(h.records_by_doc_type).reduce((a, m) => a + Object.values(m).reduce((x, y) => x + y, 0), 0)} bản ghi`} />
          <KPI label="Bảng số liệu" value={h.totals.data_tables} hint="trích từ Excel/CSV/PDF" />
          <KPI label="Thực thể" value={h.totals.entities} hint="trong đồ thị tri thức" />
          <KPI label="Câu hỏi 7 ngày" value={h.abstentions_last_days.questions_total}
            hint={h.abstentions_last_days.count ? `${h.abstentions_last_days.count} câu không đủ căn cứ` : "không có câu bị từ chối"}
            tone={h.abstentions_last_days.rate && h.abstentions_last_days.rate > 0.3 ? "amber" : undefined} />
        </div>
      )}
      {h && (h.blocked_files.count > 0 || h.open_conflicts > 0 || h.embedding_coverage.missing > 0) && (
        <div className="mb-6 flex flex-wrap gap-3">
          {h.blocked_files.count > 0 && <Link to="documents?status=blocked" className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"><AlertTriangle className="h-4 w-4" /> {h.blocked_files.count} file bị chặn khi xử lý</Link>}
          {h.open_conflicts > 0 && <Link to="ops/conflicts" className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"><AlertTriangle className="h-4 w-4" /> {h.open_conflicts} xung đột dữ liệu chờ xử lý</Link>}
          {h.embedding_coverage.missing > 0 && <span className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">{h.embedding_coverage.missing} đoạn chưa được index (đang xử lý nền)</span>}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="card lg:col-span-2">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-900">Tài liệu gần đây</h2>
            <Link to="documents" className="text-sm text-brand-700 hover:underline">Xem tất cả</Link>
          </div>
          {docs.isLoading && <Loading />}
          {docs.data && docs.data.items.length === 0 && (
            <Empty icon={<FileText className="h-10 w-10" />} title="Chưa có tài liệu" hint="Tải lên Excel, Word, PDF, CSV, ảnh, audio/video… hệ thống sẽ tự trích nội dung, bảng số liệu và thực thể."
              action={can("editor") && <Link to="documents?upload=1" className="btn-primary"><Upload className="h-4 w-4" /> Tải tài liệu đầu tiên</Link>} />
          )}
          <ul className="divide-y divide-slate-100">
            {docs.data?.items.map((d) => (
              <li key={d.document_id}>
                <Link to={`documents/${d.document_id}`} className="flex items-start gap-3 px-4 py-3 hover:bg-slate-50">
                  <span className="text-xl leading-none">{SOURCE_ICON[d.source_type] || "📄"}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2"><span className="truncate text-sm font-medium text-slate-900">{d.title}</span><StatusBadge status={d.status} /></span>
                    <span className="mt-0.5 block text-xs text-slate-500">{d.summary ? truncate(d.summary, 160) : `${d.filename} · ${d.record_count} bản ghi`}</span>
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">{fmtRelative(d.updated_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <div className="space-y-6">
          <section className="card">
            <div className="border-b border-slate-100 px-4 py-3"><h2 className="text-sm font-semibold text-slate-900">Gợi ý câu hỏi</h2></div>
            <div className="flex flex-col p-2">
              {suggestions.length === 0 && <div className="px-2 py-3 text-sm text-slate-500">Tải tài liệu lên để nhận gợi ý theo nội dung.</div>}
              {suggestions.map((s) => (
                <button key={s} className="rounded-lg px-3 py-2 text-left text-sm text-slate-700 hover:bg-brand-50 hover:text-brand-800" onClick={() => ask(s)}>{s}</button>
              ))}
            </div>
          </section>
          <section className="card">
            <div className="border-b border-slate-100 px-4 py-3"><h2 className="text-sm font-semibold text-slate-900">Đang xử lý</h2></div>
            <div className="p-4 text-sm">
              {active.length === 0 ? <div className="text-slate-500">Không có việc nền nào đang chạy.</div> : (
                <ul className="space-y-2">
                  {active.map((j) => (
                    <li key={j.job_id} className="flex items-center gap-2">
                      <StatusBadge status={j.status} />
                      <span className="flex-1 truncate text-slate-700">{j.type}</span>
                      {j.progress.percent !== null && <span className="text-xs text-slate-500">{j.progress.percent}%</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
          <div className="grid grid-cols-2 gap-3">
            <Link to="tables" className="card flex items-center gap-2 p-3 text-sm hover:bg-slate-50"><Table2 className="h-4 w-4 text-brand-600" /> Bảng số liệu</Link>
            <Link to="graph" className="card flex items-center gap-2 p-3 text-sm hover:bg-slate-50"><Network className="h-4 w-4 text-brand-600" /> Đồ thị tri thức</Link>
          </div>
        </div>
      </div>
    </Page>
  );
}
