// Video minh họa trang Giới thiệu: 7 chương, mỗi chương một "cảnh" trên sân khấu 1280x720 (bộ phát: VideoPlayer).
// Phần tử <At s e> hiện từ bước s tới trước bước e.
import clsx from "clsx";
import { forwardRef, useContext, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { fmtNumber } from "@/lib/format";
import { At, StepCtx, VideoPlayer, type Chapter, type VideoHandle } from "./VideoPlayer";
import "./tour.css";

// Thời lượng (giây) từng cảnh + bước đang sáng trên thanh quy trình (r).
export const CHAPTERS: Chapter[] = [
  { id: "intro", beats: [{ d: 6 }, { d: 5 }, { d: 8 }, { d: 9 }, { d: 6 }] },
  { id: "ingest", beats: [{ d: 7, r: 0 }, { d: 8, r: 0 }, { d: 10, r: 1 }, { d: 9, r: 2 }, { d: 9, r: 3 }, { d: 8, r: 4 }, { d: 9, r: 5 }, { d: 8, r: 6 }, { d: 8, r: 7 }, { d: 6, r: 7 }] },
  { id: "ask", beats: [{ d: 8, r: 0 }, { d: 9, r: 1 }, { d: 8, r: 2 }, { d: 8, r: 3 }, { d: 10, r: 4 }, { d: 9, r: 5 }, { d: 8, r: 6 }, { d: 10, r: 7 }, { d: 8, r: 8 }] },
  { id: "numbers", beats: [{ d: 9, r: 0 }, { d: 8, r: 1 }, { d: 9, r: 2 }, { d: 7, r: 3 }, { d: 8, r: 4 }] },
  { id: "model", beats: [{ d: 9, r: 0 }, { d: 7, r: 1 }, { d: 8, r: 2 }, { d: 10, r: 3 }, { d: 10, r: 4 }, { d: 8, r: 5 }, { d: 7, r: 6 }] },
  { id: "report", beats: [{ d: 7, r: 0 }, { d: 8, r: 1 }, { d: 6, r: 2 }] },
  { id: "compare", beats: [{ d: 8 }, { d: 11 }] },
];

/** Chuỗi dịch có định dạng nhẹ: [[n]] chip trích dẫn, ==x== đánh dấu bằng chứng, **x** đậm, `x` mono. */
export function rich(s: string): ReactNode {
  const parts = s.split(/(\[\[\d+\]\]|==[^=]+==|\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) => {
    if (/^\[\[\d+\]\]$/.test(p)) return <span key={i} className="cite">{p.slice(2, -2)}</span>;
    if (/^==.+==$/.test(p)) return <span key={i} className="mk">{p.slice(2, -2)}</span>;
    if (/^\*\*.+\*\*$/.test(p)) return <b key={i}>{p.slice(2, -2)}</b>;
    if (/^`.+`$/.test(p)) return <span key={i} className="mono">{p.slice(1, -1)}</span>;
    return p;
  });
}

function Rail({ prefix, n, r }: { prefix: string; n: number; r?: number }) {
  const { t } = useI18n();
  return (
    <ol className="rail">
      {Array.from({ length: n }, (_, k) => (
        <li key={k} className={clsx(r === k && "now", r !== undefined && k < r && "done")}><b>{k + 1}</b>{t(`${prefix}.r${k}`)}</li>
      ))}
    </ol>
  );
}

function Count({ n }: { n: number }) {
  const step = useContext(StepCtx);
  const [v, setV] = useState(n);
  const on = step >= 9;
  useEffect(() => {
    if (!on || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setV(n); return; }
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / 1100);
      setV(Math.round(n * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [on, n]);
  return <b className="big">{fmtNumber(v)}</b>;
}

function Header({ flow, title }: { flow: string; title: string }) {
  return <div className="sh"><span className="ch">{flow}</span><h3>{title}</h3></div>;
}

// ------------------------------------------------------------------ scenes
function IntroScene() {
  const { t } = useI18n();
  return (
    <>
      <At s={0} e={1} className="intro-title"><h3>{t("about.intro.title")}</h3><p>{t("about.intro.sub")}</p></At>
      <At s={1} className="intro-q"><div className="bubble">{t("about.intro.q")}</div></At>
      <div className="duel">
        <At s={2} className="card">
          <h4>{t("about.intro.naiveTitle")}</h4>
          <span className="k">{t("about.intro.naiveHow")}</span>
          <div className="ans">{t("about.intro.naiveAns")}</div>
          <div style={{ marginTop: "auto" }}><span className="tag a">{t("about.intro.naiveVerdict")}</span></div>
        </At>
        <At s={3} className="card">
          <h4>KB Explorer</h4>
          <span className="k">{rich(t("about.intro.usHow"))}</span>
          <table className="mini">
            <thead><tr><th>{t("about.intro.colProgram")}</th><th>{t("about.intro.colApplies")}</th><th>{t("about.intro.colDates")}</th><th /></tr></thead>
            <tbody>
              <tr><td>{t("about.promo.school")}</td><td>{t("about.cat.electronics")}</td><td className="mono">{t("about.intro.d.school")}</td><td><span className="cite">2</span></td></tr>
              <tr><td>{t("about.promo.flash")}</td><td>{t("about.cat.all")}</td><td className="mono">{t("about.intro.d.flash")}</td><td><span className="cite">3</span></td></tr>
              <tr><td>{t("about.promo.thanks")}</td><td>{t("about.cat.all")}</td><td className="mono">{t("about.intro.d.thanks")}</td><td><span className="cite">4</span></td></tr>
            </tbody>
          </table>
          <span className="small">{t("about.intro.q4")}</span>
          <div style={{ marginTop: "auto" }}><span className="tag v">{t("about.intro.usVerdict")}</span></div>
        </At>
      </div>
      <At s={4} className="lanes3">
        {[1, 2, 3].map((k) => <div key={k} className="card"><b>{t(`about.intro.lane${k}`)}</b><span className="small">{t(`about.intro.lane${k}Hint`)}</span></div>)}
      </At>
    </>
  );
}

function IngestScene({ r }: { r?: number }) {
  const { t } = useI18n();
  const files: [string, number, string, string][] = [
    ["DOCX", 5, "word", "01_chinh_sach_doi_tra_hang_hoa.docx"], ["XLSX", 5, "excel", "01_bang_gia_sku.xlsx"],
    ["PDF", 5, "pdf", "02_catalogue_dien_tu_tieu_dung.pdf"], ["PNG", 5, "image", "01_poster_khuyen_mai.png"],
    ["MP3", 3, "audio", "01_training_chao_don_khach_hang.mp3"], ["MP4", 2, "video", "01_video_quy_trinh_thanh_toan_quay_thu_ngan.mp4"],
  ];
  const bars = useMemo(() => { let s = 7; return Array.from({ length: 48 }, () => { s = (s * 9301 + 49297) % 233280; return 18 + (s / 233280) * 82; }); }, []);
  return (
    <>
      <Header flow={t("about.flow1")} title={t("about.ch.ingest")} />
      <Rail prefix="about.ingest" n={8} r={r} />
      <div className="body">
        <At s={0} e={1} className="ps">
          <div className="files">
            {files.map(([ico, n, k, fn]) => (
              <div key={ico} className="card ft"><span className="ico">{ico}</span><div><span className="n">{n}</span> {t(`about.ingest.ft.${k}`)}<div className="fn">{fn}</div></div></div>
            ))}
          </div>
        </At>
        <At s={1} e={2} className="ps">
          <div className="two">
            <div className="card" style={{ padding: "20px 22px", display: "flex", flexDirection: "column", gap: 12 }}>
              <span className="k">{t("about.ingest.fpTitle")}</span>
              <div className="flow"><span className="tag">01_bang_gia_sku.xlsx</span><span className="arrow run"><i /></span><span className="tag b mono">sha256</span></div>
              <div className="flow"><span className="tag">01_bang_gia_sku (1).xlsx</span><span className="arrow" /><span className="tag v">{t("about.ingest.dup")}</span></div>
              <p className="small" style={{ margin: 0 }}>{t("about.ingest.fpNote")}</p>
            </div>
            <div className="card" style={{ padding: "20px 22px", display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="k">{t("about.ingest.magicTitle")}</span>
              {([["50 4B 03 04", "office"], ["25 50 44 46", "pdf"], ["89 50 4E 47", "png"], ["49 44 33", "mp3"]] as const).map(([b, k]) => (
                <div key={k} className="mrow"><span className="bytes">{b}</span><span>{t(`about.ingest.m.${k}`)}</span></div>
              ))}
            </div>
          </div>
        </At>
        <At s={2} e={3} className="ps">
          <div className="parse">
            <div className="card prow"><span className="who">Word</span><span className="tag">{t("about.ingest.p.wordTag")}</span><span className="out">CHÍNH SÁCH ĐỔI TRẢ › Chương II › Điều 5. Thời hạn đổi trả theo ngành hàng</span></div>
            <div className="card prow"><span className="who">Excel</span><span className="tag">{t("about.ingest.p.excelTag")}</span><span className="out">sku là ĐT-018, ten_san_pham là Loa bluetooth di động 30W - NovaPhone, gia là 1440000</span></div>
            <div className="card prow"><span className="who">PDF</span><span className="tag">{t("about.ingest.p.pdfTag")}</span><span className="out">Mã SP | Tên sản phẩm | Thông số chính | Bảo hành | Giá tham khảo</span></div>
            <div className="card prow"><span className="who">{t("about.ingest.p.audio")}</span><span className="tag">{t("about.ingest.p.audioTag")}</span><span className="out"><span className="mono">[00:00:09]</span> Một trong những kỹ năng quan trọng nhất mà mọi nhân viên bán hàng cần nắm vững.</span></div>
            <div className="card prow"><span className="who">{t("about.ingest.p.image")}</span><span className="tag b">{t("about.ingest.p.imageTag")}</span><span className="out">GREENMART VIỆT NAM · KHAI TRƯƠNG CHI NHÁNH THỦ ĐỨC · giảm đến 30%</span></div>
          </div>
        </At>
        <At s={3} e={4} className="ps">
          <div className="two">
            <div className="card rec">
              <span className="k">{t("about.ingest.recWord")}</span>
              <div className="txt">Điều 5. Thời hạn đổi trả theo ngành hàng. Thời hạn đổi trả được quy định khác nhau theo từng ngành hàng…</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}><span className="tag b">{t("about.ingest.tag.policy")}</span><span className="tag">{t("about.ingest.tag.eff1")}</span><span className="tag">{t("about.ingest.tag.article")}</span></div>
            </div>
            <div className="card rec">
              <span className="k">{t("about.ingest.recExcel")}</span>
              <div className="txt">{t("about.ingest.recExcelText")}</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}><span className="tag b">{t("about.ingest.tag.priceList")}</span><span className="tag">{t("about.ingest.tag.eff8")}</span><span className="tag mk">SKU ĐT-018</span><span className="tag">{t("about.ingest.tag.row")}</span></div>
            </div>
          </div>
          <div className="card" style={{ marginTop: 20, padding: "18px 22px" }}>
            <div className="flow"><span className="tag">{t("about.ingest.v.old")}</span><span className="arrow run"><i /></span><span className="tag b">{t("about.ingest.v.new")}</span><span className="arrow" /><span className="small">{t("about.ingest.v.result")}</span></div>
          </div>
        </At>
        <At s={4} e={5} className="ps">
          <div className="two">
            <div className="card" style={{ padding: "22px 24px", borderColor: "var(--alert)" }}>
              <span className="k">{t("about.ingest.susTitle")}</span>
              <p style={{ fontSize: 19, margin: "10px 0 14px" }}>{t("about.ingest.susQuote")}</p>
              <span className="tag a">{t("about.ingest.susVerdict")}</span>
            </div>
            <div className="card" style={{ padding: "22px 24px" }}>
              <span className="k">{t("about.ingest.wrapTitle")}</span>
              <div className="code" style={{ marginTop: 10, fontSize: 14 }}>
                &lt;<span className="kw">untrusted_document</span> source="bảng giá tháng 8,{"\n"}  Sheet BangGiaSKU dòng 3"&gt;{"\n"}sku là ĐT-018, ten_san_pham là Loa…{"\n"}&lt;/<span className="kw">untrusted_document</span>&gt;
              </div>
              <p className="small" style={{ margin: "10px 0 0" }}>{t("about.ingest.wrapNote")}</p>
            </div>
          </div>
        </At>
        <At s={5} e={6} className="ps">
          <div className="parent">
            <span className="k" style={{ color: "var(--brand)" }}>{t("about.ingest.parent")}</span>
            <div className="kids">
              <div className="kid">Điều 5. Thời hạn đổi trả theo ngành hàng. Thời hạn đổi trả được quy định khác nhau…</div>
              <div className="kid">Ngành hàng tiêu dùng nhanh có thời hạn ngắn hơn do đặc thù dễ hư hỏng…</div>
              <div className="kid">…ngành hàng điện máy có thời hạn dài hơn để phù hợp với chu kỳ sử dụng…</div>
              <div className="kid">Hàng điện máy, gia dụng: 30 ngày kể từ ngày mua…</div>
            </div>
          </div>
          <div className="flow" style={{ marginTop: 14 }}><span className="tag b">{t("about.ingest.childTag")}</span><span className="arrow" /><span className="small">{t("about.ingest.childNote")}</span></div>
        </At>
        <At s={6} e={7} className="ps">
          <div className="two">
            <div className="card" style={{ padding: "22px 24px" }}>
              <span className="k">{t("about.ingest.bm25")}</span>
              <div style={{ margin: "14px 0 8px" }}>{["loa", "bluetooth", "di_động"].map((x) => <span key={x} className="tok">{x}</span>)}<span className="tok sku">ĐT-018</span><span className="tok">ngành_hàng</span><span className="tok">điện_tử</span></div>
              <p className="small" style={{ margin: 0 }}>{t("about.ingest.bm25Note")}</p>
            </div>
            <div className="card" style={{ padding: "22px 24px" }}>
              <span className="k">{t("about.ingest.vec")}</span>
              <div className="vec" style={{ margin: "14px 0 8px" }}>{bars.map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}</div>
              <p className="small" style={{ margin: 0 }}>{t("about.ingest.vecNote")}</p>
            </div>
          </div>
        </At>
        <At s={7} e={8} className="ps">
          <div className="two" style={{ alignItems: "center" }}>
            <div className="card" style={{ padding: "18px 20px" }}>
              <span className="k">01_bang_gia_sku.xlsx, BangGiaSKU</span>
              <table className="sheet" style={{ marginTop: 10 }}>
                <thead><tr>
                  <th>sku<span className="ty">{t("about.ingest.ty.text")}</span></th><th>ten_san_pham<span className="ty">{t("about.ingest.ty.text")}</span></th>
                  <th>gia<span className="ty">{t("about.ingest.ty.int")}</span></th><th>category<span className="ty">{t("about.ingest.ty.text")}</span></th>
                </tr></thead>
                <tbody>
                  <tr><td>ĐT-018</td><td>Loa bluetooth di động 30W</td><td>1440000</td><td>Điện tử</td></tr>
                  <tr><td>ĐT-010</td><td>Loa bluetooth di động 20W</td><td>1389000</td><td>Điện tử</td></tr>
                  <tr><td>TT-018</td><td>Quần jean size 29</td><td>260000</td><td>Thời trang</td></tr>
                </tbody>
              </table>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <span className="big" style={{ fontSize: 64, lineHeight: 1 }}>{t("about.ingest.tables")}</span>
              <p className="small" style={{ margin: 0, fontSize: 17 }}>{t("about.ingest.tablesNote")}</p>
            </div>
          </div>
        </At>
        <At s={8} e={9} className="ps">
          <svg viewBox="0 0 1160 470" width="1160" height="470" role="img" aria-label={t("about.ingest.g.aria")}>
            <g stroke="var(--line)" strokeWidth="2" fill="none">
              <line x1="280" y1="235" x2="560" y2="235" /><line x1="560" y1="235" x2="860" y2="110" /><line x1="560" y1="235" x2="860" y2="235" />
              <line x1="560" y1="235" x2="860" y2="360" /><line x1="280" y1="235" x2="160" y2="90" /><line x1="280" y1="235" x2="160" y2="380" />
            </g>
            <g fontSize="13" fill="var(--ink3)" textAnchor="middle">
              <text x="420" y="225">{t("about.ingest.g.inCat")}</text><text x="720" y="160">{t("about.ingest.g.applies")}</text>
              <text x="720" y="225">{t("about.ingest.g.applies")}</text><text x="720" y="315">{t("about.ingest.g.returns")}</text>
              <text x="205" y="150">{t("about.ingest.g.stocked")}</text><text x="205" y="320">{t("about.ingest.g.supplied")}</text>
            </g>
            <g fontWeight="600" fontSize="16" textAnchor="middle">
              <rect x="200" y="210" width="160" height="50" rx="25" fill="var(--marker)" /><text x="280" y="241" fill="var(--marker-ink)">SKU ĐT-018</text>
              <rect x="470" y="210" width="180" height="50" rx="25" fill="var(--brand)" /><text x="560" y="241" fill="#fff">{t("about.ingest.g.cat")}</text>
              <rect x="760" y="85" width="210" height="50" rx="12" fill="var(--surface)" stroke="var(--line)" /><text x="865" y="116" fill="var(--ink)">{t("about.promo.school")}</text>
              <rect x="760" y="210" width="210" height="50" rx="12" fill="var(--surface)" stroke="var(--line)" /><text x="865" y="241" fill="var(--ink)">{t("about.ingest.g.flash")}</text>
              <rect x="760" y="335" width="210" height="50" rx="12" fill="var(--surface)" stroke="var(--line)" /><text x="865" y="366" fill="var(--ink)">{t("about.ingest.g.policy")}</text>
              <rect x="70" y="65" width="180" height="50" rx="12" fill="var(--surface)" stroke="var(--line)" /><text x="160" y="96" fill="var(--ink)">{t("about.ingest.g.store")}</text>
              <rect x="70" y="355" width="180" height="50" rx="12" fill="var(--surface)" stroke="var(--line)" /><text x="160" y="386" fill="var(--ink)">{t("about.ingest.g.supplier")}</text>
            </g>
          </svg>
        </At>
        <At s={9} className="ps">
          <div className="counts">
            <div className="c"><Count n={25} /><span>{t("about.ingest.c.files")}</span></div>
            <span className="arrow" style={{ maxWidth: 60 }} />
            <div className="c"><Count n={641} /><span>{t("about.ingest.c.passages")}</span></div>
            <span className="arrow" style={{ maxWidth: 60 }} />
            <div className="c"><Count n={14} /><span>{t("about.ingest.c.tables")}</span></div>
            <span className="arrow" style={{ maxWidth: 60 }} />
            <div className="c"><Count n={457} /><span>{t("about.ingest.c.things")}</span></div>
          </div>
        </At>
      </div>
    </>
  );
}

function AskScene({ r }: { r?: number }) {
  const { t } = useI18n();
  const promos: [string, string, boolean, number, number, number][] = [
    // [tên, phạm vi, áp dụng cho ĐT-018?, y, x, width] — trục: 01/07 = x 270, ~8.1px/ngày
    ["about.promo.opening", "about.ask.tl.openingScope", true, 36, 270, 57],
    ["about.promo.green", "about.ask.tl.greenScope", false, 94, 351, 89],
    ["about.promo.fmcg", "about.ask.tl.fmcgScope", false, 152, 465, 24],
    ["about.promo.school", "about.ask.tl.schoolScope", true, 210, 642, 137],
    ["about.promo.flash", "about.ask.tl.allScope", true, 268, 884, 16],
    ["about.promo.thanks", "about.ask.tl.allScope", true, 326, 925, 49],
  ];
  return (
    <>
      <Header flow={t("about.flow2")} title={t("about.ask.title")} />
      <Rail prefix="about.ask" n={9} r={r} />
      <div className="body">
        <At s={0} e={1} className="ps">
          <div style={{ display: "flex", justifyContent: "flex-end" }}><div className="bubble" style={{ fontSize: 24 }}>{t("about.intro.q")}</div></div>
          <div className="tools4">
            {(["search", "tables", "graph", "read"] as const).map((k) => (
              <div key={k} className={clsx("card", k === "search" && "pick")}><b>{t(`about.ask.t.${k}`)}</b><span className="small">{t(`about.ask.t.${k}Hint`)}</span></div>
            ))}
          </div>
          <p className="small" style={{ marginTop: 16, fontSize: 16 }}>{t("about.ask.toolNote")} <span className="mono" style={{ color: "var(--brand)" }}>search_documents("Loa ĐT-018 sản phẩm ngành hàng")</span></p>
        </At>
        <At s={1} e={2} className="ps">
          <div className="two">
            <div>
              <span className="k">{t("about.ask.kw")}</span>
              <div className="list" style={{ marginTop: 10 }}>
                <div className="li top"><span className="r">#1</span><span className="t">sku là <span className="mk">ĐT-018</span>, Loa bluetooth di động 30W, gia là 1440000</span></div>
                <div className="li"><span className="r">#2</span><span className="t">sku là ĐT-010, Loa bluetooth di động 20W…</span></div>
                <div className="li"><span className="r">#3</span><span className="t">sku là ĐT-002, Loa bluetooth di động 10W…</span></div>
                <div className="li"><span className="r">#4</span><span className="t">DT-002 | Loa bluetooth di động | Công suất 20W…</span></div>
              </div>
            </div>
            <div>
              <span className="k">{t("about.ask.sem")}</span>
              <div className="list" style={{ marginTop: 10 }}>
                <div className="li"><span className="r">#1</span><span className="t">sku là ĐT-010, Loa bluetooth di động 20W…</span></div>
                <div className="li top"><span className="r">#2</span><span className="t">sku là <span className="mk">ĐT-018</span>, Loa bluetooth di động 30W…</span></div>
                <div className="li"><span className="r">#3</span><span className="t">CATALOGUE SẢN PHẨM - ĐIỆN TỬ TIÊU DÙNG…</span></div>
                <div className="li"><span className="r">#4</span><span className="t">sku là ĐT-026, Loa bluetooth di động 10W…</span></div>
              </div>
            </div>
          </div>
          <p className="small" style={{ marginTop: 16 }}>{t("about.ask.twoNote")}</p>
        </At>
        <At s={2} e={3} className="ps">
          <div className="two" style={{ alignItems: "center" }}>
            <div className="card" style={{ padding: "22px 24px" }}>
              <span className="k">{t("about.ask.rrf")}</span>
              <div className="code" style={{ marginTop: 12, fontSize: 18 }}>{t("about.ask.rrfFormula")}</div>
              <p className="small" style={{ margin: "12px 0 0" }}>{t("about.ask.rrfNote")}</p>
            </div>
            <div className="list">
              <div className="li top"><span className="r">#1</span><span className="t"><span className="mk">ĐT-018</span> {t("about.ask.i.speaker30")}</span><span className="score">1/61 + 1/62</span></div>
              <div className="li"><span className="r">#2</span><span className="t">ĐT-010 {t("about.ask.i.speaker20")}</span><span className="score">1/62 + 1/61</span></div>
              <div className="li"><span className="r">#3</span><span className="t">{t("about.ask.i.catalogue")}</span><span className="score">1/64 + 1/63</span></div>
              <div className="li"><span className="r">#4</span><span className="t">ĐT-002 {t("about.ask.i.speaker10")}</span><span className="score">1/63</span></div>
            </div>
          </div>
        </At>
        <At s={3} e={4} className="ps">
          <div className="two" style={{ alignItems: "center" }}>
            <div className="list">
              {([["about.ask.i.speaker30cat", "94%", true], ["about.ask.i.catalogue", "58%", false], ["about.ask.i.speaker20", "41%", false], ["about.ask.i.speaker10", "33%", false]] as const).map(([k, w, top]) => (
                <div key={k} className={clsx("li", top && "top")}><span className="t">{top && <span className="mk">ĐT-018</span>} {t(k)}</span><span className="barw"><i style={{ "--w": w } as CSSProperties} /></span></div>
              ))}
            </div>
            <div className="card" style={{ padding: "22px 24px" }}>
              <span className="k">{t("about.ask.rerank")}</span>
              <p style={{ margin: "10px 0 12px", fontSize: 17 }}>{t("about.ask.rerankBody")}</p>
              <span className="tag a">{t("about.ask.rerankTag")}</span>
            </div>
          </div>
          <p className="note" style={{ marginTop: 14 }}>{t("about.ask.rerankNote")}</p>
        </At>
        <At s={4} e={5} className="ps">
          <div className="chain">
            {([["Loa ĐT-018 sản phẩm ngành hàng", "about.ask.hop1"], ["khuyến mãi ưu đãi ngành hàng Điện tử", "about.ask.hop2"], ["lịch khuyến mãi tháng 10 quý 4 2026 Điện tử", "about.ask.hop3"]] as const).map(([q, k], i) => (
              <div key={k} className="hop"><span className="n">{i + 1}</span><div className="card"><div className="q">search_documents("{q}")</div><div>{rich(t(k))}</div></div></div>
            ))}
          </div>
        </At>
        <At s={5} e={6} className="ps">
          <svg viewBox="0 0 1160 420" width="1160" height="420" role="img" aria-label={t("about.ask.tl.aria")}>
            <g fontFamily="JetBrains Mono, monospace" fontSize="13" fill="var(--ink3)">
              <text x="270" y="400">{t("about.ask.tl.jul")}</text><text x="520" y="400">{t("about.ask.tl.aug")}</text><text x="771" y="400">{t("about.ask.tl.sep")}</text><text x="1013" y="400">{t("about.ask.tl.oct")}</text>
            </g>
            <g stroke="var(--line)"><line x1="270" y1="20" x2="270" y2="380" /><line x1="520" y1="20" x2="520" y2="380" /><line x1="771" y1="20" x2="771" y2="380" /><line x1="1013" y1="20" x2="1013" y2="380" /></g>
            {promos.map(([name, scope, applies, y, x, w]) => (
              <g key={name}>
                <text x="0" y={y + 16} fontSize="15" fontWeight="600" fill={applies ? "var(--ink)" : "var(--ink3)"}>{t(name)}</text>
                <text x="0" y={y + 34} fontSize="12.5" fill="var(--ink3)">{t(scope)}</text>
                <rect x={x} y={y} width={w} height="26" rx="5" fill={applies ? "var(--brand)" : "var(--muted-bar)"} />
              </g>
            ))}
            <line x1="1078" y1="10" x2="1078" y2="380" stroke="var(--alert)" strokeWidth="3" strokeDasharray="6 5" />
            <rect x="1026" y="0" width="104" height="26" rx="6" fill="var(--alert)" />
            <text x="1078" y="18" textAnchor="middle" fontSize="13" fontWeight="700" fill="#fff">{t("about.ask.tl.today")}</text>
          </svg>
        </At>
        <At s={6} e={7} className="ps">
          <div className="card answer">
            <p>{rich(t("about.ask.a1"))}</p>
            <p>{rich(t("about.ask.a2"))}</p>
            <p style={{ color: "var(--ink2)" }}>{t("about.ask.a3")}</p>
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: 16, flexWrap: "wrap" }}>
            {[1, 2, 3, 4].map((n) => <span key={n} className="tag"><span className="cite">{n}</span> {t(`about.ask.src${n}`)}</span>)}
          </div>
        </At>
        <At s={7} e={8} className="ps">
          <div className="two">
            <div className="list" style={{ gap: 10 }}>
              <div className="sent"><span>{t("about.ask.chk.real")}</span><span className="st ok">{t("about.ask.chk.realS")}</span></div>
              <div className="sent bad"><span>{rich(t("about.ask.chk.fake"))}</span><span className="st no">{t("about.ask.chk.fakeS")}</span></div>
              <div className="sent bad"><span>{t("about.ask.chk.none")}</span><span className="st no">{t("about.ask.chk.noneS")}</span></div>
              <div className="sent"><span>{t("about.ask.chk.gate")}</span><span className="st ok">{t("about.ask.chk.gateS")}</span></div>
            </div>
            <div className="guards">
              {(["money", "conflict", "scope"] as const).map((k) => <div key={k} className="card"><b>{t(`about.ask.g.${k}`)}</b><span className="small">{t(`about.ask.g.${k}Hint`)}</span></div>)}
            </div>
          </div>
        </At>
        <At s={8} className="ps">
          <div className="card" style={{ padding: "28px 26px 30px" }}>
            <div className="trail">
              {(["understood", "looked", "found", "checked", "conf"] as const).map((k, i) => (
                <div key={k} className="s"><div className="dot">{i + 1}</div><span className="k">{t(`about.ask.tr.${k}`)}</span><span className="v">{t(`about.ask.tr.${k}V`)}</span></div>
              ))}
            </div>
          </div>
          <p className="small" style={{ marginTop: 18, fontSize: 16 }}>{t("about.ask.trNote")}</p>
        </At>
      </div>
    </>
  );
}

function NumbersScene({ r }: { r?: number }) {
  const { t } = useI18n();
  const tables: [string, number][] = [["Site Summary", 1000], ["Subject Summary (IRT)", 1000], ["IRT Study at a Glance", 109], ["PSO Monitoring Visits", 1000], ["PSO FUIs", 1000], ["StreamPageComp", 1000],
    ["StreamQueryDetail", 1000], ["Subject Tier - Visit Dates", 1000], ["SAL", 1000], ["Tableau Monitoring Metrics", 1000], ["Sheet11", 1000]];
  const rows: [string, string, number, number, number, number][] = [["STE2708", "Category 3", 3, 1, 1, 1], ["STE9133", "Category 5", 3, 1, 0, 2], ["STE2334", "Category 5", 2, 0, 1, 1], ["STE6347", "Category 7", 2, 1, 0, 1], ["STE4571", "Category 1", 2, 0, 0, 2]];
  return (
    <>
      <Header flow={t("about.flow2b")} title={t("about.num.title")} />
      <Rail prefix="about.num" n={5} r={r} />
      <div className="body">
        <At s={0} e={1} className="ps">
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 18 }}><div className="bubble" style={{ fontSize: 21 }}>Which 5 sites have the most PSO follow-up items still open, and who is their primary CRA?</div></div>
          <span className="k">{t("about.num.ws")}</span>
          <div className="tbls" style={{ marginTop: 10 }}>
            {tables.map(([n, c]) => <div key={n} className={clsx("tb", n === "PSO FUIs" && "pick")}>{n}<small>{t("about.rows", { n: fmtNumber(c) })}</small></div>)}
          </div>
        </At>
        <At s={1} e={2} className="ps">
          <div className="code" style={{ fontSize: 16 }}>
            <span className="kw">SELECT</span> site, primary_cra,{"\n"}       <span className="kw">COUNT</span>(*) <span className="kw">AS</span> total_open_fuis,{"\n"}       <span className="kw">SUM</span>(<span className="kw">CASE WHEN</span> follow_up_age &lt; 30 <span className="kw">THEN</span> 1 <span className="kw">ELSE</span> 0 <span className="kw">END</span>) <span className="kw">AS</span> fuis_under_30,{"\n"}       …{"\n"}<span className="kw">FROM</span> site_summary_fake_data_pso_fuis{"\n"}<span className="kw">WHERE</span> follow_up_status = <span className="s">'Open'</span>   <span style={{ color: "var(--ink3)" }}>{t("about.num.firstGuess")}</span>{"\n"}<span className="kw">GROUP BY</span> site, primary_cra <span className="kw">ORDER BY</span> total_open_fuis <span className="kw">DESC LIMIT</span> 5
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: 16, flexWrap: "wrap" }}>
            {(["select", "noWrite", "limits"] as const).map((k) => <span key={k} className="tag v">{t(`about.num.tag.${k}`)}</span>)}
          </div>
          <p className="note" style={{ marginTop: 12 }}>{t("about.num.sqlNote")}</p>
        </At>
        <At s={2} e={3} className="ps">
          <div className="two" style={{ alignItems: "center" }}>
            <div className="code" style={{ fontSize: 16 }}><span className="kw">SELECT</span> follow_up_status, <span className="kw">COUNT</span>(*){"\n"}<span className="kw">FROM</span> site_summary_fake_data_pso_fuis{"\n"}<span className="kw">GROUP BY</span> follow_up_status</div>
            <table className="res">
              <thead><tr><th>follow_up_status</th><th className="n">{t("about.num.rowsCol")}</th></tr></thead>
              <tbody><tr><td><span className="mk">Active</span></td><td className="n">610</td></tr><tr><td>Resolved</td><td className="n">390</td></tr></tbody>
            </table>
          </div>
          <div className="card" style={{ padding: "18px 22px", marginTop: 22 }}>{rich(t("about.num.fix"))}</div>
        </At>
        <At s={3} e={4} className="ps">
          <table className="res">
            <thead><tr><th>{t("about.num.h.rank")}</th><th>Site</th><th>Primary CRA</th><th className="n">{t("about.num.h.open")}</th><th className="n">{t("about.num.h.lt30")}</th><th className="n">30–60</th><th className="n">&gt; 60</th></tr></thead>
            <tbody>{rows.map((r, i) => <tr key={r[0]}><td>{i + 1}</td><td className="mono">{r[0]}</td><td>{r[1]}</td>{r.slice(2).map((v, k) => <td key={k} className="n">{v}</td>)}</tr>)}</tbody>
          </table>
          <p className="small" style={{ marginTop: 14 }}>{t("about.num.resNote")}</p>
        </At>
        <At s={4} className="ps">
          <div className="two" style={{ alignItems: "center" }}>
            <div className="chart">
              <span className="k">{t("about.num.chart")}</span>
              {rows.map((r) => <div key={r[0]} className="crow"><span>{r[0]}</span><span className="bw"><i style={{ "--w": `${(r[2] / 3) * 100}%` } as CSSProperties} /></span><span>{r[2]}</span></div>)}
            </div>
            <div className="card" style={{ padding: "22px 24px", display: "flex", flexDirection: "column", gap: 12 }}>
              <span className="tag"><span className="cite">1</span> {t("about.num.src")}</span>
              <p style={{ margin: 0, fontSize: 17 }}>{t("about.num.why")}</p>
              <p className="small" style={{ margin: 0 }}>{t("about.num.vsRag")}</p>
            </div>
          </div>
        </At>
      </div>
    </>
  );
}

