export function fmtDate(v: string | null | undefined, withTime = false): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return withTime
    ? d.toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export function fmtRelative(v: string | null | undefined): string {
  if (!v) return "—";
  const diff = (Date.now() - new Date(v).getTime()) / 1000;
  if (diff < 60) return "vừa xong";
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`;
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)} ngày trước`;
  return fmtDate(v);
}

export function fmtBytes(n: number | null | undefined): string {
  if (!n && n !== 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function fmtNumber(v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("vi-VN") : v.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function fmtCell(v: unknown, type?: string): string {
  if (v === null || v === undefined) return "";
  if (type && /int|double|decimal|float|bigint|hugeint/i.test(type) && typeof v === "number") return fmtNumber(v);
  if (type && /date|timestamp/i.test(type) && typeof v === "string") return fmtDate(v, /timestamp/i.test(type));
  return fmtNumber(v);
}

export function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `$${v.toFixed(v < 0.01 ? 4 : 2)}`;
}

export const STATUS_LABEL: Record<string, string> = {
  queued: "Chờ xử lý", processing: "Đang xử lý", done: "Hoàn tất", blocked: "Bị chặn", error: "Lỗi",
  active: "Hoạt động", pending_review: "Chờ duyệt", superseded: "Đã thay thế", deleted: "Đã xoá", processed: "Đã xử lý",
  uploaded: "Đã tải lên", ready: "Sẵn sàng", draft: "Bản nháp", generating: "Đang sinh", open: "Mở", resolved: "Đã xử lý",
  pending: "Chờ duyệt", approved: "Đã duyệt", rejected: "Từ chối", answered: "Đã trả lời", answered_general: "Trả lời chung",
  abstained: "Không đủ căn cứ", no_citation: "Thiếu trích dẫn",
};

export const SOURCE_ICON: Record<string, string> = {
  xlsx: "📊", csv: "📊", docx: "📄", pdf: "📕", image: "🖼️", audio: "🎙️", video: "🎬", txt: "📝", md: "📝", pptx: "📑",
};

export function truncate(s: string | null | undefined, n = 120): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export const ROLE_LABEL: Record<string, string> = { viewer: "Người xem", editor: "Biên tập", owner: "Chủ sở hữu" };
