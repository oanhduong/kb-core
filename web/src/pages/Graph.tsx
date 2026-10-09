// Bản đồ tri thức: tổng quan (force graph các mục nổi bật), tìm/lọc theo loại, lân cận 1–2 bước, panel chi tiết
// (nơi được nhắc đến kèm đoạn trích dẫn, liên kết vào/ra), sửa/gộp/xoá (biên tập). Số liệu kỹ thuật chỉ ở chế độ kỹ thuật.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, ArrowRight, Maximize2, Merge, MessageSquare, Minimize2, MousePointerClick, Network, Pencil, Plus, Quote, Scan, Search, Trash2, X } from "lucide-react";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Confirm, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Segmented, Spinner, useToast } from "@/components/ui";
import { useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtNumber, fmtPct, truncate } from "@/lib/format";
import type { Entity, EntityDetail, GraphEdge } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { Advanced, usePrefs } from "@/state/prefs";

const ForceGraph2D = lazy(() => import("react-force-graph-2d"));

/** Bảng màu theo loại — hài hoà với token brand/ink/verify/alert. */
const TYPE_COLOR: Record<string, string> = {
  person: "#5B4CF5", organization: "#3A3680", product: "#F59E0B", location: "#0E9F6E", project: "#8E7CFB", event: "#E5484D",
  document: "#8C89B8", policy_clause: "#14B8A6", metric: "#D946EF", date: "#57538F", money: "#B45309", other: "#B9B6D9",
};
const TYPES = Object.keys(TYPE_COLOR);
const color = (type: string) => TYPE_COLOR[type] || TYPE_COLOR.other;

type T = ReturnType<typeof useT>;
function typeLabel(t: T, type: string): string {
  const k = `graph.type.${type}`;
  const out = t(k);
  return out === k ? type.replace(/_/g, " ") : out;
}
const relLabel = (r: { label: string | null; type: string }) => r.label || r.type.replace(/_/g, " ").toLowerCase();

interface GNode { id: string; name: string; type: string; val: number; is_seed?: boolean; x?: number; y?: number }
interface GLink { source: string | GNode; target: string | GNode; label: string; id: string; confidence: number | null }

