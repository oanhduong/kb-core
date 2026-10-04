// Render Vega-Lite spec (từ analytics/charts.py) bằng vega-embed; actions chỉ export ảnh.
import { useEffect, useRef, useState } from "react";
import embed, { type Result } from "vega-embed";
import type { Chart } from "@/lib/types";
import { fmtNumber } from "@/lib/format";

export function VegaChart({ spec, height, className }: { spec: Record<string, unknown>; height?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let view: Result | null = null;
    let cancelled = false;
    const el = ref.current;
    if (!el) return;
    const s: Record<string, unknown> = { ...spec, width: "container" };
    if (height) s.height = height;
    s.config = { ...(spec.config as object), font: "Inter, sans-serif", axis: { labelColor: "#475569", titleColor: "#334155", gridColor: "#e2e8f0" }, legend: { labelColor: "#475569", titleColor: "#334155" }, view: { stroke: null }, bar: { color: "#0d9488" }, line: { color: "#0d9488", strokeWidth: 2 }, point: { color: "#0d9488", filled: true }, area: { color: "#0d9488" }, range: { category: ["#0d9488", "#2563eb", "#f59e0b", "#ef4444", "#8b5cf6", "#10b981", "#64748b", "#ec4899"] }, ...(spec.config as object) };
    embed(el, s as never, { actions: { export: true, source: false, compiled: false, editor: false }, renderer: "canvas", i18n: { PNG_ACTION: "Tải PNG", SVG_ACTION: "Tải SVG", CLICK_TO_VIEW_ACTIONS: "Tuỳ chọn" } as never })
      .then((r) => { if (cancelled) r.finalize(); else { view = r; setErr(null); } })
      .catch((e) => setErr(String(e?.message || e)));
    return () => { cancelled = true; view?.finalize(); };
  }, [spec, height]);
  if (err) return <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">Không vẽ được biểu đồ: {err}</div>;
  return <div ref={ref} className={className} style={{ width: "100%" }} />;
}

/** Chart từ API: vega_lite | kpi | table (table do caller hiển thị). */
export function ChartView({ chart, height = 260 }: { chart: Chart | null | undefined; height?: number }) {
  if (!chart) return null;
  if (chart.type === "kpi" && chart.kpi) {
    return (
      <div className="inline-flex flex-col rounded-xl border border-slate-200 bg-white px-5 py-3">
        <span className="text-xs uppercase tracking-wide text-slate-500">{chart.title || chart.kpi.label}</span>
        <span className="text-3xl font-semibold text-brand-700">{fmtNumber(chart.kpi.value)}</span>
      </div>
    );
  }
  if (chart.vega_lite) return <VegaChart spec={chart.vega_lite} height={height} />;
  return null;
}
