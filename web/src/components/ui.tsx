// Primitive UI: Button đã là class CSS; ở đây là các thành phần cần logic/markup lặp lại.
import clsx from "clsx";
import { AlertCircle, CheckCircle2, Info, Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { STATUS_LABEL } from "@/lib/format";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx("h-4 w-4 animate-spin text-brand-600", className)} />;
}

export function Loading({ label = "Đang tải…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
      <Spinner /> {label}
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex-1">{msg}</div>
      {retry && (
        <button className="btn-secondary btn-sm" onClick={retry}>Thử lại</button>
      )}
    </div>
  );
}

export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      {icon && <div className="text-slate-300">{icon}</div>}
      <div className="text-sm font-medium text-slate-700">{title}</div>
      {hint && <div className="max-w-md text-sm text-slate-500">{hint}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

const STATUS_COLOR: Record<string, string> = {
  done: "bg-emerald-50 text-emerald-700", active: "bg-emerald-50 text-emerald-700", ready: "bg-emerald-50 text-emerald-700",
  approved: "bg-emerald-50 text-emerald-700", answered: "bg-emerald-50 text-emerald-700", resolved: "bg-emerald-50 text-emerald-700",
  processing: "bg-blue-50 text-blue-700", generating: "bg-blue-50 text-blue-700", queued: "bg-slate-100 text-slate-600",
  draft: "bg-slate-100 text-slate-600", pending: "bg-amber-50 text-amber-700", pending_review: "bg-amber-50 text-amber-700",
  blocked: "bg-amber-50 text-amber-700", open: "bg-amber-50 text-amber-700", answered_general: "bg-sky-50 text-sky-700",
  error: "bg-red-50 text-red-700", rejected: "bg-red-50 text-red-700", abstained: "bg-orange-50 text-orange-700",
  no_citation: "bg-orange-50 text-orange-700", superseded: "bg-slate-100 text-slate-500", deleted: "bg-slate-100 text-slate-500",
};

export function StatusBadge({ status, className }: { status: string | null | undefined; className?: string }) {
  if (!status) return null;
  return (
    <span className={clsx("badge", STATUS_COLOR[status] || "bg-slate-100 text-slate-600", className)}>
      {(status === "processing" || status === "generating") && <Loader2 className="h-3 w-3 animate-spin" />}
      {STATUS_LABEL[status] || status}
    </span>
  );
}

export function Badge({ children, tone = "slate", className }: { children: ReactNode; tone?: "slate" | "brand" | "amber" | "red" | "blue" | "violet" | "emerald"; className?: string }) {
  const tones = {
    slate: "bg-slate-100 text-slate-600", brand: "bg-brand-50 text-brand-700", amber: "bg-amber-50 text-amber-700",
    red: "bg-red-50 text-red-700", blue: "bg-blue-50 text-blue-700", violet: "bg-violet-50 text-violet-700", emerald: "bg-emerald-50 text-emerald-700",
  };
  return <span className={clsx("badge", tones[tone], className)}>{children}</span>;
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <div className="mt-0.5 text-sm text-slate-500">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Modal({ open, onClose, title, children, width = "max-w-lg", footer }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={clsx("card w-full max-h-[90vh] flex flex-col", width)}>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h3 className="text-base font-semibold text-slate-900">{title}</h3>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label="Đóng"><X className="h-4 w-4" /></button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-200 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, title, children, width = "max-w-xl" }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/30" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={clsx("flex h-full w-full flex-col bg-white shadow-2xl", width)}>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h3 className="truncate text-base font-semibold text-slate-900">{title}</h3>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label="Đóng"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { key: T; label: ReactNode; count?: number }[]; value: T; onChange: (k: T) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-slate-200">
      {tabs.map((t) => (
        <button key={t.key} onClick={() => onChange(t.key)}
          className={clsx("-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition", value === t.key ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800")}>
          {t.label}
          {t.count !== undefined && <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 text-xs text-slate-600">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function KPI({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "amber" | "red" | "emerald" }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={clsx("kpi-value mt-1", tone === "amber" && "text-amber-600", tone === "red" && "text-red-600", tone === "emerald" && "text-emerald-600")}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Confirm({ open, onClose, onConfirm, title, message, danger, busy }: { open: boolean; onClose: () => void; onConfirm: () => void; title: string; message: ReactNode; danger?: boolean; busy?: boolean }) {
  return (
    <Modal open={open} onClose={onClose} title={title} width="max-w-md"
      footer={<><button className="btn-secondary" onClick={onClose}>Huỷ</button><button className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm} disabled={busy}>{busy && <Spinner className="text-current" />} Xác nhận</button></>}>
      <div className="text-sm text-slate-600">{message}</div>
    </Modal>
  );
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
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={clsx("pointer-events-auto flex items-start gap-2 rounded-lg border p-3 text-sm shadow-lg",
            t.kind === "ok" && "border-emerald-200 bg-white text-emerald-800", t.kind === "err" && "border-red-200 bg-white text-red-700", t.kind === "info" && "border-slate-200 bg-white text-slate-700")}>
            {t.kind === "ok" ? <CheckCircle2 className="mt-0.5 h-4 w-4" /> : t.kind === "err" ? <AlertCircle className="mt-0.5 h-4 w-4" /> : <Info className="mt-0.5 h-4 w-4" />}
            <div className="flex-1">{t.text}</div>
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
    ok: (t: string) => v.push("ok", t),
    err: (e: unknown) => v.push("err", e instanceof Error ? e.message : String(e)),
    info: (t: string) => v.push("info", t),
  };
}

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className={clsx("btn-ghost btn-sm", className)} onClick={async () => { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200); }}>
      {done ? "Đã chép" : "Chép"}
    </button>
  );
}

export function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="flex items-center justify-between gap-2 py-2 text-sm text-slate-600">
      <span>{(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} / {total}</span>
      <div className="flex gap-1">
        <button className="btn-secondary btn-sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>Trước</button>
        <button className="btn-secondary btn-sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>Sau</button>
      </div>
    </div>
  );
}
