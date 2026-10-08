// Định dạng theo ngôn ngữ đang chọn (i18n). Nhãn trạng thái/vai trò lấy từ từ điển.
import { getLang, translate } from "@/i18n";

const loc = () => (getLang() === "vi" ? "vi-VN" : "en-GB");

export function fmtDate(v: string | null | undefined, withTime = false): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return withTime
    ? d.toLocaleString(loc(), { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(loc(), { day: "2-digit", month: "short", year: "numeric" });
}

export function fmtRelative(v: string | null | undefined): string {
  if (!v) return "—";
  const diff = (Date.now() - new Date(v).getTime()) / 1000;
  if (diff < 60) return translate("time.justNow");
  if (diff < 3600) return translate("time.minutes", { n: Math.floor(diff / 60) });
  if (diff < 86400) return translate("time.hours", { n: Math.floor(diff / 3600) });
  if (diff < 7 * 86400) return translate("time.days", { n: Math.floor(diff / 86400) });
  return fmtDate(v);
}

export function fmtBytes(n: number | null | undefined): string {
  if (!n && n !== 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function fmtNumber(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString(loc()) : v.toLocaleString(loc(), { maximumFractionDigits: digits });
  if (typeof v === "boolean") return v ? "✓" : "✗";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toLocaleString(loc(), { maximumFractionDigits: digits })}%`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toLocaleString(loc(), { maximumFractionDigits: 1 })} s`;
}

export function fmtCell(v: unknown, type?: string): string {
  if (v === null || v === undefined) return "";
  if (type && /int|double|decimal|float|bigint|hugeint/i.test(type) && typeof v === "number") return fmtNumber(v);
  if (type && /date|timestamp/i.test(type) && typeof v === "string") return fmtDate(v, /timestamp/i.test(type) && v.length > 10);
  return fmtNumber(v);
}

export function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `$${v.toFixed(v < 0.01 ? 4 : 2)}`;
}

export function statusLabel(s: string | null | undefined): string {
  if (!s) return "";
  const k = `status.${s}`;
  const out = translate(k);
  return out === k ? s : out;
}

export function roleLabel(r: string | null | undefined): string {
  return r ? translate(`role.${r}`) : "";
}

export function sourceLabel(s: string | null | undefined): string {
  if (!s) return "";
  const k = `source.${s}`;
  const out = translate(k);
  return out === k ? s.toUpperCase() : out;
}

/** Tương thích code cũ: STATUS_LABEL[s] / ROLE_LABEL[r] vẫn ra nhãn theo ngôn ngữ hiện tại. */
export const STATUS_LABEL: Record<string, string> = new Proxy({}, { get: (_t, k: string) => statusLabel(k) });
export const ROLE_LABEL: Record<string, string> = new Proxy({}, { get: (_t, k: string) => roleLabel(k) });

export const SOURCE_ICON: Record<string, string> = {
  xlsx: "📊", csv: "📊", docx: "📄", pdf: "📕", image: "🖼️", audio: "🎙️", video: "🎬", txt: "📝", md: "📝", pptx: "📑",
};

export function truncate(s: string | null | undefined, n = 120): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
