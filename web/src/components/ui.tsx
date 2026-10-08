// Thành phần UI dùng chung (song ngữ). Button/input là class CSS (styles.css); ở đây là phần cần logic/markup lặp lại.
import clsx from "clsx";
import {
  AlertCircle, AudioLines, CheckCircle2, File, FileSpreadsheet, FileText, FileType2, Film, Image as ImageIcon, Info, Loader2, Presentation, X,
} from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useT } from "@/i18n";
import { statusLabel } from "@/lib/format";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx("h-4 w-4 animate-spin text-brand-600", className)} />;
}

export function Loading({ label }: { label?: string }) {
  const t = useT();
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-600">
      <Spinner /> {label || t("loading")}
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const t = useT();
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-3 rounded-xl border border-alert-500/25 bg-alert-50 p-3.5 text-sm text-alert-700">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex-1">{msg}</div>
      {retry && <button className="btn-secondary btn-sm" onClick={retry}>{t("action.retry")}</button>}
    </div>
  );
}

export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
      {icon && <div className="mb-1 flex h-14 w-14 items-center justify-center rounded-2xl bg-ink-100/60 text-ink-600 [&>svg]:h-7 [&>svg]:w-7">{icon}</div>}
      <div className="font-display text-lg font-semibold text-ink-900">{title}</div>
      {hint && <div className="max-w-md text-sm leading-6 text-ink-600">{hint}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  done: "bg-verify-50 text-verify-700", active: "bg-verify-50 text-verify-700", ready: "bg-verify-50 text-verify-700",
  approved: "bg-verify-50 text-verify-700", answered: "bg-verify-50 text-verify-700", resolved: "bg-verify-50 text-verify-700",
  processing: "bg-brand-50 text-brand-700", generating: "bg-brand-50 text-brand-700", analyzing: "bg-brand-50 text-brand-700",
  building: "bg-brand-50 text-brand-700", queued: "bg-ink-100/70 text-ink-700", draft: "bg-ink-100/70 text-ink-700",
  review: "bg-marker-100 text-marker-700", pending: "bg-marker-100 text-marker-700", pending_review: "bg-marker-100 text-marker-700",
  blocked: "bg-marker-100 text-marker-700", open: "bg-marker-100 text-marker-700", answered_general: "bg-brand-50 text-brand-700",
  error: "bg-alert-50 text-alert-700", rejected: "bg-alert-50 text-alert-700", abstained: "bg-marker-100 text-marker-700",
  no_citation: "bg-marker-100 text-marker-700", superseded: "bg-ink-100/70 text-ink-600", deleted: "bg-ink-100/70 text-ink-600",
};
const BUSY = new Set(["processing", "generating", "analyzing", "building"]);

export function StatusBadge({ status, className }: { status: string | null | undefined; className?: string }) {
  if (!status) return null;
  return (
    <span className={clsx("chip", STATUS_TONE[status] || "bg-ink-100/70 text-ink-700", className)}>
      {BUSY.has(status) && <Loader2 className="h-3 w-3 animate-spin" />}
      {statusLabel(status)}
    </span>
  );
}

type Tone = "slate" | "brand" | "amber" | "red" | "blue" | "violet" | "emerald" | "marker" | "ink";
const TONES: Record<Tone, string> = {
  slate: "bg-ink-100/70 text-ink-700", ink: "bg-ink-900 text-white", brand: "bg-brand-50 text-brand-700", violet: "bg-brand-50 text-brand-700",
  blue: "bg-brand-50 text-brand-700", amber: "bg-marker-100 text-marker-700", marker: "bg-marker-200 text-ink-900",
  red: "bg-alert-50 text-alert-700", emerald: "bg-verify-50 text-verify-700",
};

export function Badge({ children, tone = "slate", className, title }: { children: ReactNode; tone?: Tone; className?: string; title?: string }) {
  return <span title={title} className={clsx("chip", TONES[tone], className)}>{children}</span>;
}