export function GraphPage() {
  const { wsId, can } = useWorkspace();
  const t = useT();
  const { entityId } = useParams();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [depth, setDepth] = useState<"1" | "2">("1");
  const [full, setFull] = useState(false);
  const fitRef = useRef<() => void>(() => {});
  useEffect(() => {
    if (!full) return;
    const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);
  useEffect(() => {
    const tm = setTimeout(() => fitRef.current(), 350);  // khung đổi kích thước -> căn lại cho vừa
    return () => clearTimeout(tm);
  }, [full]);
  const overview = useQuery({ queryKey: ["graph-overview", wsId], queryFn: () => api.graphOverview(wsId, 60) });
  const search = useQuery({ queryKey: ["entities", wsId, q, typeFilter], queryFn: () => api.entities(wsId, { q: q || undefined, type: typeFilter || undefined, limit: 40 }), enabled: q.length > 0 || !!typeFilter });
  const hood = useQuery({ queryKey: ["neighborhood", wsId, entityId, depth], queryFn: () => api.neighborhood(wsId, [entityId!], Number(depth), 120), enabled: !!entityId });

  const data = useMemo(() => {
    const src = entityId ? hood.data : overview.data;
    if (!src) return { nodes: [] as GNode[], links: [] as GLink[] };
    const nodes: GNode[] = src.nodes.filter((n) => !typeFilter || entityId || n.type === typeFilter).map((n) => ({ id: n.id, name: n.name, type: n.type, val: Math.max(1, Math.log2(1 + n.mention_count)), is_seed: (n as Entity).is_seed }));
    const ids = new Set(nodes.map((n) => n.id));
    const links: GLink[] = src.edges.filter((e: GraphEdge) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ id: e.id, source: e.source, target: e.target, label: relLabel(e), confidence: e.confidence }));
    return { nodes, links };
  }, [overview.data, hood.data, entityId, typeFilter]);

  const types = overview.data?.types || [];
  const legend = useMemo(() => Array.from(new Set(data.nodes.map((n) => n.type))), [data.nodes]);
  const open = (id: string) => nav(`/w/${wsId}/graph/${id}`);

  return (
    <Page wide>
      <PageHeader title={t("graph.title")} subtitle={t("graph.subtitle")}
        actions={overview.data && overview.data.entity_count > 0 && (
          <div className="flex flex-wrap gap-2">
            <Badge tone="brand">{t("graph.count.things", { n: fmtNumber(overview.data.entity_count) })}</Badge>
            <Badge>{t("graph.count.links", { n: fmtNumber(overview.data.relation_count) })}</Badge>
          </div>
        )} />
      {overview.isLoading && <Loading />}
      {overview.error && <ErrorBox error={overview.error} retry={() => overview.refetch()} />}
      {overview.data && overview.data.entity_count === 0 && (
        <div className="panel">
          <Empty icon={<Network />} title={t("graph.empty.title")} hint={t("graph.empty.hint")}
            action={<Link className="btn-primary" to={`/w/${wsId}/documents`}>{t("graph.empty.action")}</Link>} />
        </div>
      )}
      {overview.data && overview.data.entity_count > 0 && (
        <div className={clsx("grid gap-4", entityId ? "xl:grid-cols-[280px_minmax(0,1fr)_380px]" : "lg:grid-cols-[280px_minmax(0,1fr)]")}>
          {/* tìm + lọc (+ cách đọc bản đồ khi chưa chọn mục nào) */}
          <div className="flex min-w-0 flex-col gap-4">
          <aside className="panel flex max-h-[calc(100vh-200px)] min-h-[360px] flex-col overflow-hidden">
            <div className="border-b border-ink-100 p-3">
              <label className="relative block">
                <span className="sr-only">{t("graph.search.label")}</span>
                <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
                <input className="input pl-9" placeholder={t("graph.search.placeholder")} value={q} onChange={(e) => setQ(e.target.value)} />
                {q && <button className="absolute right-2 top-1.5 rounded-md p-1 text-ink-400 hover:text-ink-900" onClick={() => setQ("")} aria-label={t("graph.search.clear")}><X className="h-3.5 w-3.5" /></button>}
              </label>
              <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label={t("graph.filter.label")}>
                <button className={clsx("chip border transition", !typeFilter ? "border-ink-900 bg-ink-900 text-white" : "border-ink-100 bg-white text-ink-700 hover:border-ink-300")} aria-pressed={!typeFilter} onClick={() => setTypeFilter("")}>{t("graph.filter.all")}</button>
                {types.map((ty) => {
                  const on = typeFilter === ty.type;
                  return (
                    <button key={ty.type} aria-pressed={on} onClick={() => setTypeFilter(on ? "" : ty.type)}
                      className={clsx("chip border transition", on ? "border-ink-900 bg-ink-900 text-white" : "border-ink-100 bg-white text-ink-700 hover:border-ink-300")}>
                      <span className="h-2 w-2 rounded-full" style={{ background: color(ty.type) }} />
                      {typeLabel(t, ty.type)}<span className={clsx("tabular-nums", on ? "text-ink-300" : "text-ink-400")}>{fmtNumber(ty.count)}</span>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {(q || typeFilter) ? (
                search.isLoading ? <Loading /> : (
                  <ul className="p-1.5">
                    {search.data?.items.length === 0 && <li className="px-3 py-6 text-center text-sm text-ink-600">{t("graph.search.none")}</li>}
                    {search.data?.items.map((e) => <EntityRow key={e.id} e={e} active={e.id === entityId} onClick={() => open(e.id)} />)}
                  </ul>
                )
              ) : (
                <ul className="p-1.5">
                  <li className="px-3 pb-1 pt-2 font-display text-sm font-semibold text-ink-900">{t("graph.list.top")}</li>
                  {overview.data.nodes.slice(0, 30).map((e) => <EntityRow key={e.id} e={e} active={e.id === entityId} onClick={() => open(e.id)} />)}
                </ul>
              )}
            </div>
          </aside>
          {!entityId && <MapGuide relationTypes={overview.data.relation_types} />}
          </div>

          {/* bản đồ */}
          {full && <div className="fixed inset-0 z-40 bg-ink-950/40" onClick={() => setFull(false)} />}
          <div className={clsx("panel overflow-hidden", full ? "fixed inset-3 z-50 sm:inset-6" : "relative")}>
            <div className="absolute left-3 right-3 top-3 z-10 flex flex-wrap items-center gap-2">
              {entityId ? (
                <>
                  <button className="btn-secondary btn-sm" onClick={() => nav(`/w/${wsId}/graph`)}><ArrowLeft className="h-3.5 w-3.5" /> {t("graph.map.backToOverview")}</button>
                  <Segmented value={depth} onChange={setDepth} options={[{ key: "1", label: t("graph.map.depth1") }, { key: "2", label: t("graph.map.depth2") }]} />
                </>
              ) : <span className="rounded-lg bg-white/90 px-2.5 py-1 text-xs text-ink-600 shadow-card">{t("graph.map.hint", { n: data.nodes.length })}</span>}
              {hood.isFetching && <Spinner />}
              <div className="ml-auto flex gap-1.5">
                <button className="btn-secondary btn-sm px-2" onClick={() => fitRef.current()} title={t("graph.map.fit")} aria-label={t("graph.map.fit")}><Scan className="h-3.5 w-3.5" /></button>
                <button className="btn-secondary btn-sm px-2" onClick={() => setFull((v) => !v)} title={t(full ? "graph.map.collapse" : "graph.map.expand")} aria-label={t(full ? "graph.map.collapse" : "graph.map.expand")} aria-pressed={full}>
                  {full ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>
            <div className={clsx("dotgrid bg-paper/40", full ? "h-full" : "h-[calc(100vh-200px)] min-h-[560px]")}>
              {entityId && hood.error ? <div className="p-16"><ErrorBox error={hood.error} retry={() => hood.refetch()} /></div> : (
                <Suspense fallback={<Loading />}>
                  <Graph data={data} selected={entityId} onSelect={open} typeName={(ty) => typeLabel(t, ty)} fitRef={fitRef} />
                </Suspense>
              )}
            </div>
            {legend.length > 0 && (
              <div className="absolute bottom-3 left-3 right-3 z-10 flex flex-wrap gap-x-3 gap-y-1 rounded-xl bg-white/90 px-3 py-2 text-xs text-ink-700 shadow-card sm:right-auto sm:max-w-[80%]">
                {legend.map((ty) => <span key={ty} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: color(ty) }} />{typeLabel(t, ty)}</span>)}
              </div>
            )}
          </div>

          {/* chi tiết */}
          {entityId && <aside><EntityPanel entityId={entityId} canEdit={can("editor")} /></aside>}
        </div>
      )}
    </Page>
  );
}

function MapGuide({ relationTypes }: { relationTypes: { type: string; count: number }[] }) {
  const t = useT();
  return (
    <div className="space-y-4">
      <div className="panel p-5">
        <h2 className="font-display text-lg font-semibold text-ink-900">{t("graph.guide.title")}</h2>
        <ul className="mt-3 space-y-3 text-sm leading-6 text-ink-700">
          <li className="flex gap-3"><span className="mt-1 h-3 w-3 shrink-0 rounded-full bg-brand-600" /><span>{t("graph.guide.dots")}</span></li>
          <li className="flex gap-3"><span className="mt-2.5 h-px w-3 shrink-0 bg-ink-400" /><span>{t("graph.guide.lines")}</span></li>
          <li className="flex gap-3"><MousePointerClick className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" /><span>{t("graph.guide.click")}</span></li>
        </ul>
      </div>
      <Advanced>
        <div className="panel p-5">
          <h3 className="mb-2 font-display text-base font-semibold text-ink-900">{t("graph.relTypes.title")}</h3>
          <ul className="space-y-1 text-sm text-ink-700">
            {relationTypes.slice(0, 15).map((r) => <li key={r.type} className="flex justify-between gap-2"><span className="truncate font-mono text-xs">{r.type}</span><span className="tabular-nums text-ink-400">{fmtNumber(r.count)}</span></li>)}
          </ul>
        </div>
      </Advanced>
    </div>
  );
}

function EntityRow({ e, active, onClick }: { e: Entity; active: boolean; onClick: () => void }) {
  const t = useT();
  return (
    <li>
      <button className={clsx("flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left transition", active ? "bg-brand-50" : "hover:bg-paper")} onClick={onClick} aria-current={active || undefined}>
        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color(e.type) }} />
        <span className="min-w-0 flex-1">
          <span className={clsx("block truncate text-sm", active ? "font-semibold text-ink-900" : "text-ink-800")}>{e.name}</span>
          <span className="block truncate text-xs text-ink-600">{typeLabel(t, e.type)}, {t("graph.mentions.short", { n: fmtNumber(e.mention_count), d: fmtNumber(e.document_count) })}</span>
        </span>
      </button>
    </li>
  );
}

