// Giới thiệu, 3 tab: Tổng quan (video minh họa các luồng + so sánh với RAG / Graph RAG), Kiến trúc (sơ đồ toàn platform +
// video đường đi của dữ liệu: một file bị tách, embed ra sao và một câu hỏi đi qua những gì) và Hỏi đáp kỹ thuật.
// ?tab=arch | ?tab=tech mở thẳng tab tương ứng.
import { useSearchParams } from "react-router-dom";
import { Architecture } from "@/components/about/Architecture";
import { TechFaq } from "@/components/about/TechFaq";
import { rich, Tour } from "@/components/about/Tour";
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
type TabKey = "overview" | "arch" | "tech";

export function AboutPage() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: TabKey = raw === "tech" || raw === "arch" ? raw : "overview";
  const markLabel = (m: Mark) => t(m === "y" ? "about.cmp.yes" : m === "p" ? "about.cmp.part" : "about.cmp.no");
  const note = (key: string) => { const v = t(key); return v === key ? null : <div className="ex">{v}</div>; };
  const cell = (m: Mark, key: string) => (
    <><span className={`mark ${m}`}><i />{markLabel(m)}</span>{note(key)}</>
  );

  return (
    <Page className="flex flex-col gap-12">
      <header>
        <h1 className="font-display text-[clamp(32px,4.4vw,52px)] font-extrabold leading-[1.04] tracking-tight text-ink-900">{t("about.title")}</h1>
        <p className="mt-3 max-w-3xl text-[17px] leading-7 text-ink-600">{t("about.lead")}</p>
        <div className="mt-6">
          <Tabs<TabKey> value={tab} onChange={(k) => setParams(k === "overview" ? {} : { tab: k }, { replace: true })}
            tabs={[{ key: "overview", label: t("about.tab.overview") }, { key: "arch", label: t("about.tab.arch") }, { key: "tech", label: t("about.tab.tech") }]} />
        </div>
      </header>

      {tab === "tech" && <TechFaq />}
      {tab === "arch" && <Architecture />}
      {tab === "overview" && <>
        <Tour />

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
        </section>
      </>}
    </Page>
  );
}
