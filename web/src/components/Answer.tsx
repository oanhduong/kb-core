// 1 câu trả lời của trợ lý: nội dung có chip trích dẫn (dạ quang), kết quả số liệu (biểu đồ/bảng), dải truy vết
// (hiểu → tra → tìm → kiểm → tin cậy), danh sách nguồn, phản hồi, ghim vào báo cáo.
import clsx from "clsx";
import { BarChart3, ChevronDown, Pin, ThumbsDown, ThumbsUp, UserCheck } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { useWorkspace } from "@/state/auth";
import { useI18n } from "@/i18n";
import { fmtDate } from "@/lib/format";
import type { Artifact, Citation, Confidence, DecisionTrace, QueryArtifact } from "@/lib/types";
import { Advanced } from "@/state/prefs";
import { DataGrid } from "./DataGrid";
import { Markdown } from "./Markdown";
import { confidenceKey, ProvenanceTrail } from "./Provenance";
import { Badge, CopyButton } from "./ui";
import { ChartView } from "./VegaChart";

export function isQueryArtifact(a: Artifact): a is QueryArtifact {
  return a.type === "query_result";
}

export function ArtifactView({ artifact, compact }: { artifact: Artifact; compact?: boolean }) {
  const { t } = useI18n();
  const [showRows, setShowRows] = useState(false);
  const [showSql, setShowSql] = useState(false);
  if (!isQueryArtifact(artifact)) return null;
  const a = artifact;
  const hasChart = a.chart && (a.chart.vega_lite || a.chart.type === "kpi");
  return (
    <div className="my-3 overflow-hidden rounded-2xl border border-ink-100 bg-white">
      <div className="flex items-center justify-between gap-2 px-4 pb-1 pt-3">
        <div className="flex min-w-0 items-center gap-2 text-sm">
          <BarChart3 className="h-4 w-4 shrink-0 text-brand-600" />
          <span className="truncate font-semibold text-ink-900">{a.chart?.title || a.purpose || t("chat.artifact.result")}</span>
          <span className="shrink-0 text-xs text-ink-600">{t("chat.artifact.computed", { n: a.row_count })}</span>
        </div>
        <div className="flex shrink-0 gap-1">
          {hasChart && a.rows.length > 0 && <button className="btn-ghost btn-sm" onClick={() => setShowRows((v) => !v)}>{showRows ? t("chat.artifact.hideTable") : t("chat.artifact.table")}</button>}
          <Advanced><button className="btn-ghost btn-sm" onClick={() => setShowSql((v) => !v)}>SQL</button></Advanced>
        </div>
      </div>
      <div className="px-4 pb-4 pt-2">
        {hasChart && <ChartView chart={a.chart} height={compact ? 200 : 280} />}
        {(!hasChart || showRows) && a.rows.length > 0 && <DataGrid columns={a.columns} rows={a.rows} maxHeight={compact ? 220 : 360} compact className={hasChart ? "mt-3" : ""} />}
        {a.rows.length === 0 && <div className="text-sm text-ink-600">{t("chat.artifact.noRows")}</div>}
        {showSql && (
          <div className="relative mt-3">
            <pre className="overflow-x-auto rounded-xl bg-ink-950 p-3 text-xs text-ink-100">{a.sql}</pre>
            <CopyButton text={a.sql} className="absolute right-1 top-1 text-ink-300 hover:bg-ink-800" />
          </div>
        )}
      </div>
    </div>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence | null | undefined }) {
  const { t } = useI18n();
  const k = confidenceKey(confidence?.level);
  if (!k) return null;
  return <Badge tone={k === "high" ? "emerald" : k === "medium" ? "amber" : "red"} title={confidence?.explanation}>{t(`confidence.${k}`)}</Badge>;
}

/** Giữ để tương thích (Trust center, review): danh sách bước dạng văn bản. */
export function TracePanel({ trace }: { trace: DecisionTrace | null | undefined }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  if (!trace || !trace.summary?.length) return null;
  return (
    <div className="mt-2 text-xs">
      <button className="inline-flex items-center gap-1 text-ink-600 hover:text-ink-900" onClick={() => setOpen((v) => !v)}>
        <ChevronDown className={clsx("h-3.5 w-3.5 transition", open && "rotate-180")} /> {t("trail.how")}
      </button>
      {open && (
        <ol className="mt-2 space-y-1 rounded-xl bg-paper p-3 text-ink-700">
          {trace.summary.map((s, i) => <li key={i} className="flex gap-2"><span className="text-ink-400">{i + 1}.</span><span className="whitespace-pre-wrap">{s}</span></li>)}
        </ol>
      )}
    </div>
  );
}

