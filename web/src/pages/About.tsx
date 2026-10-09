// Giới thiệu, 2 tab: Tổng quan (video minh họa 3 luồng + so sánh với RAG / Graph RAG + lời thuyết minh) và Hỏi đáp kỹ thuật
// (từng bước nhỏ: đọc file, chunking, embedding, tìm nguồn, SQL, nối file...). ?tab=tech mở thẳng tab kỹ thuật.
import { useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { TechFaq } from "@/components/about/TechFaq";
import { CHAPTERS, rich, Tour, type TourHandle } from "@/components/about/Tour";
import { Tabs } from "@/components/ui";
import "@/components/about/tour.css";
import { useI18n } from "@/i18n";
import { Page } from "@/layout/AppShell";

type Mark = "y" | "p" | "n";
// [khóa dòng, RAG, Graph RAG, KB Explorer] — chú thích từng ô: about.cmp.rN.rag / .graph / .us, ví dụ: .ex
const MATRIX: [number, Mark, Mark, Mark][] = [
  [1, "y", "y", "y"], [2, "n", "y", "y"], [3, "n", "n", "y"], [4, "n", "p", "y"], [5, "n", "n", "y"], [6, "n", "n", "y"], [7, "n", "n", "y"],
  [8, "n", "n", "p"], [9, "n", "n", "y"], [10, "p", "p", "y"], [11, "n", "n", "y"], [12, "n", "n", "y"], [13, "n", "p", "y"],
];

export function AboutPage() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "tech" ? "tech" : "overview";
  const tour = useRef<TourHandle>(null);
  const top = useRef<HTMLDivElement>(null);
  const markLabel = (m: Mark) => t(m === "y" ? "about.cmp.yes" : m === "p" ? "about.cmp.part" : "about.cmp.no");
  const note = (key: string) => { const v = t(key); return v === key ? null : <div className="ex">{v}</div>; };
  const cell = (m: Mark, key: string) => (
    <><span className={`mark ${m}`}><i />{markLabel(m)}</span>{note(key)}</>
  );

  return (
    <Page className="flex flex-col gap-12">
      <header ref={top} className="scroll-mt-6">
        <h1 className="font-display text-[clamp(32px,4.4vw,52px)] font-extrabold leading-[1.04] tracking-tight text-ink-900">{t("about.title")}</h1>
        <p className="mt-3 max-w-3xl text-[17px] leading-7 text-ink-600">{t("about.lead")}</p>
        <div className="mt-6">
          <Tabs value={tab} onChange={(k) => setParams(k === "tech" ? { tab: "tech" } : {}, { replace: true })}
            tabs={[{ key: "overview", label: t("about.tab.overview") }, { key: "tech", label: t("about.tab.tech") }]} />
        </div>
      </header>

      {tab === "tech" ? <TechFaq /> : <>
      <Tour ref={tour} />

      <section className="about-sec flex flex-col gap-5">
        <div>
          <h2 className="font-display text-[28px] font-bold leading-tight text-ink-900">{t("about.sec.compare")}</h2>
          <p className="mt-2 max-w-3xl text-[16px] leading-7 text-ink-600">{t("about.sec.compareLead")}</p>
        </div>
        <div className="matrix-wrap">
          <table className="matrix">
            <thead><tr><th>{t("about.cmp.task")}</th><th>{t("about.cmp.rag")}</th><th>{t("about.cmp.graph")}</th><th className="us">{t("about.cmp.us")}</th></tr></thead>
            <tbody>
              {MATRIX.map(([n, rag, graph, us]) => (
                <tr key={n}>
                  <td>{t(`about.cmp.r${n}`)}</td>
                  <td>{cell(rag, `about.cmp.r${n}.rag`)}</td>
                  <td>{cell(graph, `about.cmp.r${n}.graph`)}</td>
                  <td className="us">{cell(us, `about.cmp.r${n}.us`)}<div className="ex"><b>{t("about.cmp.example")}</b> {rich(t(`about.cmp.r${n}.ex`))}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="card p-5">
            <h3 className="font-display text-[19px] font-bold text-ink-900">{t("about.enough.title")}</h3>
            <ul className="mt-2 flex list-disc flex-col gap-1.5 pl-5 text-[15px] leading-6 text-ink-600">{[1, 2, 3].map((k) => <li key={k}>{t(`about.enough.${k}`)}</li>)}</ul>
          </div>
          <div className="card p-5">
            <h3 className="font-display text-[19px] font-bold text-ink-900">{t("about.limits.title")}</h3>
            <ul className="mt-2 flex list-disc flex-col gap-1.5 pl-5 text-[15px] leading-6 text-ink-600">{[1, 2, 4, 3].map((k) => <li key={k}>{t(`about.limits.${k}`)}</li>)}</ul>
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-5">
        <div>
          <h2 className="font-display text-[28px] font-bold leading-tight text-ink-900">{t("about.sec.transcript")}</h2>
          <p className="mt-2 max-w-3xl text-[16px] leading-7 text-ink-600">{t("about.sec.transcriptLead")}</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {CHAPTERS.map((c, ci) => (
            <div key={c.id} className="card p-5">
              <h3 className="font-display text-[18px] font-bold text-ink-900">{ci + 1}. {t(`about.ch.${c.id}`)}</h3>
              <ol className="mt-2 flex list-decimal flex-col gap-1.5 pl-5 text-[14.5px] leading-6 text-ink-600">
                {c.beats.map((_, bi) => (
                  <li key={bi}>
                    <button type="button" className="rounded text-left hover:text-brand-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600"
                      onClick={() => { tour.current?.seekTo(ci, bi); top.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>
                      {t(`about.cap.${c.id}.${bi}`)}
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
      </section>
      </>}
    </Page>
  );
}
