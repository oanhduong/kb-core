// Đồ thị tri thức: tổng quan (force graph top entity), tìm/lọc theo loại, lân cận 1–2 bước, panel chi tiết entity
// (mention có trích dẫn, quan hệ vào/ra), sửa/gộp/xoá entity.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Merge, MessageSquare, Network, Pencil, Search, Trash2 } from "lucide-react";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Confirm, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Spinner, useToast } from "@/components/ui";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtNumber, truncate } from "@/lib/format";
import type { Entity, EntityDetail, GraphEdge } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

const ForceGraph2D = lazy(() => import("react-force-graph-2d"));

const TYPE_COLOR: Record<string, string> = {
  person: "#2563eb", organization: "#0d9488", product: "#f59e0b", location: "#10b981", project: "#8b5cf6", event: "#ec4899",
  document: "#64748b", policy_clause: "#0ea5e9", metric: "#ef4444", date: "#a3a3a3", money: "#d97706", other: "#94a3b8",
};
const TYPE_LABEL: Record<string, string> = {
  person: "Người", organization: "Tổ chức", product: "Sản phẩm", location: "Địa điểm", project: "Dự án", event: "Sự kiện",
  document: "Tài liệu", policy_clause: "Điều khoản", metric: "Chỉ số", date: "Thời gian", money: "Tiền", other: "Khác",
};
const color = (t: string) => TYPE_COLOR[t] || TYPE_COLOR.other;

interface GNode { id: string; name: string; type: string; val: number; is_seed?: boolean; x?: number; y?: number }
interface GLink { source: string | GNode; target: string | GNode; label: string; id: string; confidence: number | null }

