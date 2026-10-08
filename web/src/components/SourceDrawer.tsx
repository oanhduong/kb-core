// Drawer hiện nguồn trích dẫn: đoạn được trích (tô dạ quang — bằng chứng) nằm trong ngữ cảnh rộng hơn, link sang tài liệu.
// Kèm vài helper nhãn dùng chung cho khu vực "Nguồn dữ liệu" (loại tài liệu, loại đối tượng, thời lượng).
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Calculator, CalendarDays, MapPin } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { translate, useT } from "@/i18n";
import { api } from "@/lib/api";
import { docStatus, fmtDate } from "@/lib/format";
import type { Citation } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { Advanced } from "@/state/prefs";
import { Drawer, ErrorBox, Loading, SourceIcon, StatusBadge } from "./ui";

function humanize(code: string): string {
  const s = code.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function labelFrom(prefix: string, code: string | null | undefined): string {
  if (!code) return "";
  const k = `${prefix}.${code}`;
  const out = translate(k);
  return out === k ? humanize(code) : out;
}

/** Nhãn thân thiện cho mã loại tài liệu (policy, price_list…). */
export function docTypeLabel(code: string | null | undefined): string {
  return labelFrom("sources.docType", code);
}

/** Nhãn thân thiện cho loại đối tượng (person, organization…). */
export function entityTypeLabel(code: string | null | undefined): string {
  return labelFrom("sources.entityType", code);
}

/** Loại tài liệu: nhãn thân thiện; mã máy chỉ hiện khi bật chi tiết kỹ thuật. */
export function DocType({ code }: { code: string | null | undefined }) {
  if (!code) return null;
  const label = docTypeLabel(code);
  return (
    <>
      {label}
      {label !== code && <Advanced><code className="ml-1.5 font-mono text-[11px] text-ink-400">{code}</code></Advanced>}
    </>
  );
}

/** Thời lượng (giây) -> "1 h 4 min" / "3 min 20 s". */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return "";
  const total = Math.round(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return translate("sources.duration.hours", { h, m });
  if (m > 0) return translate("sources.duration.minutes", { m, s });
  return translate("sources.duration.seconds", { s });
}

/** Đoạn ngữ cảnh rộng, phần được trích được tô dạ quang ngay tại chỗ (nếu tìm thấy). */
function InContext({ text, parent }: { text: string; parent: string }) {
  const needle = text.trim();
  const i = needle ? parent.indexOf(needle) : -1;
  if (i < 0) return <>{parent}</>;
  return (
    <>
      {parent.slice(0, i)}
      <span className="marker box-decoration-clone text-ink-900">{needle}</span>
      {parent.slice(i + needle.length)}
    </>
  );
}

function Meta({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return <span className="inline-flex items-center gap-1.5">{icon}{children}</span>;
}

export function SourceDrawer({ citation, onClose }: { citation: Citation | null; onClose: () => void }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const q = useQuery({
    queryKey: ["chunk", wsId, citation?.chunk_id],
    queryFn: () => api.chunk(wsId, citation!.chunk_id!),
    enabled: !!citation?.chunk_id,
  });
  const title = citation ? (
    <span className="flex min-w-0 items-center gap-2">
      <span className="cite-chip shrink-0">{citation.n}</span>
      <span className="truncate">{citation.label}</span>
    </span>
  ) : "";
  const c = q.data;
  const hasContext = !!c?.parent_text && c.parent_text.trim() !== c.text.trim();

  return (
    <Drawer open={!!citation} onClose={onClose} title={title}>
      {citation && !citation.chunk_id && (
        <div className="space-y-4">
          <div className="flex items-start gap-3 text-sm leading-6 text-ink-700">
            <Calculator className="mt-0.5 h-[18px] w-[18px] shrink-0 text-ink-400" />
            <p>{t("sources.drawer.computed")}</p>
          </div>
          {citation.snippet && (
            <section>
              <h4 className="mb-2 text-sm font-semibold text-ink-900">{t("sources.drawer.computedValue")}</h4>
              <pre className="well whitespace-pre-wrap p-4 font-sans text-sm leading-6 text-ink-800">{citation.snippet}</pre>
            </section>
          )}
          {citation.document_id && (
            <Link to={`/w/${wsId}/documents/${citation.document_id}`} className="btn-secondary btn-sm" onClick={onClose}>
              {t("sources.drawer.openFile")} <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          )}
        </div>
      )}
      {citation?.chunk_id && q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {c && (
        <div className="space-y-6">
          {c.document && (
            <div className="well flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
              <SourceIcon type={c.document.source_type} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-ink-900">{c.document.title}</div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-600">
                  <span><DocType code={c.document.doc_type} /></span>
                  {c.location && <Meta icon={<MapPin className="h-3.5 w-3.5 text-ink-400" />}>{c.location}</Meta>}
                  {c.effective_date && <Meta icon={<CalendarDays className="h-3.5 w-3.5 text-ink-400" />}>{t("sources.inEffectFrom", { date: fmtDate(c.effective_date) })}</Meta>}
                  {c.document.status !== "active" && <StatusBadge status={docStatus(c.document.status)} />}
                </div>
              </div>
              <Link to={`/w/${wsId}/documents/${c.document.document_id}`} className="btn-secondary btn-sm shrink-0 self-start sm:self-center" onClick={onClose}>
                {t("sources.drawer.openFile")} <ArrowUpRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          )}

          <section>
            <h4 className="mb-1 font-display text-base font-semibold text-ink-900">{t("sources.drawer.passage")}</h4>
            <p className="mb-3 text-xs text-ink-600">{t("sources.drawer.passageHint")}</p>
            <blockquote className="whitespace-pre-wrap rounded-xl border border-ink-100 bg-white p-4 text-[15px] leading-7 text-ink-900">
              <span className="marker box-decoration-clone">{c.text}</span>
            </blockquote>
          </section>

          {hasContext && (
            <section>
              <h4 className="mb-1 font-display text-base font-semibold text-ink-900">{t("sources.drawer.context")}</h4>
              <p className="mb-3 text-xs text-ink-600">{t("sources.drawer.contextHint")}</p>
              <div className="well max-h-80 overflow-y-auto whitespace-pre-wrap p-4 text-sm leading-6 text-ink-600" tabIndex={0}>
                <InContext text={c.text} parent={c.parent_text!} />
              </div>
            </section>
          )}

          <Advanced>
            <dl className="space-y-1 border-t border-ink-100 pt-4 text-xs text-ink-600">
              <div className="flex gap-2"><dt className="w-24 shrink-0">{t("sources.tech.chunkId")}</dt><dd className="break-all font-mono">{c.chunk_id}</dd></div>
              {c.record_id && <div className="flex gap-2"><dt className="w-24 shrink-0">{t("sources.tech.recordId")}</dt><dd className="break-all font-mono">{c.record_id}</dd></div>}
              {c.role && <div className="flex gap-2"><dt className="w-24 shrink-0">{t("sources.tech.role")}</dt><dd className="font-mono">{c.role}</dd></div>}
              {c.citation && <div className="flex gap-2"><dt className="w-24 shrink-0">{t("sources.tech.citation")}</dt><dd>{c.citation}</dd></div>}
            </dl>
          </Advanced>
        </div>
      )}
    </Drawer>
  );
}