export function CitationList({ citations, onCite }: { citations: Citation[]; onCite: (c: Citation) => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  if (!citations.length) return null;
  const shown = open ? citations : citations.slice(0, 3);
  return (
    <div className="mt-3">
      <div className="mb-1.5 text-[12px] font-medium text-ink-600">{t("chat.sources", { n: citations.length })}</div>
      <ul className="grid gap-1.5 sm:grid-cols-2">
        {shown.map((c) => (
          <li key={c.n}>
            <button className="group flex w-full items-start gap-2 rounded-xl bg-white px-2.5 py-2 text-left ring-1 ring-ink-100 transition hover:ring-ink-300" onClick={() => onCite(c)}>
              <span className="cite-chip mt-0.5 shrink-0">{c.n}</span>
              <span className="min-w-0 flex-1 text-xs">
                <span className="block truncate font-medium text-ink-900">{c.label}</span>
                {c.effective_date && <span className="text-ink-600">{t("chat.effective", { date: fmtDate(c.effective_date) })}</span>}
                {c.snippet && <span className="mt-0.5 line-clamp-2 block text-ink-600">{c.snippet}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {citations.length > 3 && <button className="mt-1.5 text-xs font-medium text-brand-700 hover:underline" onClick={() => setOpen((v) => !v)}>{open ? t("action.showLess") : t("chat.moreSources", { n: citations.length - 3 })}</button>}
    </div>
  );
}

export interface AnswerData {
  text: string;
  status?: string | null;
  citations: Citation[];
  artifacts: Artifact[];
  confidence?: Confidence | null;
  trace?: DecisionTrace | null;
  trace_id?: string | null;
  trace_url?: string | null;
  streaming?: boolean;
}

export function AnswerView({ a, onCite, onFeedback, onPin, feedback }: { a: AnswerData; onCite: (c: Citation | null, n: number) => void; onFeedback?: (r: "up" | "down") => void; onPin?: () => void; feedback?: "up" | "down" | null }) {
  const { t } = useI18n();
  const held = a.status === "abstained" || a.status === "no_citation";
  return (
    <div>
      {a.artifacts.filter(isQueryArtifact).map((art, i) => <ArtifactView key={`${art.sid || "a"}-${i}`} artifact={art} />)}
      {a.status === "pending_review" && !a.streaming && (
        <div className="mb-2 flex items-start gap-2 rounded-xl bg-marker-100 px-3 py-2 text-sm text-ink-900"><UserCheck className="mt-0.5 h-4 w-4 text-marker-700" />{t("chat.pendingReview")}</div>
      )}
      <div className={clsx(held && "rounded-xl border border-marker-300 bg-marker-50 px-3.5 py-1", a.status === "error" && "rounded-xl border border-alert-500/30 bg-alert-50 px-3.5 py-1")}>
        {held && !a.streaming && <div className="pt-2 text-[12px] font-semibold text-marker-700">{t("chat.heldBack")}</div>}
        <Markdown text={a.text} citations={a.citations} onCite={onCite} streaming={a.streaming} />
      </div>
      {!a.streaming && (
        <>
          <CitationList citations={a.citations} onCite={(c) => onCite(c, c.n)} />
          {a.trace ? <ProvenanceTrail className="mt-3" trace={a.trace} status={a.status} confidence={a.confidence} citations={a.citations.length} />
            : a.trace_id && <LazyTrail traceId={a.trace_id} />}
          <div className="mt-2 flex flex-wrap items-center gap-1">
            {a.status === "answered_general" && <Badge tone="brand">{t("chat.general")}</Badge>}
            <span className="flex-1" />
            {onFeedback && a.trace_id && (
              <>
                <button className={clsx("btn-ghost btn-sm", feedback === "up" && "bg-verify-50 text-verify-700")} title={t("chat.helpful")} aria-pressed={feedback === "up"} onClick={() => onFeedback("up")}><ThumbsUp className="h-3.5 w-3.5" /></button>
                <button className={clsx("btn-ghost btn-sm", feedback === "down" && "bg-alert-50 text-alert-700")} title={t("chat.notRight")} aria-pressed={feedback === "down"} onClick={() => onFeedback("down")}><ThumbsDown className="h-3.5 w-3.5" /></button>
              </>
            )}
            {onPin && a.trace_id && (a.status === "answered" || a.status === "answered_general") && (
              <button className="btn-ghost btn-sm" onClick={onPin}><Pin className="h-3.5 w-3.5" />{t("chat.pin")}</button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Câu trả lời trong lịch sử chưa kèm trace: tải khi người dùng muốn xem. */
function LazyTrail({ traceId }: { traceId: string }) {
  const { t } = useI18n();
  const { wsId } = useWorkspace();
  const [on, setOn] = useState(false);
  const q = useQuery({ queryKey: ["answer", wsId, traceId], queryFn: () => api.answer(wsId, traceId), enabled: on, staleTime: 300_000 });
  if (!on) return <button className="mt-2 text-xs font-medium text-ink-600 hover:text-ink-900" onClick={() => setOn(true)}>{t("trail.how")}</button>;
  if (!q.data) return <div className="mt-2 text-xs text-ink-400">{t("loading")}</div>;
  return <ProvenanceTrail className="mt-3" trace={q.data.decision_trace} status={q.data.status} confidence={q.data.confidence} citations={q.data.citations.length} />;
}