export function GraphPage() {
  const { wsId, can } = useWorkspace();
  const { entityId } = useParams();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [depth, setDepth] = useState<1 | 2>(1);
  const overview = useQuery({ queryKey: ["graph-overview", wsId], queryFn: () => api.graphOverview(wsId, 60) });
  const search = useQuery({ queryKey: ["entities", wsId, q, typeFilter], queryFn: () => api.entities(wsId, { q: q || undefined, type: typeFilter || undefined, limit: 40 }), enabled: q.length > 0 || !!typeFilter });
  const hood = useQuery({ queryKey: ["neighborhood", wsId, entityId, depth], queryFn: () => api.neighborhood(wsId, [entityId!], depth, 120), enabled: !!entityId });

  const data = useMemo(() => {
    const src = entityId ? hood.data : overview.data;
    if (!src) return { nodes: [] as GNode[], links: [] as GLink[] };
    const nodes: GNode[] = src.nodes.filter((n) => !typeFilter || entityId || n.type === typeFilter).map((n) => ({ id: n.id, name: n.name, type: n.type, val: Math.max(1, Math.log2(1 + n.mention_count)), is_seed: (n as Entity).is_seed }));
    const ids = new Set(nodes.map((n) => n.id));
    const links: GLink[] = src.edges.filter((e: GraphEdge) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ id: e.id, source: e.source, target: e.target, label: e.label || e.type, confidence: e.confidence }));
    return { nodes, links };
  }, [overview.data, hood.data, entityId, typeFilter]);

  const types = overview.data?.types || [];

  return (
    <Page wide>
      <PageHeader title="Đồ thị tri thức" subtitle="Thực thể và mối quan hệ được trích tự động từ mọi tài liệu (schema mở). Bấm vào một nút để xem lân cận, mention và trích dẫn."
        actions={overview.data && <div className="flex gap-2 text-sm text-slate-600"><Badge>{fmtNumber(overview.data.entity_count)} thực thể</Badge><Badge>{fmtNumber(overview.data.relation_count)} quan hệ</Badge></div>} />
      {overview.isLoading && <Loading />}
      {overview.error && <ErrorBox error={overview.error} retry={() => overview.refetch()} />}
      {overview.data && overview.data.entity_count === 0 && <Empty icon={<Network className="h-10 w-10" />} title="Chưa có thực thể" hint="Đồ thị được xây sau khi tài liệu xử lý xong (bước knowledge_extract). Tải tài liệu có nội dung văn bản để bắt đầu." />}
      {overview.data && overview.data.entity_count > 0 && (
        <div className="grid gap-4 xl:grid-cols-[300px_1fr_360px]">
          <aside className="space-y-3">
            <div className="card p-2">
              <div className="relative"><Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" /><input className="input pl-8" placeholder="Tìm thực thể…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
              <div className="mt-2 flex flex-wrap gap-1">
                <button className={clsx("badge cursor-pointer", !typeFilter ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600")} onClick={() => setTypeFilter("")}>Tất cả</button>
                {types.map((t) => <button key={t.type} className={clsx("badge cursor-pointer", typeFilter === t.type ? "text-white" : "bg-slate-100 text-slate-600")} style={typeFilter === t.type ? { background: color(t.type) } : undefined} onClick={() => setTypeFilter(typeFilter === t.type ? "" : t.type)}><span className="h-2 w-2 rounded-full" style={{ background: color(t.type) }} />{TYPE_LABEL[t.type] || t.type} {t.count}</button>)}
              </div>
            </div>
            <div className="card max-h-[520px] overflow-y-auto">
              {(q || typeFilter) ? (
                search.isLoading ? <Loading /> : (
                  <ul className="divide-y divide-slate-100">
                    {search.data?.items.length === 0 && <li className="p-3 text-sm text-slate-500">Không có kết quả</li>}
                    {search.data?.items.map((e) => <EntityRow key={e.id} e={e} active={e.id === entityId} onClick={() => nav(`/w/${wsId}/graph/${e.id}`)} />)}
                  </ul>
                )
              ) : (
                <ul className="divide-y divide-slate-100">
                  <li className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Nổi bật nhất</li>
                  {overview.data.nodes.slice(0, 30).map((e) => <EntityRow key={e.id} e={e} active={e.id === entityId} onClick={() => nav(`/w/${wsId}/graph/${e.id}`)} />)}
                </ul>
              )}
            </div>
          </aside>

          <div className="card relative min-h-[560px] overflow-hidden">
            <div className="absolute left-3 top-3 z-10 flex items-center gap-2">
              {entityId ? (
                <>
                  <button className="btn-secondary btn-sm" onClick={() => nav(`/w/${wsId}/graph`)}><ArrowLeft className="h-3.5 w-3.5" /> Tổng quan</button>
                  <div className="flex rounded-lg border border-slate-300 bg-white p-0.5 text-xs"><button className={clsx("rounded-md px-2 py-1", depth === 1 ? "bg-brand-600 text-white" : "text-slate-600")} onClick={() => setDepth(1)}>1 bước</button><button className={clsx("rounded-md px-2 py-1", depth === 2 ? "bg-brand-600 text-white" : "text-slate-600")} onClick={() => setDepth(2)}>2 bước</button></div>
                </>
              ) : <span className="rounded-lg bg-white/80 px-2 py-1 text-xs text-slate-500">Top {data.nodes.length} thực thể · kéo để di chuyển, cuộn để zoom</span>}
              {hood.isFetching && <Spinner />}
            </div>
            <Suspense fallback={<Loading />}>
              <Graph data={data} selected={entityId} onSelect={(id) => nav(`/w/${wsId}/graph/${id}`)} />
            </Suspense>
          </div>

          <aside>
            {entityId ? <EntityPanel entityId={entityId} canEdit={can("editor")} /> : (
              <div className="card p-4 text-sm text-slate-600">
                <h3 className="mb-2 font-semibold text-slate-900">Loại quan hệ</h3>
                <ul className="space-y-1">{overview.data.relation_types.slice(0, 15).map((r) => <li key={r.type} className="flex justify-between"><span>{r.type}</span><span className="text-slate-400">{r.count}</span></li>)}</ul>
              </div>
            )}
          </aside>
        </div>
      )}
    </Page>
  );
}

function EntityRow({ e, active, onClick }: { e: Entity; active: boolean; onClick: () => void }) {
  return (
    <li>
      <button className={clsx("flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-slate-50", active && "bg-brand-50")} onClick={onClick}>
        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color(e.type) }} />
        <span className="min-w-0 flex-1"><span className="block truncate text-sm text-slate-800">{e.name}</span><span className="block text-xs text-slate-500">{TYPE_LABEL[e.type] || e.type} · {e.mention_count} mention · {e.document_count} tài liệu</span></span>
      </button>
    </li>
  );
}