/** Bán kính chấm (đơn vị đồ thị) theo số lần được nhắc. */
const radius = (n: GNode) => 5 + n.val * 2.4;
/** Chấm không nhỏ hơn MIN_PX điểm ảnh trên màn hình dù thu nhỏ cỡ nào. */
const MIN_PX = 4.5;
const LABEL_PX = 12;

type SimNode = GNode & { vx?: number; vy?: number };
type Force = ((alpha: number) => void) & { initialize?: (nodes: SimNode[]) => void };

/** Lực chống chồng: hai chấm không đè lên nhau (chừa chỗ cho nhãn bên dưới). */
function collide(pad: number): Force {
  let nodes: SimNode[] = [];
  const f: Force = () => {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = (b.x ?? 0) - (a.x ?? 0), dy = (b.y ?? 0) - (a.y ?? 0);
        const min = radius(a) + radius(b) + pad;
        const d2 = dx * dx + dy * dy;
        if (d2 >= min * min) continue;
        const d = Math.sqrt(d2) || 0.01;
        const push = ((min - d) / d) * 0.5;
        const px = (dx || 0.01) * push, py = dy * push;
        a.x = (a.x ?? 0) - px; a.y = (a.y ?? 0) - py;
        b.x = (b.x ?? 0) + px; b.y = (b.y ?? 0) + py;
      }
    }
  };
  f.initialize = (ns) => { nodes = ns; };
  return f;
}

