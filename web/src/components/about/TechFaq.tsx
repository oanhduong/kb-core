// Tab "Hỏi đáp kỹ thuật" của trang Giới thiệu: sơ đồ đường đi của file/câu hỏi (bấm để nhảy tới nhóm câu hỏi), tìm kiếm,
// câu hỏi–đáp mở/đóng theo nhóm. Nội dung ở i18n/tech.ts — đoạn cách nhau bằng dòng trống, "- " là gạch đầu dòng,
// "1. " là danh sách đánh số, `code`, **đậm**, *nghiêng*.
import clsx from "clsx";
import { ChevronRight, Search, X } from "lucide-react";
import { Fragment, useMemo, useState, type ReactNode } from "react";
import { useI18n } from "@/i18n";

const SECTIONS: { id: string; qs: string[] }[] = [
  { id: "ingest", qs: ["types", "parse", "injection", "versions", "records"] },
  { id: "index", qs: ["chunking", "embedding", "bm25", "nollm"] },
  { id: "retrieve", qs: ["retrieval", "rerankfail", "abstain", "dates"] },
  { id: "agent", qs: ["flow", "tools", "agent", "router", "graphrag"] },
  { id: "tables", qs: ["tables", "nl2sql", "sqlsafe", "charts"] },
  { id: "links", qs: ["links", "linkfalse", "linksql", "linkmap", "models"] },
  { id: "knowledge", qs: ["extract", "profile"] },
  { id: "answer", qs: ["citations", "review", "conflicts", "confidence"] },
  { id: "ops", qs: ["llms", "language", "jobs", "security", "observe"] },
];

// [bước trên sơ đồ, nhóm câu hỏi giải thích bước đó]
const FLOW: [string, string][] = [
  ["upload", "ingest"], ["parse", "ingest"], ["records", "ingest"], ["chunks", "index"], ["index", "index"], ["tables", "tables"],
  ["links", "links"], ["knowledge", "knowledge"], ["question", "agent"], ["retrieve", "retrieve"], ["answer", "answer"],
];

function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g).map((p, i) => {
    if (p.startsWith("`") && p.endsWith("`")) return <code key={i} className="rounded bg-ink-100/70 px-1 py-0.5 font-mono text-[12.5px] text-ink-900">{p.slice(1, -1)}</code>;
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={i} className="font-semibold text-ink-900">{p.slice(2, -2)}</strong>;
    if (p.length > 2 && p.startsWith("*") && p.endsWith("*")) return <em key={i}>{p.slice(1, -1)}</em>;
    return <Fragment key={i}>{p}</Fragment>;
  });
}

function Answer({ text }: { text: string }) {
  return (
    <div className="space-y-3 text-[15px] leading-7 text-ink-700">
      {text.split(/\n\n+/).map((block, bi) => {
        const lines = block.split("\n");
        if (lines.every((l) => l.startsWith("- "))) {
          return <ul key={bi} className="list-disc space-y-1.5 pl-5">{lines.map((l, i) => <li key={i}>{inline(l.slice(2))}</li>)}</ul>;
        }
        if (lines.every((l) => /^\d+\. /.test(l))) {
          return <ol key={bi} className="list-decimal space-y-1.5 pl-5">{lines.map((l, i) => <li key={i}>{inline(l.replace(/^\d+\. /, ""))}</li>)}</ol>;
        }
        // đoạn mở đầu + danh sách ngay sau (không có dòng trống)
        const first = lines.findIndex((l) => l.startsWith("- ") || /^\d+\. /.test(l));
        if (first > 0) {
          const items = lines.slice(first);
          const ordered = /^\d+\. /.test(items[0]);
          const List = ordered ? "ol" : "ul";
          return (
            <div key={bi} className="space-y-1.5">
              <p>{inline(lines.slice(0, first).join(" "))}</p>
              <List className={clsx("space-y-1.5 pl-5", ordered ? "list-decimal" : "list-disc")}>
                {items.map((l, i) => <li key={i}>{inline(l.replace(/^(- |\d+\. )/, ""))}</li>)}
              </List>
            </div>
          );
        }
        return <p key={bi}>{inline(block)}</p>;
      })}
    </div>
  );
}

