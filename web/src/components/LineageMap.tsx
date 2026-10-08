// Sơ đồ dòng dữ liệu của 1 mô hình: sheet phụ (trái) -> sheet chính (giữa) -> view kết quả (phải).
// Đường nối = quan hệ đã phát hiện: liền màu xanh = đã duyệt, gạch nối tím = đề xuất; sheet không dùng nằm mờ phía dưới.
// Đường được "vẽ" một lần khi mở (khoảnh khắc chuyển động duy nhất của màn hình), tôn trọng reduced-motion qua CSS.
import clsx from "clsx";
import { useMemo } from "react";
import { useT } from "@/i18n";
import { fmtNumber } from "@/lib/format";
import type { ModelSpecView, RelationshipSpec, TableSpecView } from "@/lib/types";

const NW = 236;
const NH = 52;
const GAP = 14;
const COL1 = 16;
const COL2 = 420;
const COL3 = 760;

function trunc(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function LineageMap({ spec, selected, onSelect, viewName, built }: {
  spec: ModelSpecView; selected?: string | null; onSelect?: (tableKey: string | null) => void; viewName: string; built: boolean;
}) {
  const t = useT();
  const main = spec.tables.find((x) => x.key === spec.main_table);
  const rels = spec.relationships.filter((r) => r.status !== "rejected");
  const sources = useMemo(() => {
    const linked = new Set(rels.map((r) => r.source_table));
    return spec.tables.filter((x) => x.key !== spec.main_table && x.role !== "ignored" && linked.has(x.key));
  }, [spec.tables, spec.main_table, rels]);
  const unused = spec.tables.filter((x) => x.key !== spec.main_table && !sources.includes(x));
  const usedBy = useMemo(() => {
    const m: Record<string, number> = {};
    for (const c of spec.columns) if (c.rule.relationship_id) m[c.rule.relationship_id] = (m[c.rule.relationship_id] || 0) + 1;
    return m;
  }, [spec.columns]);
  if (!main) return null;
  const h = Math.max(sources.length * (NH + GAP) - GAP, 3 * NH) + 32;
  const mainY = h / 2 - 48;
  const pos = (i: number) => 16 + i * (NH + GAP);
  const mainCols = new Map(main.columns.map((c) => [c.name, c.label]));
  const relOf = (key: string) => rels.filter((r) => r.source_table === key);

  const node = (x: number, y: number, tb: TableSpecView, rel?: RelationshipSpec) => {
    const active = selected === tb.key;
    const conf = rel?.status === "confirmed";
    return (
      <g key={tb.key} transform={`translate(${x},${y})`} className="cursor-pointer" onClick={() => onSelect?.(active ? null : tb.key)} role="button" tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && onSelect?.(active ? null : tb.key)}>
        <rect width={NW} height={NH} rx={12} className={clsx("transition", active ? "fill-ink-900" : "fill-white")} stroke={active ? "#1E1B4B" : "#E7E6F3"} />
        <circle cx={18} cy={NH / 2} r={5} fill={conf ? "#0E9F6E" : "#8E7CFB"} />
        <text x={32} y={22} className={clsx("text-[13px] font-semibold", active ? "fill-white" : "fill-ink-900")}>{trunc(tb.title, 26)}</text>
        <text x={32} y={39} className={clsx("text-[11px]", active ? "fill-ink-300" : "fill-ink-600")}>
          {t("models.map.rows", { n: fmtNumber(tb.row_count) })}{rel && usedBy[rel.id] ? ` · ${t("models.map.feeds", { n: usedBy[rel.id] })}` : ""}
        </text>
      </g>
    );
  };

  return (
    <div className="dotgrid overflow-x-auto rounded-2xl border border-ink-100 bg-paper">
      <svg viewBox={`0 0 ${COL3 + 220} ${h}`} className="min-w-[760px]" style={{ width: "100%", height: "auto" }} role="img" aria-label={t("models.map.aria")}>
        {sources.map((s, i) => relOf(s.key).map((r, j) => {
          const y1 = pos(i) + NH / 2 + j * 6;
          const y2 = mainY + 30 + ((i + 1) / (sources.length + 1)) * 36;
          const x1 = COL1 + NW;
          const x2 = COL2;
          const d = `M${x1},${y1} C${x1 + 90},${y1} ${x2 - 90},${y2} ${x2},${y2}`;
          const conf = r.status === "confirmed";
          const dim = selected && selected !== s.key;
          const key = r.keys[0];
          const label = key ? mainCols.get(key.base_column) || key.base_column : "";
          return (
            <g key={r.id} opacity={dim ? 0.18 : 1} className="transition-opacity">
              <path d={d} fill="none" stroke={conf ? "#0E9F6E" : "#8E7CFB"} strokeWidth={selected === s.key ? 2.6 : 1.6}
                strokeDasharray={conf ? undefined : "5 5"} pathLength={400} className={conf ? "animate-draw" : ""} style={conf ? { strokeDasharray: 400 } : undefined}>
                <title>{label}</title>
              </path>
              {selected === s.key && <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 6} textAnchor="middle" className="fill-ink-800 text-[11px] font-medium">{trunc(label, 22)}</text>}
            </g>
          );
        }))}
        {sources.map((s, i) => node(COL1, pos(i), s, relOf(s.key)[0]))}
        {/* sheet chính */}
        <g transform={`translate(${COL2},${mainY})`}>
          <rect width={NW} height={96} rx={16} fill="#1E1B4B" />
          <rect x={14} y={62} width={64} height={8} rx={4} fill="#FDE68A" />
          <text x={16} y={28} className="fill-ink-300 text-[11px]">{t("models.map.main")}</text>
          <text x={16} y={50} className="fill-white text-[15px] font-semibold">{trunc(main.title, 24)}</text>
          <text x={88} y={70} className="fill-ink-300 text-[11px]">{t("models.map.columns", { n: main.columns.length })}</text>
        </g>
        <path d={`M${COL2 + NW},${mainY + 48} L${COL3},${mainY + 48}`} stroke={built ? "#0E9F6E" : "#B9B6D9"} strokeWidth={2} strokeDasharray={built ? undefined : "4 5"} />
        <g transform={`translate(${COL3},${mainY + 16})`}>
          <rect width={208} height={64} rx={14} fill="#fff" stroke={built ? "#0E9F6E" : "#E7E6F3"} strokeWidth={built ? 1.6 : 1} />
          <text x={14} y={26} className="fill-ink-600 text-[11px]">{built ? t("models.map.viewReady") : t("models.map.viewPending")}</text>
          <text x={14} y={46} className="fill-ink-900 font-mono text-[12px] font-medium">{trunc(viewName, 26)}</text>
        </g>
      </svg>
      {unused.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-ink-100 bg-white/70 px-4 py-2.5 text-xs text-ink-600">
          <span>{t("models.map.unused")}</span>
          {unused.map((u) => <button key={u.key} onClick={() => onSelect?.(u.key)} className={clsx("chip bg-ink-100/70 text-ink-700 hover:bg-ink-100", selected === u.key && "ring-1 ring-ink-700")}>{u.title}</button>)}
        </div>
      )}
    </div>
  );
}