function ModelScene({ r }: { r?: number }) {
  const { t } = useI18n();
  const sheets = ["Site Summary", "Subject Summary (IRT)", "IRT Study at a Glance", "PSO Monitoring Visits", "PSO FUIs", "StreamPageComp", "StreamQueryDetail", "Subject Tier - Visit Dates", "SAL", "Tableau Monitoring Metrics", "Sheet11"];
  return (
    <>
      <Header flow={t("about.flow3")} title={t("about.model.title")} />
      <Rail prefix="about.model" n={7} r={r} />
      <div className="body">
        <At s={0} e={1} className="ps">
          <div className="tabs">{sheets.map((s, i) => <span key={s} className={clsx(i === 0 && "main")}>{s}</span>)}</div>
          <div className="two" style={{ marginTop: 22 }}>
            <div className="card" style={{ padding: "20px 22px" }}><span className="big" style={{ fontSize: 54 }}>{t("about.model.sheets")}</span><p className="small" style={{ margin: "6px 0 0", fontSize: 16 }}>{t("about.model.sheetsNote")}</p></div>
            <div className="card" style={{ padding: "20px 22px", display: "flex", flexDirection: "column", gap: 10, alignItems: "flex-start" }}>
              <span className="tag b">{t("about.model.pivots")}</span><span className="tag a">{t("about.model.pasted")}</span>
              <p className="small" style={{ margin: 0 }}>{t("about.model.hard")}</p>
            </div>
          </div>
        </At>
        <At s={1} e={2} className="ps">
          <div className="two" style={{ alignItems: "center" }}>
            <div className="code" style={{ fontSize: 16 }}>
              <span className="kw">schema</span> dm_659116543e{"\n"}
              {([["site_summary", 1000], ["subject_summary_irt", 1000], ["irt_study_at_a_glance", 109], ["pso_fuis", 1000], ["streamquerydetail", 1000]] as const).map(([n, c]) => `  ${n.padEnd(24)}${t("about.rows", { n: fmtNumber(c) }).padStart(10)}\n`).join("")}
              {"  …   "}{t("about.model.tablesMore")}
            </div>
            <p style={{ fontSize: 18, margin: 0 }}>{rich(t("about.model.schemaNote"))}</p>
          </div>
        </At>
        <At s={2} e={3} className="ps">
          <svg viewBox="0 0 1160 470" width="1160" height="470" role="img" aria-label={t("about.model.g.aria")}>
            <g stroke="var(--line)" strokeWidth="2">
              <line x1="580" y1="235" x2="160" y2="70" /><line x1="580" y1="235" x2="160" y2="235" /><line x1="580" y1="235" x2="160" y2="400" />
              <line x1="580" y1="235" x2="1000" y2="70" /><line x1="580" y1="235" x2="1000" y2="235" /><line x1="580" y1="235" x2="1000" y2="400" />
            </g>
            <g textAnchor="middle">
              <rect x="470" y="200" width="220" height="70" rx="16" fill="var(--brand)" /><text x="580" y="232" fill="#fff" fontWeight="700" fontSize="18">Site Summary</text><text x="580" y="254" fill="#fff" fontSize="13">{t("about.model.g.main")}</text>
              <g fontSize="15" fontWeight="600" fill="var(--ink)">
                {([[40, 45, "PSO FUIs"], [40, 210, "Subject Summary (IRT)"], [40, 375, "StreamQueryDetail"], [880, 45, "Tableau Monitoring Metrics"], [880, 210, "SAL"], [880, 375, "PSO Monitoring Visits"]] as const).map(([x, y, n]) => (
                  <g key={n}><rect x={x} y={y} width="240" height="50" rx="12" fill="var(--surface)" stroke={n === "SAL" ? "var(--marker)" : "var(--line)"} strokeWidth={n === "SAL" ? 3 : 1} /><text x={x + 120} y={y + 31}>{n}</text></g>
                ))}
              </g>
              <g fontFamily="JetBrains Mono, monospace" fontSize="12.5" fill="var(--ink2)">
                <text x="370" y="140">Site # = Site #</text><text x="370" y="226">Site # = Site Number</text><text x="370" y="345">Site # = StudyEnvironmentSiteNumber</text>
                <text x="790" y="140">Site # = Site No</text><text x="790" y="345">Site # = Site #</text>
              </g>
              <rect x="690" y="165" width="270" height="34" rx="8" fill="var(--marker)" /><text x="825" y="187" fill="var(--marker-ink)" fontFamily="JetBrains Mono, monospace" fontSize="13">{t("about.model.g.digits")}</text>
            </g>
          </svg>
        </At>
        <At s={3} e={4} className="ps">
          <div className="two">
            <div className="ladder">
              <span className="k">{t("about.model.ladder")}</span>
              {[1, 2, 3, 4].map((k) => <div key={k} className="card rung"><span className="i">{k}</span><b>{t(`about.model.l${k}`)}</b><span className="small">{t(`about.model.l${k}h`)}</span></div>)}
            </div>
            <div className="card colcard">
              <span className="k">{t("about.model.example")}</span>
              <b style={{ fontSize: 20 }}>Days since last IMV</b>
              <div className="code" style={{ fontSize: 16, padding: "10px 14px" }}>DAYS(AS_OF(), [Last IMV])</div>
              <span className="small">{t("about.model.asOf")}</span>
              <div className="meter"><i style={{ "--w": "100%", background: "var(--verify)" } as CSSProperties} /></div>
              <span className="ok">{t("about.model.match")}</span>
            </div>
          </div>
        </At>
        <At s={4} e={5} className="ps">
          <div className="steps5">{(["sheets", "links", "calc", "build", "share"] as const).map((k) => <span key={k} className={clsx(k === "calc" && "cur")}>{t(`about.model.s.${k}`)}</span>)}</div>
          <div className="two" style={{ marginTop: 20 }}>
            <div className="card colcard">
              <b style={{ fontSize: 20 }}>Open Queries</b>
              <span className="small">{t("about.model.oqAi")}</span>
              <div className="meter"><i style={{ "--w": "0.8%", background: "var(--alert)" } as CSSProperties} /></div>
              <span className="no">{t("about.model.oqBad")}</span>
            </div>
            <div className="card colcard">
              <span className="k">{t("about.model.words")}</span>
              <p style={{ margin: 0, fontSize: 17 }}>{t("about.model.wordsQuote")}</p>
              <div className="flow"><span className="tag b">{t("about.model.toRule")}</span><span className="arrow run"><i /></span><span className="tag">{t("about.model.tested")}</span></div>
              <span className="note">{t("about.model.wordsNote")}</span>
            </div>
          </div>
        </At>
        <At s={5} e={6} className="ps">
          <div className="two" style={{ alignItems: "center" }}>
            <div>
              <span className="k">{t("about.model.recon")}</span>
              <div className="grid10" style={{ marginTop: 12 }}>{Array.from({ length: 100 }, (_, i) => <i key={i} className={clsx([7, 23, 24, 58, 81].includes(i) && "x")} />)}</div>
              <div style={{ display: "flex", gap: 10, marginTop: 12, flexWrap: "wrap" }}><span className="tag v">{t("about.model.pass")}</span><span className="tag">{t("about.model.partial")}</span><span className="tag a">{t("about.model.diff")}</span></div>
            </div>
            <p style={{ fontSize: 18, margin: 0 }}>{t("about.model.reconNote")}</p>
          </div>
          <p className="note" style={{ marginTop: 14 }}>{t("about.model.gridNote")}</p>
        </At>
        <At s={6} className="ps">
          <div className="share">{(["bi", "doc", "ask"] as const).map((k) => <div key={k} className="card"><b>{t(`about.model.${k}`)}</b><span className="small">{t(`about.model.${k}Hint`)}</span></div>)}</div>
        </At>
      </div>
    </>
  );
}