export function TechFaq() {
  const { t, lang } = useI18n();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => new Set(["chunking"]));
  const needle = q.trim().toLowerCase();
  const visible = useMemo(() => SECTIONS.map((s) => ({
    ...s, qs: s.qs.filter((id) => !needle || `${t(`tech.q.${id}`)} ${t(`tech.a.${id}`)}`.toLowerCase().includes(needle)),
  })).filter((s) => s.qs.length > 0), [needle, t, lang]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (id: string) => setOpen((cur) => { const n = new Set(cur); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const jump = (sec: string) => {
    setQ("");
    setOpen((cur) => new Set([...cur, SECTIONS.find((s) => s.id === sec)!.qs[0]]));
    requestAnimationFrame(() => document.getElementById(`tech-${sec}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h2 className="font-display text-[28px] font-bold leading-tight text-ink-900">{t("tech.title")}</h2>
        <p className="mt-2 max-w-3xl text-[16px] leading-7 text-ink-600">{t("tech.lead")}</p>
      </div>

      <section className="panel p-5" aria-labelledby="tech-flow">
        <h3 id="tech-flow" className="mb-3 text-sm font-semibold text-ink-900">{t("tech.flow.title")}</h3>
        <ol className="flex flex-wrap items-center gap-y-2">
          {FLOW.map(([step, sec], i) => (
            <li key={step} className="flex items-center">
              <button type="button" onClick={() => jump(sec)}
                className={clsx("rounded-full px-3 py-1.5 text-sm ring-1 transition hover:ring-brand-300 hover:text-brand-700",
                  step === "question" ? "bg-ink-900 text-white ring-ink-900 hover:text-white" : "bg-white text-ink-800 ring-ink-100")}>
                {t(`tech.flow.${step}`)}
              </button>
              {i < FLOW.length - 1 && <ChevronRight className="mx-1 h-4 w-4 shrink-0 text-ink-300" aria-hidden />}
            </li>
          ))}
        </ol>
      </section>

      <div className="grid gap-8 lg:grid-cols-[230px_minmax(0,1fr)]">
        <nav className="hidden lg:block" aria-label={t("about.tab.tech")}>
          <ul className="sticky top-6 space-y-0.5 text-sm">
            {SECTIONS.map((s) => (
              <li key={s.id}><button type="button" onClick={() => jump(s.id)} className="w-full rounded-lg px-3 py-1.5 text-left text-ink-700 hover:bg-white hover:text-ink-900">{t(`tech.sec.${s.id}`)}</button></li>
            ))}
          </ul>
        </nav>
        <div className="min-w-0 space-y-8">
          <label className="relative block max-w-md">
            <span className="sr-only">{t("tech.search")}</span>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
            <input className="input pl-9" placeholder={t("tech.search")} value={q} onChange={(e) => setQ(e.target.value)} />
            {q && <button type="button" className="absolute right-2 top-1.5 rounded-md p-1 text-ink-400 hover:text-ink-900" onClick={() => setQ("")} aria-label={t("tech.clear")}><X className="h-3.5 w-3.5" /></button>}
          </label>
          {visible.length === 0 && <p className="text-sm text-ink-600">{t("tech.none")}</p>}
          {visible.map((s) => (
            <section key={s.id} id={`tech-${s.id}`} className="scroll-mt-6">
              <h3 className="mb-3 font-display text-xl font-semibold text-ink-900">{t(`tech.sec.${s.id}`)}</h3>
              <div className="panel divide-y divide-ink-100">
                {s.qs.map((id) => {
                  const isOpen = !!needle || open.has(id);
                  return (
                    <div key={id}>
                      <button type="button" className="flex w-full items-start gap-3 px-5 py-4 text-left" aria-expanded={isOpen} onClick={() => toggle(id)}>
                        <ChevronRight className={clsx("mt-1 h-4 w-4 shrink-0 text-ink-400 transition", isOpen && "rotate-90")} />
                        <span className="text-[15.5px] font-medium leading-6 text-ink-900">{t(`tech.q.${id}`)}</span>
                      </button>
                      {isOpen && <div className="px-5 pb-5 pl-12"><Answer text={t(`tech.a.${id}`)} /></div>}
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
