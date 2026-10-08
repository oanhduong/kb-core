// Mảnh dùng chung của studio mô hình dữ liệu: loại quy tắc, chip bằng chứng, nhãn kiểu dữ liệu, kết quả đối chiếu.
import clsx from "clsx";
import { ArrowLeftRight, Bot, Calculator, CircleHelp, Database, FileSpreadsheet, Hash, PenLine, Sigma, Table2, UserCheck } from "lucide-react";
import type { ReactNode } from "react";
import { MatchMeter } from "@/components/ui";
import { useI18n } from "@/i18n";
import { fmtCell } from "@/lib/format";
import type { ColumnCheckSpec, EvidenceView, RuleKind } from "@/lib/types";

export const KIND_ICON: Record<RuleKind, typeof Hash> = {
  input: PenLine, lookup: ArrowLeftRight, aggregate: Sigma, formula: Calculator, constant: Hash, unresolved: CircleHelp,
};

export function KindTag({ kind, className }: { kind: RuleKind; className?: string }) {
  const { t } = useI18n();
  const Icon = KIND_ICON[kind] || CircleHelp;
  return (
    <span className={clsx("inline-flex items-center gap-1.5 text-xs font-medium", kind === "unresolved" ? "text-alert-700" : "text-ink-600", className)} title={t(`models.kind.${kind}.hint`)}>
      <span className={clsx("flex h-6 w-6 items-center justify-center rounded-lg", kind === "unresolved" ? "bg-alert-50" : "bg-ink-100/70")}><Icon className="h-3.5 w-3.5" /></span>
      {t(`models.kind.${kind}`)}
    </span>
  );
}

const EV_ICON: Record<string, typeof Hash> = {
  formula: FileSpreadsheet, pivot: Table2, data_match: Database, name_match: Hash, ai: Bot, user: UserCheck, structure: Hash,
};

function weak(e: EvidenceView): boolean {
  const m = Number(e.detail?.matched ?? 0);
  const c = Number(e.detail?.compared ?? 0);
  return e.detail?.what !== "keys" && c > 0 && m / c < 0.9;
}

export function EvidenceChips({ items, limit = 4 }: { items: EvidenceView[]; limit?: number }) {
  const { t, pick } = useI18n();
  const seen = new Set<string>();
  const uniq = items.filter((e) => (seen.has(e.kind) ? false : (seen.add(e.kind), true)));
  if (!uniq.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {uniq.slice(0, limit).map((e, i) => {
        const Icon = EV_ICON[e.kind] || Hash;
        return (
          <span key={i} title={pick(e.text) + (e.note ? ` — ${e.note}` : "")}
            className={clsx("chip cursor-help", e.kind === "formula" || e.kind === "pivot" ? "bg-ink-900 text-white" : e.kind === "user" ? "bg-verify-50 text-verify-700" : e.kind === "ai" ? "bg-brand-50 text-brand-700" : "bg-ink-100/70 text-ink-700")}>
            <Icon className="h-3 w-3" />{t(`models.ev.${e.kind === "data_match" && weak(e) ? "data_tested" : e.kind}`)}
          </span>
        );
      })}
    </span>
  );
}

export function typeLabel(type: string, t: (k: string) => string): string {
  const k = ["integer", "double", "percent"].includes(type) ? "number" : type === "timestamp" ? "datetime" : type;
  return t(`models.type.${k}`);
}

/** Tỉ lệ khớp Excel kèm giải thích ngắn (khi công thức chờ cột khác). */
export function CheckLine({ check, columnLabel }: { check: ColumnCheckSpec | null | undefined; columnLabel?: (name: string) => string }) {
  const { t } = useI18n();
  if (!check) return <span className="text-xs text-ink-400">{t("models.check.none")}</span>;
  if (check.rate === null || check.rate === undefined) return <span className="text-xs text-ink-400">{t("models.check.empty")}</span>;
  return (
    <span className="inline-flex flex-col gap-0.5">
      <MatchMeter rate={check.rate} />
      {check.basis === "isolated" && (
        <span className="text-[11px] leading-4 text-ink-600">{t("models.check.isolated", { cols: (check.waiting_on || []).map((c) => columnLabel?.(c) || c).join(", ") })}</span>
      )}
    </span>
  );
}

export function SamplesTable({ check, type }: { check: ColumnCheckSpec | null | undefined; type: string }) {
  const { t } = useI18n();
  if (!check?.samples?.length) return null;
  return (
    <div className="overflow-hidden rounded-xl border border-ink-100">
      <table className="w-full text-sm">
        <thead><tr><th className="th">{t("models.samples.row")}</th><th className="th">{t("models.samples.excel")}</th><th className="th">{t("models.samples.ours")}</th></tr></thead>
        <tbody>
          {check.samples.slice(0, 6).map((s, i) => (
            <tr key={i}>
              <td className="td tabular-nums text-ink-600">{s.row}</td>
              <td className="td"><span className="marker">{s.expected === null ? "—" : fmtCell(s.expected, type)}</span></td>
              <td className="td">{s.actual === null || s.actual === undefined ? <span className="text-ink-400">{t("models.samples.blank")}</span> : fmtCell(s.actual, type)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Section({ title, hint, children, aside }: { title: ReactNode; hint?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="mb-7">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="font-display text-[17px] font-semibold text-ink-900">{title}</h3>
          {hint && <p className="mt-0.5 text-sm leading-6 text-ink-600">{hint}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}