function ReportScene({ r }: { r?: number }) {
  const { t } = useI18n();
  return (
    <>
      <Header flow={t("about.flow4")} title={t("about.report.title")} />
      <Rail prefix="about.report" n={3} r={r} />
      <div className="body">
        <div className="two">
          <At s={0} className="card" style={{ padding: "22px 24px" }}>
            <span className="k">{t("about.report.request")}</span>
            <p style={{ fontSize: 19, margin: "8px 0 14px" }}>{t("about.report.ask")}</p>
            <ol style={{ margin: 0, paddingLeft: 22, display: "flex", flexDirection: "column", gap: 8, fontSize: 17, listStyle: "decimal" }}>
              {[1, 2, 3, 4].map((k) => <li key={k}>{t(`about.report.o${k}`)}</li>)}
            </ol>
          </At>
          <At s={1} className="card" style={{ padding: "22px 24px" }}>
            <span className="k">{t("about.report.sec")}</span>
            <p style={{ fontSize: 17, margin: "8px 0 12px" }}>{rich(t("about.report.para"))}</p>
            <div className="chart" style={{ gap: 8 }}>
              {([["m7", 33, 1], ["m8", 33, 1], ["m9", 66, 2]] as const).map(([m, w, n]) => <div key={m} className="crow"><span>{t(`about.report.${m}`)}</span><span className="bw"><i style={{ "--w": `${w}%` } as CSSProperties} /></span><span>{n}</span></div>)}
            </div>
          </At>
        </div>
        <At s={2} style={{ display: "flex", gap: 12, marginTop: 22, alignItems: "center" }}>
          <span className="k">{t("about.report.export")}</span>{["PDF", "Word", "Excel", "HTML"].map((f) => <span key={f} className="tag b">{f}</span>)}
          <span className="small" style={{ marginLeft: 12 }}>{t("about.report.pin")}</span>
        </At>
        <At s={0}><p className="note" style={{ marginTop: 12 }}>{t("about.report.note")}</p></At>
      </div>
    </>
  );
}

