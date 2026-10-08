// Bảng kết quả truy vấn: tiêu đề cột thân thiện (original/labels, tên SQL chỉ ở tooltip khi bật chi tiết kỹ thuật),
// sticky header, số căn phải dạng tabular, ô trống là gạch ngang mờ, sort phía client, giới hạn dòng.
import clsx from "clsx";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { getLang, useT } from "@/i18n";
import { fmtCell, fmtNumber } from "@/lib/format";
import type { TableColumn } from "@/lib/types";
import { usePrefs } from "@/state/prefs";

const NUM = /int|double|decimal|float|hugeint|real|numeric/i;

export type ColumnKind = "number" | "date" | "text" | "bool" | "other";

/** Loại cột bằng lời (Number/Date/Text…) thay cho kiểu SQL. */
export function columnKind(type: string | undefined): ColumnKind {
  if (!type) return "other";
  if (NUM.test(type)) return "number";
  if (/date|time/i.test(type)) return "date";
  if (/bool/i.test(type)) return "bool";
  if (/char|text|string|uuid|enum/i.test(type)) return "text";
  return "other";
}

/** "tong_ton_kho" -> "Tong ton kho" — dùng khi không có tên gốc. */
export function humanize(name: string): string {
  const s = name.replace(/[_]+/g, " ").replace(/\s+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : name;
}

/** Tên hiển thị của cột: nhãn truyền vào > tên gốc trong file > tên SQL viết lại cho dễ đọc. */
export function columnLabel(c: TableColumn, labels?: Record<string, string>): string {
  return labels?.[c.name] || (c.original && c.original.trim()) || humanize(c.name);
}

export function DataGrid({ columns, rows, maxRows = 200, maxHeight = 420, compact, className, labels, total }: {
  columns: TableColumn[];
  rows: unknown[][];
  maxRows?: number;
  maxHeight?: number;
  compact?: boolean;
  className?: string;
  /** Tên hiển thị theo tên cột SQL (tuỳ chọn). */
  labels?: Record<string, string>;
  /** Tổng số dòng thật (nếu rows chỉ là một phần) — để ghi chú "hiển thị n/total". */
  total?: number;
}) {
  const t = useT();
  const { advanced } = usePrefs();
  const [sort, setSort] = useState<{ i: number; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    const xs = rows.slice(0, maxRows);
    if (!sort) return xs;
    const { i, dir } = sort;
    const lang = getLang();
    return [...xs].sort((a, b) => {
      const x = a[i], y = b[i];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
      return String(x).localeCompare(String(y), lang) * dir;
    });
  }, [rows, sort, maxRows]);

  if (!columns.length) return <div className="rounded-xl border border-dashed border-ink-100 py-8 text-center text-sm text-ink-600">{t("grid.empty")}</div>;
  const shownTotal = Math.max(total ?? 0, rows.length);
  const cycle = (i: number) => setSort((s) => (s?.i === i ? (s.dir === 1 ? { i, dir: -1 } : null) : { i, dir: 1 }));

  return (
    <div className={clsx("overflow-hidden rounded-xl border border-ink-100 bg-white", className)}>
      <div className="overflow-auto" style={{ maxHeight }}>
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10">
            <tr>
              {columns.map((c, i) => {
                const num = NUM.test(c.type);
                const label = columnLabel(c, labels);
                const ariaSort = sort?.i === i ? (sort.dir === 1 ? "ascending" : "descending") : "none";
                return (
                  <th key={c.name + i} scope="col" aria-sort={ariaSort} className={clsx("th p-0", num && "text-right")}
                    title={advanced ? `${c.name} · ${c.type}` : c.description || undefined}>
                    <button type="button" onClick={() => cycle(i)}
                      className={clsx("flex w-full items-center gap-1 px-3 text-xs font-semibold text-ink-600 hover:text-ink-900", compact ? "py-2" : "py-2.5", num && "justify-end")}>
                      <span className="max-w-[260px] truncate">{label}</span>
                      {sort?.i === i && (sort.dir === 1 ? <ArrowUp className="h-3 w-3 shrink-0 text-brand-600" /> : <ArrowDown className="h-3 w-3 shrink-0 text-brand-600" />)}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, ri) => (
              <tr key={ri} className="transition-colors hover:bg-paper">
                {columns.map((c, ci) => {
                  const v = r[ci];
                  const empty = v === null || v === undefined || v === "";
                  const num = NUM.test(c.type);
                  return (
                    <td key={ci} className={clsx("td whitespace-nowrap", compact && "py-1.5", num && "text-right tabular-nums")}>
                      {empty ? <span className="text-ink-300" aria-label={t("explore.grid.emptyCell")}>—</span> : <CellText text={fmtCell(v, c.type)} />}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shownTotal > sorted.length && (
        <div className="border-t border-ink-100 bg-paper px-3 py-2 text-xs text-ink-600">
          {t("grid.showing", { n: fmtNumber(sorted.length), total: fmtNumber(shownTotal) })}
        </div>
      )}
    </div>
  );
}

function CellText({ text }: { text: string }) {
  if (text.length <= 48) return <>{text}</>;
  return <span className="block max-w-[360px] truncate" title={text}>{text}</span>;
}
