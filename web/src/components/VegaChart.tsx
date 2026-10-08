// Render Vega-Lite spec (từ analytics/charts.py) bằng vega-embed với bảng màu của hệ thiết kế; menu chỉ có tải ảnh.
import { AlertCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import embed, { type Result } from "vega-embed";
import { useI18n } from "@/i18n";
import { fmtNumber } from "@/lib/format";
import type { Chart } from "@/lib/types";

export const CHART_PALETTE = ["#5B4CF5", "#0E9F6E", "#F59E0B", "#E5484D", "#3A3680", "#8C89B8", "#14B8A6", "#EC4899"];
const FONT = "'Be Vietnam Pro', ui-sans-serif, system-ui, sans-serif";
// formatLocale của vega là trạng thái toàn cục -> luôn truyền rõ cả hai ngôn ngữ.
const VI_LOCALE = { decimal: ",", thousands: ".", grouping: [3], currency: ["", " ₫"] };
const EN_LOCALE = { decimal: ".", thousands: ",", grouping: [3], currency: ["$", ""] };
const INK = { 900: "#1E1B4B", 700: "#3A3680", 600: "#57538F", 400: "#8C89B8", 100: "#E7E6F3" };

function themeConfig(): Record<string, unknown> {
  const main = CHART_PALETTE[0];
  return {
    font: FONT,
    background: "transparent",
    padding: 4,
    view: { stroke: null },
    axis: {
      labelColor: INK[600], titleColor: INK[700], labelFont: FONT, titleFont: FONT, labelFontSize: 11, titleFontSize: 12,
      titleFontWeight: 500, gridColor: INK[100], domainColor: INK[100], tickColor: INK[100], labelPadding: 6, titlePadding: 10,
    },
    axisBand: { grid: false },
    legend: { labelColor: INK[600], titleColor: INK[700], labelFont: FONT, titleFont: FONT, titleFontWeight: 500, symbolType: "circle" },
    title: { color: INK[900], font: FONT, fontWeight: 600, fontSize: 14, anchor: "start" },
    header: { labelColor: INK[600], titleColor: INK[700], labelFont: FONT, titleFont: FONT },
    bar: { color: main, cornerRadiusEnd: 3 },
    line: { color: main, strokeWidth: 2 },
    point: { color: main, filled: true, size: 50 },
    area: { color: main, opacity: 0.85 },
    arc: { stroke: "#FFFFFF", strokeWidth: 1.5 },
    rect: { color: main },
    rule: { color: INK[400] },
    text: { color: INK[900], font: FONT },
    range: { category: CHART_PALETTE, ordinal: { scheme: "purples" }, ramp: { scheme: "purples" } },
  };
}

export function VegaChart({ spec, height, className }: { spec: Record<string, unknown>; height?: number; className?: string }) {
  const { t, lang } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let view: Result | null = null;
    let cancelled = false;
    const el = ref.current;
    if (!el) return;
    const s: Record<string, unknown> = { ...spec, width: "container", autosize: { type: "fit", contains: "padding" } };
    if (height) s.height = height;
    // spec.config của server (nếu có) được ưu tiên hơn theme mặc định.
    s.config = { ...themeConfig(), ...((spec.config as object) || {}) };
    embed(el, s as never, {
      actions: { export: true, source: false, compiled: false, editor: false },
      renderer: "svg",
      formatLocale: lang === "vi" ? VI_LOCALE : EN_LOCALE,
      i18n: { PNG_ACTION: t("explore.chart.savePng"), SVG_ACTION: t("explore.chart.saveSvg"), CLICK_TO_VIEW_ACTIONS: t("explore.chart.options") } as never,
    })
      .then((r) => { if (cancelled) r.finalize(); else { view = r; setErr(null); } })
      .catch((e) => setErr(String(e?.message || e)));
    return () => { cancelled = true; view?.finalize(); };
  }, [spec, height, lang, t]);
  if (err) {
    return (
      <div className="flex items-start gap-2 rounded-xl border border-alert-500/25 bg-alert-50 p-3 text-sm text-alert-700">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{t("chart.failed", { error: err })}
      </div>
    );
  }
  return <div ref={ref} className={className} style={{ width: "100%" }} />;
}

/** Một con số lớn (KPI) — dùng cho chart type "kpi" và block kpi của báo cáo. */
export function KpiValue({ label, value }: { label?: string | null; value: unknown }) {
  return (
    <div className="inline-flex min-w-[180px] flex-col gap-1 rounded-xl border border-ink-100 bg-white px-5 py-4 shadow-card">
      {label && <span className="text-[13px] font-medium text-ink-600">{label}</span>}
      <span className="font-display text-4xl font-semibold tabular-nums tracking-tight text-ink-900">{fmtNumber(value)}</span>
    </div>
  );
}

/** Chart từ API: vega_lite | kpi | table (table do caller hiển thị). */
export function ChartView({ chart, height = 260 }: { chart: Chart | null | undefined; height?: number }) {
  if (!chart) return null;
  if (chart.type === "kpi" && chart.kpi) return <KpiValue label={chart.title || chart.kpi.label} value={chart.kpi.value} />;
  if (chart.vega_lite) return <VegaChart spec={chart.vega_lite} height={height} />;
  return null;
}
