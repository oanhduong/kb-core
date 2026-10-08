// Dải truy vết của 1 câu trả lời — điểm khác biệt so với chatbot RAG thường: cho người dùng thấy hệ thống đã HIỂU câu hỏi,
// TRA ở đâu (tài liệu bằng tìm kiếm từ khoá + ngữ nghĩa, bảng số liệu bằng truy vấn tính toán, bản đồ tri thức), TÌM được gì,
// KIỂM từng câu khẳng định với nguồn ra sao, và mức tin cậy. Chi tiết thô (câu truy vấn, điểm, công cụ) chỉ ở chế độ kỹ thuật.
import clsx from "clsx";
import { BookOpenText, Brain, ChevronDown, Database, FileSearch, Network, ShieldCheck, ShieldQuestion, UserCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import type { Confidence, DecisionTrace } from "@/lib/types";
import { usePrefs } from "@/state/prefs";

export const SKILL_OF: Record<string, "documents" | "tables" | "graph"> = {
  search_documents: "documents", read_source: "documents", get_document_overview: "documents", list_documents: "documents",
  query_tables: "tables", list_tables: "tables", explore_graph: "graph",
};
const SKILL_ICON = { documents: FileSearch, tables: Database, graph: Network };

export function confidenceKey(level: string | null | undefined): "high" | "medium" | "low" | null {
  if (!level) return null;
  const l = level.toLowerCase();
  if (l === "cao" || l === "high") return "high";
  if (l.startsWith("trung") || l === "medium") return "medium";
  if (l === "thấp" || l === "low") return "low";
  return null;
}

interface Found { kind?: string; used_in_answer?: boolean; origin?: string }

/** Nhãn loại câu hỏi; intent mới chưa có bản dịch -> "Câu hỏi chung" thay vì lộ key thô. */
export function qtypeLabel(t: (k: string) => string, qtype: unknown): string {
  const key = `trail.qtype.${String(qtype || "general")}`;
  const label = t(key);
  return label === key ? t("trail.qtype.general") : label;
}

export function summarizeTrace(trace: DecisionTrace | null | undefined) {
  const searched = (trace?.searched || []) as Record<string, unknown>[];
  const skills: Record<string, { lookups: number; hits: number; keyword: boolean; meaning: boolean; rows: number }> = {};
  for (const s of searched) {
    const sk = SKILL_OF[String(s.tool || "")];
    if (!sk) continue;
    const cur = (skills[sk] ||= { lookups: 0, hits: 0, keyword: false, meaning: false, rows: 0 });
    cur.lookups += 1;
    cur.hits += Number(s.hits || s.listed || 0);
    if (s.bm25) cur.keyword = true;
    if (s.dense) cur.meaning = true;
    cur.rows += Number(s.rows || 0);
  }
  const found = (trace?.found || []) as Found[];
  return { skills, found: found.length, used: found.filter((f) => f.used_in_answer).length, conflicts: (trace?.conflicts || []).length, degraded: trace?.degraded || [] };
}

export function ProvenanceTrail({ trace, status, confidence, citations, animate, className }: {
  trace: DecisionTrace | null | undefined; status?: string | null; confidence?: Confidence | null; citations: number; animate?: boolean; className?: string;
}) {
  const { t } = useI18n();
  const { advanced } = usePrefs();
  const [open, setOpen] = useState(false);
  if (!trace) return null;
  const s = summarizeTrace(trace);
  const ck = confidenceKey(confidence?.level || trace.confidence?.level);
  const skillNames = (Object.keys(s.skills) as ("documents" | "tables" | "graph")[]);
  const checked = status === "answered" || status === "pending_review";
  const stages: { icon: typeof Brain; title: string; value: ReactNode; tone?: "verify" | "marker" | "alert" }[] = [
    { icon: Brain, title: t("trail.understood"), value: qtypeLabel(t, trace.understood?.question_type) },
    {
      icon: BookOpenText, title: t("trail.lookedIn"),
      value: skillNames.length ? (
        <span className="flex flex-wrap gap-1">{skillNames.map((k) => { const I = SKILL_ICON[k]; return <span key={k} className="inline-flex items-center gap-1"><I className="h-3.5 w-3.5 text-ink-400" />{t(`trail.skill.${k}`)}</span>; })}</span>
      ) : t("trail.nothing"),
    },
    { icon: FileSearch, title: t("trail.found"), value: s.found ? t("trail.foundValue", { n: s.found, used: s.used || citations }) : t("trail.nothingFound") },
    status === "pending_review"
      ? { icon: UserCheck, title: t("trail.checked"), value: t("trail.review"), tone: "marker" }
      : { icon: checked ? ShieldCheck : ShieldQuestion, title: t("trail.checked"), value: checked ? t("trail.cited", { n: citations }) : status === "abstained" || status === "no_citation" ? t("trail.heldBack") : t("trail.general"), tone: checked ? "verify" : status === "abstained" || status === "no_citation" ? "marker" : undefined },
  ];
  if (ck) stages.push({ icon: ShieldCheck, title: t("trail.confidence"), value: t(`confidence.${ck}`), tone: ck === "high" ? "verify" : ck === "medium" ? "marker" : "alert" });

  return (
    <div className={clsx("rounded-2xl border border-ink-100 bg-white", className)}>
      <ol className="relative grid gap-y-3 p-4" style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }}>
        <span aria-hidden className="absolute left-8 right-8 top-[30px] h-px bg-ink-100" />
        {animate && <span aria-hidden className="absolute left-8 top-[30px] h-px origin-left animate-[grow_1.4s_cubic-bezier(.65,0,.35,1)_forwards] bg-ink-900" style={{ right: "2rem" }} />}
        {stages.map((st, i) => (
          <li key={i} className="relative flex min-w-0 flex-col items-start gap-1.5 pr-2" style={animate ? { animation: `rise .4s ease-out ${0.15 + i * 0.22}s both` } : undefined}>
            <span className={clsx("relative z-10 flex h-7 w-7 items-center justify-center rounded-full ring-4 ring-white",
              st.tone === "verify" ? "bg-verify-600 text-white" : st.tone === "marker" ? "bg-marker-200 text-ink-900" : st.tone === "alert" ? "bg-alert-500 text-white" : "bg-ink-900 text-white")}>
              <st.icon className="h-3.5 w-3.5" />
            </span>
            <span className="text-[11px] font-medium text-ink-600">{st.title}</span>
            <span className="text-[13px] font-semibold leading-5 text-ink-900">{st.value}</span>
          </li>
        ))}
      </ol>
      <button className="flex w-full items-center gap-1.5 border-t border-ink-100 px-4 py-2 text-xs font-medium text-ink-600 hover:text-ink-900" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <ChevronDown className={clsx("h-3.5 w-3.5 transition", open && "rotate-180")} />{open ? t("trail.hide") : t("trail.how")}
      </button>
      {open && (
        <div className="space-y-2 border-t border-ink-100 px-4 py-3 text-sm text-ink-700">
          {skillNames.map((k) => {
            const v = s.skills[k];
            return (
              <p key={k} className="leading-6">
                <b className="font-semibold text-ink-900">{t(`trail.skill.${k}`)}:</b>{" "}
                {k === "documents" && t("trail.docsDetail", { n: v.lookups, hits: v.hits, how: [v.keyword && t("trail.keyword"), v.meaning && t("trail.meaning")].filter(Boolean).join(" + ") || t("trail.browse") })}
                {k === "tables" && t("trail.tablesDetail", { n: v.lookups, rows: v.rows })}
                {k === "graph" && t("trail.graphDetail", { n: v.lookups })}
              </p>
            );
          })}
          <p className="leading-6">{checked ? t("trail.checkDetail") : t("trail.heldDetail")}</p>
          {s.conflicts > 0 && <p className="leading-6 text-marker-700">{t("trail.conflicts", { n: s.conflicts })}</p>}
          {advanced && (
            <ol className="mt-2 space-y-1 rounded-xl bg-paper p-3 font-mono text-[11.5px] leading-5 text-ink-700">
              {(trace.summary || []).map((x, i) => <li key={i}>{x}</li>)}
              {s.degraded.length > 0 && <li className="text-marker-700">degraded: {s.degraded.join("; ")}</li>}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