export function PageHeader({ title, subtitle, actions, kicker }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; kicker?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {kicker && <div className="mb-1 text-sm text-ink-600">{kicker}</div>}
        <h1 className="font-display text-[28px] font-semibold leading-tight text-ink-900">{title}</h1>
        {subtitle && <div className="mt-1.5 max-w-3xl text-[15px] leading-6 text-ink-600">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

export function Modal({ open, onClose, title, children, width = "max-w-lg", footer }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string; footer?: ReactNode }) {
  const t = useT();
  useEscape(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/45 p-4 backdrop-blur-[2px]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal className={clsx("panel flex max-h-[90vh] w-full animate-rise flex-col shadow-lift", width)}>
        <div className="flex items-center justify-between gap-3 px-6 pb-2 pt-5">
          <h3 className="font-display text-lg font-semibold text-ink-900">{title}</h3>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label={t("action.close")}><X className="h-4 w-4" /></button>
        </div>
        <div className="overflow-y-auto px-6 py-3">{children}</div>
        {footer && <div className="flex justify-end gap-2 px-6 pb-5 pt-3">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, title, children, width = "max-w-xl" }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string }) {
  const t = useT();
  useEscape(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink-950/35" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal className={clsx("flex h-full w-full flex-col bg-white shadow-lift", width)}>
        <div className="flex items-center justify-between gap-3 border-b border-ink-100 px-6 py-4">
          <h3 className="truncate font-display text-lg font-semibold text-ink-900">{title}</h3>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label={t("action.close")}><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { key: T; label: ReactNode; count?: number }[]; value: T; onChange: (k: T) => void }) {
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-ink-100">
      {tabs.map((tb) => (
        <button key={tb.key} role="tab" aria-selected={value === tb.key} onClick={() => onChange(tb.key)}
          className={clsx("-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition",
            value === tb.key ? "border-ink-900 text-ink-900" : "border-transparent text-ink-600 hover:text-ink-900")}>
          {tb.label}
          {tb.count !== undefined && <span className={clsx("ml-1.5 rounded-full px-1.5 text-xs", value === tb.key ? "bg-ink-900 text-white" : "bg-ink-100 text-ink-700")}>{tb.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({ options, value, onChange, className }: { options: { key: T; label: ReactNode; title?: string }[]; value: T; onChange: (v: T) => void; className?: string }) {
  return (
    <div className={clsx("seg", className)}>
      {options.map((o) => <button key={o.key} type="button" title={o.title} aria-pressed={o.key === value} onClick={() => onChange(o.key)}>{o.label}</button>)}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs leading-5 text-ink-600">{hint}</span>}
    </label>
  );
}

export function KPI({ label, value, hint, tone }: { label: ReactNode; value: ReactNode; hint?: ReactNode; tone?: "amber" | "red" | "emerald" }) {
  return (
    <div className="card p-4">
      <div className="text-[13px] font-medium text-ink-600">{label}</div>
      <div className={clsx("kpi-value mt-1", tone === "amber" && "text-marker-700", tone === "red" && "text-alert-700", tone === "emerald" && "text-verify-700")}>{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-600">{hint}</div>}
    </div>
  );
}

export function Confirm({ open, onClose, onConfirm, title, message, danger, busy, confirmLabel }: { open: boolean; onClose: () => void; onConfirm: () => void; title: string; message: ReactNode; danger?: boolean; busy?: boolean; confirmLabel?: string }) {
  const t = useT();
  return (
    <Modal open={open} onClose={onClose} title={title} width="max-w-md"
      footer={<><button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button><button className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm} disabled={busy}>{busy && <Spinner className="text-current" />} {confirmLabel || t("action.confirm")}</button></>}>
      <div className="text-sm leading-6 text-ink-700">{message}</div>
    </Modal>
  );
}

/** Thanh tỉ lệ khớp: xanh = khớp nguồn, vàng = một phần, đỏ = lệch. */
export function MatchMeter({ rate, className, label }: { rate: number | null | undefined; className?: string; label?: ReactNode }) {
  if (rate === null || rate === undefined) return null;
  const pct = Math.round(rate * 1000) / 10;
  const tone = rate >= 0.999 ? "bg-verify-500" : rate >= 0.9 ? "bg-marker-300" : "bg-alert-500";
  return (
    <span className={clsx("inline-flex items-center gap-2", className)}>
      <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-ink-100"><span className={clsx("absolute inset-y-0 left-0 rounded-full", tone)} style={{ width: `${Math.max(3, pct)}%` }} /></span>
      <span className="text-xs font-semibold tabular-nums text-ink-800">{pct}%</span>
      {label && <span className="text-xs text-ink-600">{label}</span>}
    </span>
  );
}

const SRC_ICON: Record<string, { icon: typeof File; cls: string }> = {
  xlsx: { icon: FileSpreadsheet, cls: "bg-verify-50 text-verify-700" }, csv: { icon: FileSpreadsheet, cls: "bg-verify-50 text-verify-700" },
  docx: { icon: FileText, cls: "bg-brand-50 text-brand-700" }, pdf: { icon: FileType2, cls: "bg-alert-50 text-alert-700" },
  pptx: { icon: Presentation, cls: "bg-marker-100 text-marker-700" }, image: { icon: ImageIcon, cls: "bg-marker-100 text-marker-700" },
  audio: { icon: AudioLines, cls: "bg-ink-100 text-ink-800" }, video: { icon: Film, cls: "bg-ink-100 text-ink-800" },
  txt: { icon: FileText, cls: "bg-ink-100 text-ink-700" }, md: { icon: FileText, cls: "bg-ink-100 text-ink-700" },
};

export function SourceIcon({ type, size = "md" }: { type: string; size?: "sm" | "md" | "lg" }) {
  const s = SRC_ICON[type] || { icon: File, cls: "bg-ink-100 text-ink-700" };
  const Icon = s.icon;
  const box = size === "sm" ? "h-7 w-7 rounded-lg" : size === "lg" ? "h-12 w-12 rounded-2xl" : "h-9 w-9 rounded-xl";
  const ic = size === "sm" ? "h-3.5 w-3.5" : size === "lg" ? "h-6 w-6" : "h-[18px] w-[18px]";
  return <span className={clsx("inline-flex shrink-0 items-center justify-center", box, s.cls)}><Icon className={ic} /></span>;
}

// ---------------------------------------------------------------- toast
interface Toast { id: number; kind: "ok" | "err" | "info"; text: string }
const ToastCtx = createContext<{ push: (kind: Toast["kind"], text: string) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = ++seq.current;
    setItems((xs) => [...xs, { id, kind, text }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "err" ? 7000 : 3500);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[340px] flex-col gap-2" aria-live="polite">
        {items.map((x) => (
          <div key={x.id} className={clsx("pointer-events-auto flex animate-rise items-start gap-2.5 rounded-xl px-4 py-3 text-sm shadow-lift",
            x.kind === "err" ? "bg-alert-700 text-white" : "bg-ink-900 text-white")}>
            {x.kind === "ok" ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-verify-500" /> : x.kind === "err" ? <AlertCircle className="mt-0.5 h-4 w-4" /> : <Info className="mt-0.5 h-4 w-4 text-brand-300" />}
            <div className="flex-1 leading-5">{x.text}</div>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  const v = useContext(ToastCtx);
  if (!v) throw new Error("useToast ngoài ToastProvider");
  return {
    ok: (x: string) => v.push("ok", x),
    err: (e: unknown) => v.push("err", e instanceof Error ? e.message : String(e)),
    info: (x: string) => v.push("info", x),
  };
}

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const t = useT();
  const [done, setDone] = useState(false);
  return (
    <button type="button" className={clsx("btn-ghost btn-sm", className)} onClick={async () => { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200); }}>
      {done ? t("action.copied") : t("action.copy")}
    </button>
  );
}

export function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const t = useT();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="flex items-center justify-between gap-2 py-3 text-sm text-ink-600">
      <span>{t("pagination.range", { from: (page - 1) * pageSize + 1, to: Math.min(page * pageSize, total), total })}</span>
      <div className="flex gap-1">
        <button className="btn-secondary btn-sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>{t("action.previous")}</button>
        <button className="btn-secondary btn-sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>{t("action.next")}</button>
      </div>
    </div>
  );
}

/** Khối chỉ dẫn ngắn có thể đóng (ghi nhớ theo key). */
export function Tip({ id, children }: { id: string; children: ReactNode }) {
  const key = `kbx.tip.${id}`;
  const [hidden, setHidden] = useState(() => { try { return localStorage.getItem(key) === "1"; } catch { return false; } });
  if (hidden) return null;
  return (
    <div className="mb-5 flex items-start gap-3 rounded-xl border border-brand-100 bg-brand-50/60 px-4 py-3 text-sm leading-6 text-ink-800">
      <Info className="mt-1 h-4 w-4 shrink-0 text-brand-600" />
      <div className="flex-1">{children}</div>
      <button className="btn-ghost btn-sm -mr-1" onClick={() => { setHidden(true); try { localStorage.setItem(key, "1"); } catch { /* */ } }}><X className="h-3.5 w-3.5" /></button>
    </div>
  );
}
