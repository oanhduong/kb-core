// Danh mục bảng số liệu (DuckDB) + chi tiết: mô tả, cột + thống kê, xem dữ liệu, export, câu hỏi gợi ý.
import { useQuery } from "@tanstack/react-query";
import { Download, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DataGrid } from "@/components/DataGrid";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Tabs, useToast } from "@/components/ui";
import { Page } from "@/layout/AppShell";
import { api, download } from "@/lib/api";
import { fmtCell, fmtNumber } from "@/lib/format";
import { useWorkspace } from "@/state/auth";

export function TablesPage() {
  const { wsId } = useWorkspace();
  const { tableId } = useParams();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const tables = useQuery({ queryKey: ["tables", wsId], queryFn: () => api.tables(wsId) });
  const list = useMemo(() => (tables.data || []).filter((t) => !q || `${t.title} ${t.name} ${t.document_title} ${t.description || ""}`.toLowerCase().includes(q.toLowerCase())), [tables.data, q]);
  const selected = tableId || (list[0]?.id ?? null);

  return (
    <Page wide>
      <PageHeader title="Bảng số liệu" subtitle="Bảng tự trích từ Excel/CSV/PDF, có kiểu cột và mô tả ngữ nghĩa; truy vấn bằng SQL hoặc ngôn ngữ tự nhiên ở mục Phân tích."
        actions={<Link to={`/w/${wsId}/analytics`} className="btn-primary">Phân tích số liệu</Link>} />
      {tables.isLoading && <Loading />}
      {tables.error && <ErrorBox error={tables.error} retry={() => tables.refetch()} />}
      {tables.data && tables.data.length === 0 && <Empty icon={<Table2 className="h-10 w-10" />} title="Chưa có bảng số liệu" hint="Tải lên file Excel/CSV hoặc PDF có bảng; hệ thống sẽ tự nhận diện header, kiểu dữ liệu và mô tả." />}
      {tables.data && tables.data.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          <aside className="card flex max-h-[calc(100vh-200px)] flex-col overflow-hidden">
            <div className="relative border-b border-slate-100 p-2">
              <Search className="pointer-events-none absolute left-4 top-4 h-4 w-4 text-slate-400" />
              <input className="input pl-8" placeholder="Tìm bảng…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <ul className="flex-1 overflow-y-auto">
              {list.map((t) => (
                <li key={t.id}>
                  <button className={`w-full px-3 py-2.5 text-left hover:bg-slate-50 ${selected === t.id ? "bg-brand-50" : ""}`} onClick={() => nav(`/w/${wsId}/tables/${t.id}`)}>
                    <div className={`truncate text-sm font-medium ${selected === t.id ? "text-brand-800" : "text-slate-800"}`}>{t.title}</div>
                    <div className="truncate text-xs text-slate-500">{t.document_title} · {fmtNumber(t.row_count)} dòng · {t.columns.length} cột</div>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          <div className="min-w-0">{selected ? <TableDetailView tableId={selected} /> : <Empty title="Chọn một bảng" />}</div>
        </div>
      )}
    </Page>
  );
}

function TableDetailView({ tableId }: { tableId: string }) {
  const { wsId } = useWorkspace();
  const nav = useNavigate();
  const toast = useToast();
  const [tab, setTab] = useState<"columns" | "rows">("columns");
  const [offset, setOffset] = useState(0);
  const t = useQuery({ queryKey: ["table", wsId, tableId], queryFn: () => api.table(wsId, tableId) });
  const rows = useQuery({ queryKey: ["table-rows", wsId, tableId, offset], queryFn: () => api.tableRows(wsId, tableId, { limit: 100, offset }), enabled: tab === "rows" });
  if (t.isLoading) return <Loading />;
  if (t.error || !t.data) return <ErrorBox error={t.error || "Không có bảng"} />;
  const d = t.data;
  const sqlName = d.name;
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">{d.title}</h2>
            <div className="mt-0.5 text-xs text-slate-500">
              <code className="rounded bg-slate-100 px-1 py-0.5 font-mono">{sqlName}</code> · từ <Link className="text-brand-700 hover:underline" to={`/w/${wsId}/documents/${d.document_id}`}>{d.document_title}</Link>{d.source_location ? ` · ${d.source_location}` : ""} · {fmtNumber(d.row_count)} dòng
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-secondary btn-sm" onClick={() => download(api.exportTablePath(wsId, d.id, "xlsx"), `${sqlName}.xlsx`).catch(toast.err)}><Download className="h-3.5 w-3.5" /> XLSX</button>
            <button className="btn-secondary btn-sm" onClick={() => download(api.exportTablePath(wsId, d.id, "csv"), `${sqlName}.csv`).catch(toast.err)}><Download className="h-3.5 w-3.5" /> CSV</button>
            <button className="btn-primary btn-sm" onClick={() => nav(`/w/${wsId}/analytics?sql=${encodeURIComponent(`SELECT * FROM ${sqlName} LIMIT 100`)}`)}>Mở trong SQL</button>
          </div>
        </div>
        {d.description && <p className="mt-3 text-sm leading-6 text-slate-700">{d.description}</p>}
        {d.suggested_questions?.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">{d.suggested_questions.map((s) => <button key={s} className="badge bg-brand-50 text-brand-700 hover:bg-brand-100" onClick={() => nav(`/w/${wsId}/analytics?q=${encodeURIComponent(s)}`)}>{s}</button>)}</div>
        )}
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "columns", label: "Cột & thống kê", count: d.columns.length }, { key: "rows", label: "Dữ liệu" }]} />
      {tab === "columns" && (
        <div className="card overflow-hidden">
          <table className="w-full"><thead><tr><th className="th">Cột</th><th className="th">Kiểu</th><th className="th">Mô tả</th><th className="th text-right">Có giá trị</th><th className="th text-right">Phân biệt</th><th className="th">Min / Max</th><th className="th text-right">Trung bình</th></tr></thead>
            <tbody>{d.columns.map((c) => {
              const s = (d.stats || {})[c.name] || {};
              const filled = typeof s.count === "number" ? s.count : undefined;
              return (
                <tr key={c.name} className="hover:bg-slate-50">
                  <td className="td font-mono text-xs font-medium text-slate-800">{c.name}</td><td className="td"><Badge>{c.type}</Badge></td>
                  <td className="td max-w-xs text-xs text-slate-600">{c.description || (c.examples?.length ? <span className="text-slate-500">vd: {c.examples.slice(0, 3).map((x) => fmtNumber(x)).join(", ")}</span> : "—")}{c.original && c.original !== c.name ? <span className="block text-[11px] text-slate-400">gốc: {c.original}</span> : null}</td>
                  <td className="td text-right text-xs">{filled !== undefined ? `${fmtNumber(filled)}${d.row_count ? ` (${Math.round((100 * filled) / d.row_count)}%)` : ""}` : "—"}</td>
                  <td className="td text-right text-xs">{s.distinct !== undefined ? fmtNumber(s.distinct) : "—"}</td>
                  <td className="td whitespace-nowrap text-xs">{s.min !== undefined && s.min !== null ? `${fmtCell(s.min, c.type)} / ${fmtCell(s.max, c.type)}` : "—"}</td>
                  <td className="td text-right text-xs">{typeof s.avg === "number" ? fmtNumber(Math.round(s.avg * 100) / 100) : "—"}</td>
                </tr>
              );
            })}</tbody></table>
        </div>
      )}
      {tab === "rows" && (
        <div>
          {rows.isLoading && <Loading />}
          {rows.data && <>
            <DataGrid columns={rows.data.columns} rows={rows.data.rows} maxRows={100} maxHeight={560} compact />
            <div className="mt-2 flex items-center justify-between text-sm text-slate-600">
              <span>Dòng {offset + 1}–{Math.min(offset + 100, d.row_count)} / {fmtNumber(d.row_count)}</span>
              <div className="flex gap-1"><button className="btn-secondary btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Trước</button><button className="btn-secondary btn-sm" disabled={offset + 100 >= d.row_count} onClick={() => setOffset(offset + 100)}>Sau</button></div>
            </div>
          </>}
        </div>
      )}
    </div>
  );
}
