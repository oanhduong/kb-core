// Hiển thị 1 câu trả lời của trợ lý: Markdown + chip trích dẫn, artifact (chart/bảng), danh sách nguồn,
// độ tin cậy + decision trace, feedback, ghim vào báo cáo.
import clsx from "clsx";
import { ChevronDown, ChevronRight, Database, Pin, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { fmtDate } from "@/lib/format";
import type { Artifact, Citation, Confidence, DecisionTrace, QueryArtifact } from "@/lib/types";
import { DataGrid } from "./DataGrid";
import { Markdown } from "./Markdown";
import { Badge, CopyButton } from "./ui";
import { ChartView } from "./VegaChart";

export function isQueryArtifact(a: Artifact): a is QueryArtifact {
  return a.type === "query_result";
}

export function ArtifactView({ artifact, compact }: { artifact: Artifact; compact?: boolean }) {
  const [showSql, setShowSql] = useState(false);
  const [showRows, setShowRows] = useState(false);
  if (!isQueryArtifact(artifact)) return null;
  const a = artifact;
  const hasChart = a.chart && (a.chart.vega_lite || a.chart.type === "kpi");
  return (
    <div className="card my-2 overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm">
          <Database className="h-4 w-4 shrink-0 text-brand-600" />
          <span className="truncate font-medium text-slate-800">{a.chart?.title || a.purpose || "Kết quả truy vấn"}</span>
          <Badge>{a.row_count} dòng</Badge>
          {a.sid && <Badge tone="brand">{a.sid}</Badge>}
        </div>
        <div className="flex shrink-0 gap-1">
          <button className="btn-ghost btn-sm" onClick={() => setShowSql((v) => !v)}>SQL</button>
          {hasChart && a.rows.length > 0 && <button className="btn-ghost btn-sm" onClick={() => setShowRows((v) => !v)}>{showRows ? "Ẩn bảng" : "Bảng"}</button>}
        </div>
      </div>
      <div className="p-3">
        {hasChart && <ChartView chart={a.chart} height={compact ? 200 : 280} />}
        {(!hasChart || showRows) && a.rows.length > 0 && <DataGrid columns={a.columns} rows={a.rows} maxHeight={compact ? 220 : 360} compact className={hasChart ? "mt-3" : ""} />}
        {a.rows.length === 0 && <div className="text-sm text-slate-500">Truy vấn không trả về dòng nào.</div>}
        {showSql && (
          <div className="relative mt-3">
            <pre className="overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">{a.sql}</pre>
            <CopyButton text={a.sql} className="absolute right-1 top-1 text-slate-300 hover:bg-slate-800" />
          </div>
        )}
      </div>
    </div>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence | null | undefined }) {
  if (!confidence || !confidence.level) return null;
  const tone = confidence.level === "cao" ? "emerald" : confidence.level === "trung bình" ? "amber" : confidence.level === "thấp" ? "red" : "slate";
  return (
    <Badge tone={tone} className="cursor-help" >
      <span title={confidence.explanation}>Độ tin cậy: {confidence.level}{confidence.score !== null && confidence.score !== undefined ? ` (${Math.round(confidence.score * 100)}%)` : ""}</span>
    </Badge>
  );
}

export function TracePanel({ trace }: { trace: DecisionTrace | null | undefined }) {
  const [open, setOpen] = useState(false);
  if (!trace || !trace.summary?.length) return null;
  return (
    <div className="mt-2 text-xs">
      <button className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-800" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />} Hệ thống đã làm gì để trả lời
      </button>
      {open && (
        <ol className="mt-2 space-y-1 rounded-lg border border-slate-200 bg-slate-50 p-3 text-slate-600">
          {trace.summary.map((s, i) => <li key={i} className="flex gap-2"><span className="text-slate-400">{i + 1}.</span><span className="whitespace-pre-wrap">{s}</span></li>)}
          {trace.degraded?.length > 0 && <li className="mt-1 text-amber-700">Suy giảm: {trace.degraded.join("; ")}</li>}
        </ol>
      )}
    </div>
  );
}

export function CitationList({ citations, onCite }: { citations: Citation[]; onCite: (c: Citation) => void }) {
  const [open, setOpen] = useState(false);
  if (!citations.length) return null;
  return (
    <div className="mt-2 text-xs">
      <button className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-800" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />} {citations.length} nguồn tham chiếu
      </button>
      {open && (
        <ul className="mt-2 space-y-1">
          {citations.map((c) => (
            <li key={c.n}>
              <button className="group flex w-full items-start gap-2 rounded-lg px-2 py-1 text-left hover:bg-slate-100" onClick={() => onCite(c)}>
                <span className="cite-chip mt-0.5">{c.n}</span>
                <span className="flex-1">
                  <span className="text-slate-800 group-hover:underline">{c.label}</span>
                  {c.effective_date && <span className="ml-1 text-slate-400">· hiệu lực {fmtDate(c.effective_date)}</span>}
                  {c.snippet && <span className="mt-0.5 block truncate text-slate-500">{c.snippet}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
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
  const degraded = a.status === "abstained" || a.status === "no_citation" || a.status === "error";
  return (
    <div>
      {a.artifacts.filter(isQueryArtifact).map((art, i) => <ArtifactView key={art.sid || i} artifact={art} />)}
      <div className={clsx(degraded && "rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-1")}>
        <Markdown text={a.text} citations={a.citations} onCite={onCite} streaming={a.streaming} />
      </div>
      {!a.streaming && (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <ConfidenceBadge confidence={a.confidence} />
            {a.status === "answered_general" && <Badge tone="blue">Trả lời chung — không dựa trên tài liệu</Badge>}
            {a.status === "pending_review" && <Badge tone="amber">Đang chờ người phụ trách duyệt</Badge>}
            <span className="flex-1" />
            {onFeedback && a.trace_id && (
              <>
                <button className={clsx("btn-ghost btn-sm", feedback === "up" && "bg-emerald-50 text-emerald-700")} title="Hữu ích" onClick={() => onFeedback("up")}><ThumbsUp className="h-3.5 w-3.5" /></button>
                <button className={clsx("btn-ghost btn-sm", feedback === "down" && "bg-red-50 text-red-700")} title="Chưa đúng" onClick={() => onFeedback("down")}><ThumbsDown className="h-3.5 w-3.5" /></button>
              </>
            )}
            {onPin && a.trace_id && (a.status === "answered" || a.status === "answered_general") && (
              <button className="btn-ghost btn-sm" title="Ghim vào báo cáo" onClick={onPin}><Pin className="h-3.5 w-3.5" /> Ghim vào báo cáo</button>
            )}
          </div>
          <CitationList citations={a.citations} onCite={(c) => onCite(c, c.n)} />
          <TracePanel trace={a.trace} />
        </>
      )}
    </div>
  );
}