function Graph({ data, selected, onSelect }: { data: { nodes: GNode[]; links: GLink[] }; selected?: string; onSelect: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const fg = useRef<{ zoomToFit: (ms?: number, px?: number) => void; d3Force: (name: string) => { strength?: (n: number) => void } | undefined } | null>(null);
  const [size, setSize] = useState({ w: 600, h: 560 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: Math.max(520, el.clientHeight) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const t = setTimeout(() => fg.current?.zoomToFit(400, 40), 600);
    return () => clearTimeout(t);
  }, [data]);
  const graphData = useMemo(() => ({ nodes: data.nodes.map((n) => ({ ...n })), links: data.links.map((l) => ({ ...l })) }), [data]);
  const paint = useCallback((node: GNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const r = 3 + node.val * 1.6;
    ctx.beginPath();
    ctx.arc(node.x!, node.y!, r, 0, 2 * Math.PI);
    ctx.fillStyle = color(node.type);
    ctx.fill();
    if (node.id === selected || node.is_seed) { ctx.lineWidth = 2 / scale; ctx.strokeStyle = "#0f172a"; ctx.stroke(); }
    if (scale > 0.9 || node.val > 3 || node.id === selected) {
      ctx.font = `${Math.max(10 / scale, 2.5)}px Inter, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = "#1e293b";
      ctx.fillText(truncate(node.name, 28), node.x!, node.y! + r + 1.5);
    }
  }, [selected]);
  return (
    <div ref={ref} className="h-[560px] w-full">
      <ForceGraph2D ref={fg as never} width={size.w} height={size.h} graphData={graphData} nodeId="id" nodeCanvasObject={paint as never}
        nodePointerAreaPaint={((node: GNode, c: string, ctx: CanvasRenderingContext2D) => { ctx.fillStyle = c; ctx.beginPath(); ctx.arc(node.x!, node.y!, 3 + node.val * 1.6 + 2, 0, 2 * Math.PI); ctx.fill(); }) as never}
        linkColor={() => "#cbd5e1"} linkWidth={(l) => 0.5 + ((l as GLink).confidence || 0.5) * 1.5} linkDirectionalArrowLength={3} linkDirectionalArrowRelPos={1}
        linkLabel={(l) => (l as GLink).label} nodeLabel={(n) => `${(n as GNode).name} (${TYPE_LABEL[(n as GNode).type] || (n as GNode).type})`}
        onNodeClick={(n) => onSelect((n as GNode).id)} cooldownTicks={120} backgroundColor="#ffffff" enableNodeDrag d3VelocityDecay={0.3} />
    </div>
  );
}

function EntityPanel({ entityId, canEdit }: { entityId: string; canEdit: boolean }) {
  const { wsId } = useWorkspace();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const d = useQuery({ queryKey: ["entity", wsId, entityId], queryFn: () => api.entity(wsId, entityId) });
  const [edit, setEdit] = useState(false);
  const [merge, setMerge] = useState(false);
  const [del, setDel] = useState(false);
  const invalidate = () => { void qc.invalidateQueries({ queryKey: ["entity", wsId] }); void qc.invalidateQueries({ queryKey: ["neighborhood", wsId] }); void qc.invalidateQueries({ queryKey: ["graph-overview", wsId] }); void qc.invalidateQueries({ queryKey: ["entities", wsId] }); };
  const delMut = useMutation({ mutationFn: () => api.deleteEntity(wsId, entityId), onSuccess: () => { toast.ok("Đã xoá thực thể"); invalidate(); nav(`/w/${wsId}/graph`); }, onError: toast.err });
  if (d.isLoading) return <div className="card"><Loading /></div>;
  if (d.error || !d.data) return <ErrorBox error={d.error || "Không có"} />;
  const e = d.data;
  return (
    <div className="card max-h-[calc(100vh-180px)] overflow-y-auto">
      <div className="border-b border-slate-100 p-4">
        <div className="flex items-start gap-2">
          <span className="mt-1.5 h-3 w-3 shrink-0 rounded-full" style={{ background: color(e.type) }} />
          <div className="min-w-0 flex-1">
            <h3 className="text-base font-semibold text-slate-900">{e.name}</h3>
            <div className="text-xs text-slate-500">{TYPE_LABEL[e.type] || e.type} · {e.mention_count} mention · {e.document_count} tài liệu</div>
          </div>
        </div>
        {e.description && <p className="mt-2 text-sm text-slate-700">{e.description}</p>}
        {e.aliases.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{e.aliases.map((a) => <Badge key={a}>{a}</Badge>)}</div>}
        <div className="mt-3 flex flex-wrap gap-1.5">
          <button className="btn-primary btn-sm" onClick={() => nav(`/w/${wsId}/chat?q=${encodeURIComponent(`Cho tôi biết mọi thông tin và mối liên hệ của "${e.name}" trong các tài liệu`)}`)}><MessageSquare className="h-3.5 w-3.5" /> Hỏi về thực thể này</button>
          {canEdit && <><button className="btn-secondary btn-sm" onClick={() => setEdit(true)}><Pencil className="h-3.5 w-3.5" /></button><button className="btn-secondary btn-sm" onClick={() => setMerge(true)} title="Gộp thực thể trùng"><Merge className="h-3.5 w-3.5" /></button><button className="btn-danger btn-sm" onClick={() => setDel(true)}><Trash2 className="h-3.5 w-3.5" /></button></>}
        </div>
      </div>
      <Section title={`Quan hệ (${e.relations_out.length + e.relations_in.length})`}>
        {e.relations_out.length + e.relations_in.length === 0 && <p className="text-xs text-slate-500">Chưa có quan hệ.</p>}
        <ul className="space-y-1.5 text-sm">
          {e.relations_out.map((r) => <RelRow key={r.id} dir="out" r={r} onOpen={() => nav(`/w/${wsId}/graph/${r.entity.id}`)} />)}
          {e.relations_in.map((r) => <RelRow key={r.id} dir="in" r={r} onOpen={() => nav(`/w/${wsId}/graph/${r.entity.id}`)} />)}
        </ul>
      </Section>
      <Section title={`Xuất hiện trong tài liệu (${e.mentions.length})`}>
        <ul className="space-y-2 text-sm">
          {e.mentions.map((m) => (
            <li key={m.id} className="rounded-lg bg-slate-50 p-2">
              <Link to={`/w/${wsId}/documents/${m.document_id}`} className="text-xs font-medium text-brand-700 hover:underline">{m.citation}</Link>
              {m.snippet && <p className="mt-0.5 text-xs leading-5 text-slate-600">{m.snippet}</p>}
            </li>
          ))}
        </ul>
      </Section>
      <EditEntityModal open={edit} onClose={() => setEdit(false)} e={e} onSaved={invalidate} />
      <MergeModal open={merge} onClose={() => setMerge(false)} e={e} onSaved={invalidate} />
      <Confirm open={del} onClose={() => setDel(false)} onConfirm={() => delMut.mutate()} danger title="Xoá thực thể" message="Xoá thực thể cùng mention và quan hệ của nó. Tài liệu gốc không bị ảnh hưởng." busy={delMut.isPending} />
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="border-b border-slate-100 p-4 last:border-0"><h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>{children}</section>;
}

function RelRow({ r, dir, onOpen }: { r: EntityDetail["relations_out"][number]; dir: "in" | "out"; onOpen: () => void }) {
  return (
    <li className="flex items-start gap-1.5">
      <span className={clsx("mt-1 shrink-0 text-xs", dir === "out" ? "text-brand-600" : "text-slate-400")}>{dir === "out" ? "→" : "←"}</span>
      <span className="min-w-0 flex-1">
        <span className="text-xs text-slate-500">{r.label || r.type} </span>
        <button className="font-medium text-slate-800 hover:text-brand-700 hover:underline" onClick={onOpen}>{r.entity.name}</button>
        {r.evidence && <span className="block truncate text-xs text-slate-400" title={r.evidence}>“{r.evidence}”</span>}
      </span>
    </li>
  );
}

function EditEntityModal({ open, onClose, e, onSaved }: { open: boolean; onClose: () => void; e: EntityDetail; onSaved: () => void }) {
  const { wsId } = useWorkspace();
  const toast = useToast();
  const [form, setForm] = useState({ name: e.name, type: e.type, description: e.description || "", aliases: e.aliases.join(", ") });
  useEffect(() => { if (open) setForm({ name: e.name, type: e.type, description: e.description || "", aliases: e.aliases.join(", ") }); }, [open, e]);
  const mut = useMutation({
    mutationFn: () => api.updateEntity(wsId, e.id, { name: form.name.trim(), type: form.type, description: form.description.trim() || undefined, aliases: form.aliases.split(",").map((s) => s.trim()).filter(Boolean) }),
    onSuccess: () => { toast.ok("Đã lưu"); onSaved(); onClose(); }, onError: toast.err,
  });
  return (
    <Modal open={open} onClose={onClose} title="Sửa thực thể" footer={<><button className="btn-secondary" onClick={onClose}>Huỷ</button><button className="btn-primary" disabled={mut.isPending} onClick={() => mut.mutate()}>Lưu</button></>}>
      <div className="space-y-3">
        <Field label="Tên"><input className="input" value={form.name} onChange={(ev) => setForm({ ...form, name: ev.target.value })} /></Field>
        <Field label="Loại"><select className="input" value={form.type} onChange={(ev) => setForm({ ...form, type: ev.target.value })}>{Object.keys(TYPE_LABEL).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}</select></Field>
        <Field label="Mô tả"><textarea className="input" rows={3} value={form.description} onChange={(ev) => setForm({ ...form, description: ev.target.value })} /></Field>
        <Field label="Tên gọi khác (phân cách bằng dấu phẩy)"><input className="input" value={form.aliases} onChange={(ev) => setForm({ ...form, aliases: ev.target.value })} /></Field>
      </div>
    </Modal>
  );
}

function MergeModal({ open, onClose, e, onSaved }: { open: boolean; onClose: () => void; e: EntityDetail; onSaved: () => void }) {
  const { wsId } = useWorkspace();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Entity[]>([]);
  const search = useQuery({ queryKey: ["entities", wsId, "merge", q], queryFn: () => api.entities(wsId, { q, limit: 20 }), enabled: open && q.length > 0 });
  const mut = useMutation({ mutationFn: () => api.mergeEntities(wsId, e.id, picked.map((p) => p.id)), onSuccess: () => { toast.ok(`Đã gộp ${picked.length} thực thể vào "${e.name}"`); setPicked([]); onSaved(); onClose(); }, onError: toast.err });
  return (
    <Modal open={open} onClose={onClose} title={`Gộp vào "${e.name}"`} footer={<><button className="btn-secondary" onClick={onClose}>Huỷ</button><button className="btn-primary" disabled={!picked.length || mut.isPending} onClick={() => mut.mutate()}>Gộp {picked.length || ""}</button></>}>
      <p className="mb-3 text-sm text-slate-600">Chọn các thực thể trùng (viết khác tên). Mention và quan hệ của chúng sẽ chuyển sang thực thể này, tên cũ trở thành alias.</p>
      <input className="input" placeholder="Tìm thực thể cần gộp…" value={q} onChange={(ev) => setQ(ev.target.value)} />
      {picked.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{picked.map((p) => <button key={p.id} className="badge bg-brand-50 text-brand-700" onClick={() => setPicked((xs) => xs.filter((x) => x.id !== p.id))}>{p.name} ✕</button>)}</div>}
      <ul className="mt-2 max-h-60 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200">
        {search.data?.items.filter((x) => x.id !== e.id && !picked.some((p) => p.id === x.id)).map((x) => (
          <li key={x.id}><button className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50" onClick={() => setPicked((xs) => [...xs, x])}><span className="h-2 w-2 rounded-full" style={{ background: color(x.type) }} />{x.name}<span className="text-xs text-slate-400">{TYPE_LABEL[x.type] || x.type} · {x.mention_count}</span></button></li>
        ))}
      </ul>
    </Modal>
  );
}
