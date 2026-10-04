// Bảng kết quả truy vấn: cột có kiểu, căn phải số, sticky header, giới hạn dòng + sort client.
import clsx from "clsx";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { fmtCell } from "@/lib/format";
import type { TableColumn } from "@/lib/types";

const NUM = /int|double|decimal|float|hugeint|real/i;

export function DataGrid({ columns, rows, maxRows = 200, maxHeight = 420, compact, className }: { columns: TableColumn[]; rows: unknown[][]; maxRows?: number; maxHeight?: number; compact?: boolean; className?: string }) {
  const [sort, setSort] = useState<{ i: number; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    const xs = rows.slice(0, maxRows);
    if (!sort) return xs;
    const { i, dir } = sort;
    return [...xs].sort((a, b) => {
      const x = a[i], y = b[i];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
      return String(x).localeCompare(String(y), "vi") * dir;
    });
  }, [rows, sort, maxRows]);
  if (!columns.length) return <div className="py-4 text-center text-sm text-slate-500">Không có dữ liệu</div>;
  return (
    <div className={clsx("overflow-auto rounded-lg border border-slate-200", className)} style={{ maxHeight }}>
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10">
          <tr>
            {columns.map((c, i) => (
              <th key={c.name + i} className={clsx("th cursor-pointer select-none", NUM.test(c.type) && "text-right")} title={c.type}
                onClick={() => setSort((s) => (s?.i === i ? (s.dir === 1 ? { i, dir: -1 } : null) : { i, dir: 1 }))}>
                <span className="inline-flex items-center gap-1">{c.name}{sort?.i === i && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, ri) => (
            <tr key={ri} className="hover:bg-slate-50">
              {columns.map((c, ci) => (
                <td key={ci} className={clsx("td whitespace-nowrap", compact && "py-1", NUM.test(c.type) && "text-right font-mono text-[13px]", (r[ci] === null || r[ci] === undefined) && "text-slate-300")}>
                  {r[ci] === null || r[ci] === undefined ? "∅" : fmtCell(r[ci], c.type)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows && <div className="border-t border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-500">Hiển thị {maxRows}/{rows.length} dòng</div>}
    </div>
  );
}