function CompareScene() {
  const { t } = useI18n();
  const rows: [string, "yes" | "part" | "none", "yes" | "part" | "none", string, number][] = [
    ["about.cmp.r1", "yes", "yes", "about.cmp.s.find", 0], ["about.cmp.r2", "none", "yes", "about.cmp.s.links", 0],
    ["about.cmp.r3", "none", "none", "about.cmp.s.sql", 1], ["about.cmp.r4", "none", "part", "about.cmp.s.steps", 1],
    ["about.cmp.r6", "none", "none", "about.cmp.s.refuse", 1], ["about.cmp.r7", "none", "none", "about.cmp.s.versions", 1],
    ["about.cmp.r12", "none", "none", "about.cmp.s.models", 1],
  ];
  const mark = (v: string) => t(v === "yes" ? "about.cmp.yes" : v === "part" ? "about.cmp.part" : "about.cmp.no");
  return (
    <>
      <Header flow={t("about.summary")} title={t("about.cmp.title")} />
      <div className="body" style={{ top: 130 }}>
        <div className="cmp3">
          <div className="h">{t("about.cmp.task")}</div><div className="h">{t("about.cmp.rag")}</div><div className="h">{t("about.cmp.graph")}</div><div className="h us">{t("about.cmp.us")}</div>
          {rows.map(([k, a, b, us, s]) => (
            <CmpRow key={k} s={s} cells={[<>{t(k)}</>, <span className={a}>{mark(a)}</span>, <span className={b}>{mark(b)}</span>, <span className="yes">{t(us)}</span>]} />
          ))}
        </div>
      </div>
    </>
  );
}

function CmpRow({ s, cells }: { s: number; cells: ReactNode[] }) {
  const step = useContext(StepCtx);
  const on = step >= s;
  return <>{cells.map((c, i) => <div key={i} className={clsx("ti", on && "on", i === 3 && "us")}>{c}</div>)}</>;
}

// ------------------------------------------------------------------ player
export type TourHandle = VideoHandle;

export const Tour = forwardRef<TourHandle>(function Tour(_, ref) {
  const { t } = useI18n();
  const scenes: Record<string, (r: number | undefined) => ReactNode> = {
    intro: () => <IntroScene />, ingest: (r) => <IngestScene r={r} />, ask: (r) => <AskScene r={r} />, numbers: (r) => <NumbersScene r={r} />,
    model: (r) => <ModelScene r={r} />, report: (r) => <ReportScene r={r} />, compare: () => <CompareScene />,
  };
  return (
    <VideoPlayer ref={ref} chapters={CHAPTERS} label={t("about.player.label")} chapterTitle={(id) => t(`about.ch.${id}`)}
      caption={(id, bi) => t(`about.cap.${id}.${bi}`)} renderScene={(id, r) => scenes[id](r)} />
  );
});