/** Lực kéo nhẹ về tâm: chấm lẻ (không liên kết) không trôi ra xa làm bản đồ phải thu nhỏ. */
function gravity(strength: number): Force {
  let nodes: SimNode[] = [];
  const f: Force = (alpha) => {
    for (const n of nodes) {
      n.vx = (n.vx ?? 0) - (n.x ?? 0) * strength * alpha;
      n.vy = (n.vy ?? 0) - (n.y ?? 0) * strength * alpha;
    }
  };
  f.initialize = (ns) => { nodes = ns; };
  return f;
}

interface FGApi {
  zoomToFit: (ms?: number, px?: number) => void;
  d3Force: (name: string, f?: unknown) => { strength?: (v: number) => unknown; distance?: (v: (l: GLink) => number) => unknown; distanceMax?: (v: number) => unknown } | undefined;
  d3ReheatSimulation: () => void;
}

function Graph({ data, selected, onSelect, typeName, fitRef }: { data: { nodes: GNode[]; links: GLink[] }; selected?: string; onSelect: (id: string) => void; typeName: (t: string) => string; fitRef: React.MutableRefObject<() => void> }) {
  const ref = useRef<HTMLDivElement>(null);
  const fg = useRef<FGApi | null>(null);
  const fitted = useRef(false);
  const [size, setSize] = useState({ w: 600, h: 600 });
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: Math.max(520, el.clientHeight) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const graphData = useMemo(() => ({ nodes: data.nodes.map((n) => ({ ...n })), links: data.links.map((l) => ({ ...l })) }), [data]);
  // hàng xóm trực tiếp của mỗi chấm -> rê chuột làm nổi chấm đó cùng các liên kết, làm mờ phần còn lại
  const neighbors = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const l of data.links) {
      const a = typeof l.source === "string" ? l.source : l.source.id, b = typeof l.target === "string" ? l.target : l.target.id;
      if (!m.has(a)) m.set(a, new Set());
      if (!m.has(b)) m.set(b, new Set());
      m.get(a)!.add(b);
      m.get(b)!.add(a);
    }
    return m;
  }, [data]);
  // chỉ ghi nhãn cho ~14 mục được nhắc nhiều nhất (phóng to thì hiện thêm) -> không chồng chữ
  const labelMin = useMemo(() => {
    const vals = data.nodes.map((n) => n.val).sort((a, b) => b - a);
    return vals[Math.min(13, vals.length - 1)] ?? 0;
  }, [data]);
  const focus = hover || selected || null;
  const lit = useCallback((id: string) => !focus || id === focus || !!neighbors.get(focus)?.has(id), [focus, neighbors]);

  // cấu hình lực ngay khi đồ thị (tải lazy) gắn ref, và mỗi lần đổi dữ liệu
  const [ready, setReady] = useState(false);
  const setFg = useCallback((inst: FGApi | null) => { fg.current = inst; setReady(!!inst); }, []);
  useEffect(() => {
    const g = fg.current;
    if (!g || !ready) return;
    fitted.current = false;
    const n = data.nodes.length;
    const deg = (id: string) => neighbors.get(id)?.size ?? 0;
    g.d3Force("charge")?.strength?.(n > 80 ? -120 : -170);
    g.d3Force("charge")?.distanceMax?.(280);
    // nút trung tâm càng nhiều liên kết thì cạnh càng dài -> các nút lá có chỗ xếp vòng quanh, không dính chùm
    g.d3Force("link")?.distance?.((l: GLink) => {
      const a = l.source as GNode, b = l.target as GNode;
      return 28 + radius(a) + radius(b) + 5 * Math.sqrt(Math.max(deg(a.id), deg(b.id)));
    });
    g.d3Force("collide", collide(12));
    g.d3Force("gravity", gravity(0.09));
    g.d3ReheatSimulation();
  }, [graphData, data.nodes.length, ready, neighbors]);
  useEffect(() => { fitRef.current = () => fg.current?.zoomToFit(400, 48); }, [fitRef]);

  const paint = useCallback((node: GNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const r = Math.max(radius(node), MIN_PX / scale);
    const isSel = node.id === selected;
    const on = lit(node.id);
    ctx.globalAlpha = on ? 1 : 0.18;
    if (isSel || node.id === hover) {
      ctx.beginPath();
      ctx.arc(node.x!, node.y!, r + 6 / scale, 0, 2 * Math.PI);
      ctx.fillStyle = "rgba(91,76,245,.18)";
      ctx.fill();
    }
    ctx.beginPath();
    ctx.arc(node.x!, node.y!, r, 0, 2 * Math.PI);
    ctx.fillStyle = color(node.type);
    ctx.fill();
    ctx.lineWidth = (isSel || node.is_seed ? 2.5 : 1.5) / scale;
    ctx.strokeStyle = isSel || node.is_seed ? "#1E1B4B" : "#FFFFFF";
    ctx.stroke();
    // đang rê/chọn 1 chấm: ghi tên mọi hàng xóm nếu ít (≤ 12), nhiều hơn thì chỉ các mục chính -> không chồng chữ
    const fewNeighbors = (neighbors.get(focus || "")?.size ?? 0) <= 12;
    const showLabel = isSel || node.id === hover || (focus && fewNeighbors && on) || node.val >= labelMin || scale > 2.2;
    if (showLabel && (on || !focus)) {
      const fs = LABEL_PX / scale;
      ctx.font = `${isSel || node.id === focus ? 600 : 500} ${fs}px 'Be Vietnam Pro', sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      const label = truncate(node.name, 30);
      const w = ctx.measureText(label).width;
      const y = node.y! + r + 3 / scale;
      ctx.fillStyle = "rgba(255,255,255,.9)";
      ctx.fillRect(node.x! - w / 2 - 3 / scale, y - 1 / scale, w + 6 / scale, fs * 1.3);
      ctx.fillStyle = "#1E1B4B";
      ctx.fillText(label, node.x!, y);
    }
    ctx.globalAlpha = 1;
  }, [selected, hover, focus, lit, labelMin, neighbors]);
  const linkOn = (l: GLink) => {
    if (!focus) return true;
    const a = typeof l.source === "string" ? l.source : l.source.id, b = typeof l.target === "string" ? l.target : l.target.id;
    return a === focus || b === focus;
  };
  return (
    <div ref={ref} className="h-full w-full">
      <ForceGraph2D ref={setFg as never} width={size.w} height={size.h} graphData={graphData} nodeId="id" nodeCanvasObject={paint as never}
        nodePointerAreaPaint={((node: GNode, c: string, ctx: CanvasRenderingContext2D, scale: number) => { ctx.fillStyle = c; ctx.beginPath(); ctx.arc(node.x!, node.y!, Math.max(radius(node), MIN_PX / scale) + 3 / scale, 0, 2 * Math.PI); ctx.fill(); }) as never}
        linkColor={(l) => (linkOn(l as GLink) ? (focus ? "rgba(91,76,245,.55)" : "rgba(87,83,143,.3)") : "rgba(87,83,143,.06)")}
        linkWidth={(l) => (linkOn(l as GLink) && focus ? 2 : 0.6 + ((l as GLink).confidence || 0.5) * 1.2)}
        linkDirectionalArrowLength={4} linkDirectionalArrowRelPos={1}
        linkDirectionalArrowColor={(l) => (linkOn(l as GLink) ? "rgba(87,83,143,.55)" : "rgba(87,83,143,.06)")}
        linkLabel={(l) => (l as GLink).label} nodeLabel={(n) => `${(n as GNode).name} (${typeName((n as GNode).type)})`}
        onNodeHover={(n) => setHover(n ? (n as GNode).id : null)}
        onNodeClick={(n) => onSelect((n as GNode).id)}
        onEngineStop={() => { if (!fitted.current) { fitted.current = true; fg.current?.zoomToFit(500, 48); } }}
        cooldownTicks={200} backgroundColor="rgba(0,0,0,0)" enableNodeDrag d3VelocityDecay={0.35} minZoom={0.3} maxZoom={8} />
    </div>
  );
}

/** Tô dạ quang các lần tên (hoặc tên khác) xuất hiện trong đoạn trích — đây là bằng chứng. */
function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  const clean = terms.map((s) => s.trim()).filter((s) => s.length > 1);
  if (!clean.length) return <>{text}</>;
  const re = new RegExp(`(${clean.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).sort((a, b) => b.length - a.length).join("|")})`, "gi");
  const parts = text.split(re);
  return <>{parts.map((p, i) => (i % 2 === 1 ? <mark key={i} className="marker text-ink-900">{p}</mark> : <span key={i}>{p}</span>))}</>;
}

function EntityPanel({ entityId, canEdit }: { entityId: string; canEdit: boolean }) {
  const { wsId } = useWorkspace();
  const t = useT();
  const { advanced } = usePrefs();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const d = useQuery({ queryKey: ["entity", wsId, entityId], queryFn: () => api.entity(wsId, entityId) });
  const [edit, setEdit] = useState(false);
  const [merge, setMerge] = useState(false);
  const [del, setDel] = useState(false);
  const invalidate = () => { void qc.invalidateQueries({ queryKey: ["entity", wsId] }); void qc.invalidateQueries({ queryKey: ["neighborhood", wsId] }); void qc.invalidateQueries({ queryKey: ["graph-overview", wsId] }); void qc.invalidateQueries({ queryKey: ["entities", wsId] }); };
  const delMut = useMutation({ mutationFn: () => api.deleteEntity(wsId, entityId), onSuccess: () => { toast.ok(t("graph.delete.done")); invalidate(); nav(`/w/${wsId}/graph`); }, onError: toast.err });
  if (d.isLoading) return <div className="panel"><Loading /></div>;
  if (d.error || !d.data) return <ErrorBox error={d.error || t("graph.entity.missing")} retry={() => d.refetch()} />;
  const e = d.data;
  const relCount = e.relations_out.length + e.relations_in.length;
  return (
    <div className="panel max-h-[calc(100vh-160px)] overflow-y-auto">
      <div className="border-b border-ink-100 p-5">
        <div className="flex items-start gap-3">
          <span className="mt-2 h-3.5 w-3.5 shrink-0 rounded-full ring-4 ring-paper" style={{ background: color(e.type) }} />
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-xl font-semibold leading-tight text-ink-900">{e.name}</h2>
            <div className="mt-1 text-sm text-ink-600">{typeLabel(t, e.type)}</div>
          </div>
        </div>
        <p className="mt-3 text-sm text-ink-700">{t("graph.entity.mentioned", { n: fmtNumber(e.mention_count), d: fmtNumber(e.document_count) })}</p>
        {e.description && <p className="mt-2 text-sm leading-6 text-ink-800">{e.description}</p>}
        {e.aliases.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-xs font-medium text-ink-600">{t("graph.entity.aliases")}</div>
            <div className="flex flex-wrap gap-1">{e.aliases.map((a) => <Badge key={a}>{a}</Badge>)}</div>
          </div>
        )}
        <Advanced><div className="mt-2 font-mono text-[11px] text-ink-400">{e.id}</div></Advanced>
        <div className="mt-4 flex flex-wrap gap-1.5">
          <button className="btn-primary btn-sm" onClick={() => nav(`/w/${wsId}/chat?q=${encodeURIComponent(t("graph.entity.askPrompt", { name: e.name }))}`)}><MessageSquare className="h-3.5 w-3.5" /> {t("graph.entity.ask")}</button>
          {canEdit && (
            <>
              <button className="btn-secondary btn-sm" onClick={() => setEdit(true)}><Pencil className="h-3.5 w-3.5" /> {t("action.edit")}</button>
              <button className="btn-secondary btn-sm" onClick={() => setMerge(true)}><Merge className="h-3.5 w-3.5" /> {t("graph.merge.button")}</button>
              <button className="btn-danger btn-sm" onClick={() => setDel(true)} aria-label={t("graph.delete.title")} title={t("graph.delete.title")}><Trash2 className="h-3.5 w-3.5" /></button>
            </>
          )}
        </div>
      </div>

      <Section title={t("graph.entity.mentionsTitle")} count={e.mentions.length}>
        {e.mentions.length === 0 && <p className="text-sm text-ink-600">{t("graph.entity.noMentions")}</p>}
        <ul className="space-y-3">
          {e.mentions.map((m) => (
            <li key={m.id} className="well p-3">
              <Link to={`/w/${wsId}/documents/${m.document_id}`} className="link text-sm">{m.document_title || m.citation}</Link>
              {m.location && <span className="ml-1.5 text-xs text-ink-600">{m.location}</span>}
              {m.snippet && (
                <blockquote className="mt-2 flex gap-2 text-[13px] leading-6 text-ink-800">
                  <Quote className="mt-1 h-3.5 w-3.5 shrink-0 text-ink-400" />
                  <span><Highlighted text={m.snippet} terms={[e.name, ...e.aliases]} /></span>
                </blockquote>
              )}
              <Advanced><div className="mt-1 font-mono text-[11px] text-ink-400">{m.citation}</div></Advanced>
            </li>
          ))}
        </ul>
      </Section>

      <Section title={t("graph.entity.linksTitle")} count={relCount}>
        {relCount === 0 && <p className="text-sm text-ink-600">{t("graph.entity.noLinks")}</p>}
        <ul className="space-y-2.5">
          {e.relations_out.map((r) => <RelRow key={r.id} dir="out" r={r} showConfidence={advanced} onOpen={() => nav(`/w/${wsId}/graph/${r.entity.id}`)} />)}
          {e.relations_in.map((r) => <RelRow key={r.id} dir="in" r={r} showConfidence={advanced} onOpen={() => nav(`/w/${wsId}/graph/${r.entity.id}`)} />)}
        </ul>
      </Section>

      <EditEntityModal open={edit} onClose={() => setEdit(false)} e={e} onSaved={invalidate} />
      <MergeModal open={merge} onClose={() => setMerge(false)} e={e} onSaved={invalidate} />
      <Confirm open={del} onClose={() => setDel(false)} onConfirm={() => delMut.mutate()} danger title={t("graph.delete.title")} message={t("graph.delete.message", { name: e.name })} confirmLabel={t("graph.delete.confirm")} busy={delMut.isPending} />
    </div>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="border-b border-ink-100 p-5 last:border-0">
      <h3 className="mb-3 flex items-center gap-2 font-display text-base font-semibold text-ink-900">
        {title}{count !== undefined && <span className="rounded-full bg-ink-100 px-1.5 text-xs font-medium text-ink-700">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

function RelRow({ r, dir, onOpen, showConfidence }: { r: EntityDetail["relations_out"][number]; dir: "in" | "out"; onOpen: () => void; showConfidence: boolean }) {
  const t = useT();
  const Icon = dir === "out" ? ArrowRight : ArrowLeft;
  return (
    <li className="flex items-start gap-2.5">
      <span className={clsx("mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg", dir === "out" ? "bg-brand-50 text-brand-700" : "bg-ink-100/70 text-ink-600")} title={t(dir === "out" ? "graph.rel.out" : "graph.rel.in")}>
        <Icon className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0 flex-1 text-sm">
        <span className="text-ink-600">{relLabel(r)} </span>
        <button className="font-medium text-ink-900 underline-offset-2 hover:text-brand-700 hover:underline" onClick={onOpen}>{r.entity.name}</button>
        {showConfidence && r.confidence !== null && <span className="ml-1.5 text-xs tabular-nums text-ink-400">{fmtPct(r.confidence)}</span>}
        {r.evidence && <span className="mt-0.5 block text-xs leading-5 text-ink-600" title={r.evidence}>“{truncate(r.evidence, 160)}”</span>}
      </span>
    </li>
  );
}

function EditEntityModal({ open, onClose, e, onSaved }: { open: boolean; onClose: () => void; e: EntityDetail; onSaved: () => void }) {
  const { wsId } = useWorkspace();
  const t = useT();
  const toast = useToast();
  const [form, setForm] = useState({ name: e.name, type: e.type, description: e.description || "", aliases: e.aliases.join(", ") });
  useEffect(() => { if (open) setForm({ name: e.name, type: e.type, description: e.description || "", aliases: e.aliases.join(", ") }); }, [open, e]);
  const mut = useMutation({
    mutationFn: () => api.updateEntity(wsId, e.id, { name: form.name.trim(), type: form.type, description: form.description.trim() || undefined, aliases: form.aliases.split(",").map((s) => s.trim()).filter(Boolean) }),
    onSuccess: () => { toast.ok(t("action.saved")); onSaved(); onClose(); }, onError: toast.err,
  });
  const typeOptions = TYPES.includes(form.type) ? TYPES : [...TYPES, form.type];
  return (
    <Modal open={open} onClose={onClose} title={t("graph.edit.title")}
      footer={<><button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button><button className="btn-primary" disabled={mut.isPending || !form.name.trim()} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />} {t("action.save")}</button></>}>
      <div className="space-y-3">
        <Field label={t("graph.edit.name")}><input className="input" value={form.name} onChange={(ev) => setForm({ ...form, name: ev.target.value })} /></Field>
        <Field label={t("graph.edit.type")}><select className="input" value={form.type} onChange={(ev) => setForm({ ...form, type: ev.target.value })}>{typeOptions.map((ty) => <option key={ty} value={ty}>{typeLabel(t, ty)}</option>)}</select></Field>
        <Field label={t("graph.edit.description")}><textarea className="input" rows={3} value={form.description} onChange={(ev) => setForm({ ...form, description: ev.target.value })} /></Field>
        <Field label={t("graph.edit.aliases")} hint={t("graph.edit.aliasesHint")}><input className="input" value={form.aliases} onChange={(ev) => setForm({ ...form, aliases: ev.target.value })} /></Field>
      </div>
    </Modal>
  );
}

function MergeModal({ open, onClose, e, onSaved }: { open: boolean; onClose: () => void; e: EntityDetail; onSaved: () => void }) {
  const { wsId } = useWorkspace();
  const t = useT();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Entity[]>([]);
  const search = useQuery({ queryKey: ["entities", wsId, "merge", q], queryFn: () => api.entities(wsId, { q, limit: 20 }), enabled: open && q.length > 0 });
  const mut = useMutation({ mutationFn: () => api.mergeEntities(wsId, e.id, picked.map((p) => p.id)), onSuccess: () => { toast.ok(t("graph.merge.done", { n: picked.length, name: e.name })); setPicked([]); onSaved(); onClose(); }, onError: toast.err });
  const results = search.data?.items.filter((x) => x.id !== e.id && !picked.some((p) => p.id === x.id)) || [];
  return (
    <Modal open={open} onClose={onClose} title={t("graph.merge.title", { name: e.name })}
      footer={<><button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button><button className="btn-primary" disabled={!picked.length || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />} {picked.length ? t("graph.merge.confirmN", { n: picked.length }) : t("graph.merge.confirm")}</button></>}>
      <p className="mb-3 text-sm leading-6 text-ink-700">{t("graph.merge.explain")}</p>
      <label className="relative block">
        <span className="sr-only">{t("graph.merge.search")}</span>
        <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
        <input className="input pl-9" placeholder={t("graph.merge.search")} value={q} onChange={(ev) => setQ(ev.target.value)} autoFocus />
      </label>
      {picked.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {picked.map((p) => (
            <button key={p.id} className="chip bg-brand-50 text-brand-700 hover:bg-brand-100" onClick={() => setPicked((xs) => xs.filter((x) => x.id !== p.id))} aria-label={t("graph.merge.unpick", { name: p.name })}>
              {p.name} <X className="h-3 w-3" />
            </button>
          ))}
        </div>
      )}
      {q && (
        <ul className="mt-3 max-h-60 overflow-y-auto rounded-xl border border-ink-100 p-1">
          {search.isLoading && <li><Loading /></li>}
          {!search.isLoading && results.length === 0 && <li className="px-3 py-4 text-center text-sm text-ink-600">{t("graph.search.none")}</li>}
          {results.map((x) => (
            <li key={x.id}>
              <button className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm hover:bg-paper" onClick={() => setPicked((xs) => [...xs, x])}>
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color(x.type) }} />
                <span className="min-w-0 flex-1 truncate text-ink-900">{x.name}</span>
                <span className="shrink-0 text-xs text-ink-600">{typeLabel(t, x.type)}</span>
                <Plus className="h-3.5 w-3.5 shrink-0 text-ink-400" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
