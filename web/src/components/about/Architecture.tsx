// Tab "Kiến trúc" của trang Giới thiệu:
//  1. Sơ đồ toàn platform trên khung 1200x880 (scale theo bề rộng, kéo ngang trên màn hình hẹp): hộp HTML + mũi tên SVG.
//     Tím = đường đi của file tải lên, xanh = đường đi của câu hỏi, nét đứt = dịch vụ bên ngoài.
//  2. Video "đi theo một mẩu dữ liệu": Điều 4 của chính sách bảo hành từ lúc tải lên tới lúc trả lời "Tivi bảo hành bao lâu?".
//     Số liệu (record, chunk, 50/42 ứng viên, điểm rerank) lấy từ workspace mẫu GreenMart và một lần tìm kiếm chạy thật.
import clsx from "clsx";
import { useEffect, useMemo, useRef, type CSSProperties, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { At, VideoPlayer, type Chapter } from "./VideoPlayer";
import { rich } from "./Tour";
import "./arch.css";

// ------------------------------------------------------------------ sơ đồ
const W = 1200;
const H = 880;

function Canvas({ children }: { children: ReactNode }) {
  const wrap = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const fit = () => { if (inner.current) inner.current.style.transform = `scale(${el.clientWidth / W})`; };
    const ro = new ResizeObserver(fit);
    ro.observe(el); fit();
    return () => ro.disconnect();
  }, []);
  return (
    <div className="overflow-x-auto">
      <div ref={wrap} className="relative min-w-[880px]" style={{ aspectRatio: `${W} / ${H}` }}>
        <div ref={inner} className="arch" style={{ width: W, height: H }}>{children}</div>
      </div>
    </div>
  );
}

function Box({ x, y, w, h, title, desc, ext, className, children }: {
  x: number; y: number; w: number; h: number; title: string; desc?: string; ext?: boolean; className?: string; children?: ReactNode;
}) {
  return (
    <div className={clsx("node", ext && "ext", className)} style={{ left: x, top: y, width: w, height: h }}>
      <b>{title}</b>
      {desc && <span className="d">{desc}</span>}
      {children}
    </div>
  );
}

function Label({ x, y, tone, children }: { x: number; y: number; tone: "in" | "q" | "ext"; children: ReactNode }) {
  return <span className={clsx("elabel", tone)} style={{ left: x, top: y }}>{children}</span>;
}

function Diagram() {
  const { t } = useI18n();
  // [path, loại] — tím: nạp file, xanh: câu hỏi, xám đứt: bên ngoài
  const edges: [string, "in" | "q" | "ext"][] = [
    ["M185 116 V164", "in"], ["M235 116 V164", "q"], ["M475 116 V164", "ext"],
    ["M520 164 V140 H700 V116", "q"], ["M760 164 V116", "in"], ["M1040 164 V116", "ext"],
    ["M185 510 V584", "in"], ["M235 510 V584", "q"],
    ["M700 510 V584", "in"], ["M855 510 V584", "in"], ["M1075 510 V584", "in"],
    ["M470 510 V540 H1010 V584", "q"], ["M380 510 V560 H810 V584", "q"],
  ];
  return (
    <Canvas>
      <svg className="edges" viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden>
        <defs>
          {(["in", "q", "ext"] as const).map((k) => (
            <marker key={k} id={`arr-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" className={`m-${k}`} />
            </marker>
          ))}
        </defs>
        {edges.map(([d, k], i) => <path key={i} d={d} className={`e-${k}`} markerEnd={`url(#arr-${k})`} />)}
      </svg>

      <Box x={40} y={16} w={330} h={100} title={t("arch.n.user")} desc={t("arch.n.user.d")} />
      <Box x={390} y={16} w={170} h={100} title={t("arch.n.ext")} desc={t("arch.n.ext.d")} />
      <Box x={640} y={16} w={250} h={100} title={t("arch.n.claude")} desc={t("arch.n.claude.d")} ext />
      <Box x={910} y={16} w={250} h={100} title={t("arch.n.obs")} desc={t("arch.n.obs.d")} ext />

      <Box x={40} y={164} w={520} h={346} title={t("arch.n.api")} className="group">
        <ol className="rows">{[1, 2, 3, 4, 5, 6, 7].map((k) => <li key={k}>{t(`arch.n.api.${k}`)}</li>)}</ol>
      </Box>
      <Box x={640} y={164} w={520} h={346} title={t("arch.n.worker")} className="group">
        <ol className="rows">{[1, 2, 3, 4, 5, 6].map((k) => {
          const [job, what] = t(`arch.n.worker.${k}`).split(" — ");
          return <li key={k}>{what ? <><code>{job}</code> — {what}</> : job}</li>;
        })}</ol>
      </Box>

      <Box x={40} y={584} w={700} h={280} title={t("arch.n.pg")} className="group pg">
        <div className="pg-grid">
          {["kb", "vec", "graph", "data", "ops"].map((k) => (
            <div key={k} className={clsx("pg-cell", k === "ops" && "wide")}><b>{t(`arch.n.pg.${k}`)}</b><span>{t(`arch.n.pg.${k}.d`)}</span></div>
          ))}
        </div>
      </Box>
      <Box x={760} y={584} w={190} h={280} title={t("arch.n.files")} desc={t("arch.n.files.d")} className="group" />
      <Box x={970} y={584} w={190} h={280} title={t("arch.n.models")} desc={t("arch.n.models.d")} className="group" />

      <Label x={248} y={130} tone="q">{t("arch.e.http")}</Label>
      <Label x={712} y={124} tone="q">{t("arch.e.llm")}</Label>
      <Label x={1052} y={130} tone="ext">{t("arch.e.trace")}</Label>
      <Label x={52} y={540} tone="in">{t("arch.e.api_pg")}</Label>
      <Label x={520} y={530} tone="q">{t("arch.e.api_models")}</Label>
      <Label x={470} y={550} tone="q">{t("arch.e.api_files")}</Label>
      <Label x={612} y={514} tone="in">{t("arch.e.worker_pg")}</Label>
      <Label x={830} y={556} tone="in">{t("arch.e.worker_files")}</Label>
      <Label x={995} y={514} tone="in">{t("arch.e.worker_models")}</Label>
    </Canvas>
  );
}

// ------------------------------------------------------------------ video
const CHAPTERS: Chapter[] = [
  { id: "file", beats: [{ d: 9, r: 0 }, { d: 9, r: 1 }, { d: 9, r: 2 }, { d: 11, r: 3 }, { d: 8, r: 3 }, { d: 11, r: 4 }, { d: 9, r: 5 }, { d: 10, r: 6 }, { d: 8, r: 7 }] },
  { id: "ask", beats: [{ d: 10, r: 0 }, { d: 9, r: 1 }, { d: 9, r: 2 }, { d: 11, r: 3 }, { d: 8, r: 4 }, { d: 11, r: 5 }, { d: 9, r: 6 }] },
  { id: "answer", beats: [{ d: 10, r: 0 }, { d: 9, r: 1 }, { d: 9, r: 2 }, { d: 10, r: 3 }] },
];
const RAIL_N: Record<string, number> = { file: 8, ask: 7, answer: 4 };

const ART4 = [
  "Điều 4. Thời hạn bảo hành theo nhóm sản phẩm",
  "Thời hạn bảo hành được quy định riêng cho từng nhóm sản phẩm căn cứ theo chính sách của nhà sản xuất và cam kết bổ sung của hệ thống, áp dụng kể từ ngày ghi trên hóa đơn mua hàng.",
  "1. Tivi, tủ lạnh, máy giặt: 24 tháng.",
  "2. Điện thoại, máy tính bảng: 12 tháng, riêng pin và phụ kiện đi kèm là 06 tháng.",
  "3. Đồ gia dụng nhỏ (nồi cơm điện, quạt, bàn ủi...): 12 tháng.",
  "4. Công lắp đặt do kỹ thuật viên hệ thống thực hiện: 06 tháng.",
];
const PREFIX = "Chính Sách Bảo Hành Sản Phẩm | CHƯƠNG II. THỜI HẠN VÀ ĐIỀU KIỆN BẢO HÀNH › Điều 4. Thời hạn bảo hành theo nhóm sản phẩm";
const QUERY_TOKENS = ["▁que", "ry", ":", "▁Ti", "vi", "▁bảo", "▁hành", "▁bao", "▁lâu", "?"];
const BM25_TOKENS = ["điều", "thời_hạn", "bảo_hành", "nhóm", "sản_phẩm", "nhà_sản_xuất", "hóa_đơn", "tivi", "tủ_lạnh", "máy_giặt", "24", "tháng", "điện_thoại", "máy_tính_bảng", "pin", "phụ_kiện", "nồi_cơm_điện", "quạt", "lắp_đặt"];
const RERANK: [string, number][] = [
  ["arch.s.source", 0.973], ["arch.s.otherDelivery", 0.094], ["arch.s.otherCard", 0.070], ["arch.s.otherScan", 0.051],
  ["arch.s.other9", 0.046], ["arch.s.other18", 0.027], ["arch.s.other5", 0.021], ["arch.s.other10", 0.020],
];
const TOOLS = ["search_documents", "list_documents", "get_document_overview", "list_tables", "query_tables", "explore_graph", "read_source"];

function Rail({ id, r }: { id: string; r?: number }) {
  const { t } = useI18n();
  return (
    <ol className="rail">
      {Array.from({ length: RAIL_N[id] }, (_, k) => (
        <li key={k} className={clsx(r === k && "now", r !== undefined && k < r && "done")}><b>{k + 1}</b>{t(`arch.${id}.r${k}`)}</li>
      ))}
    </ol>
  );
}

function Head({ id, r }: { id: string; r?: number }) {
  const { t } = useI18n();
  return (<><div className="sh"><span className="ch">{CHAPTERS.findIndex((c) => c.id === id) + 1} / {CHAPTERS.length}</span><h3>{t(`arch.ch.${id}`)}</h3></div><Rail id={id} r={r} /></>);
}

function useBars(n: number, seed: number) {
  return useMemo(() => { let s = seed; return Array.from({ length: n }, () => { s = (s * 9301 + 49297) % 233280; return 12 + (s / 233280) * 88; }); }, [n, seed]);
}

function FileScene({ r }: { r?: number }) {
  const { t } = useI18n();
  const bars = useBars(64, 11);
  return (
    <>
      <Head id="file" r={r} />
      <div className="body">
        <At s={0} e={1} className="ps j-upload">
          <div className="card j-file"><span className="ico">DOCX</span><div><b className="mono">02_chinh_sach_bao_hanh_san_pham.docx</b><span className="small">{t("arch.s.upload")}</span></div></div>
          <div className="j-bytes"><span className="k">{t("arch.s.magic")}</span><code>50 4B 03 04 14 00 06 00 … word/document.xml</code><span className="tag b">{t("arch.s.isDocx")}</span></div>
          <span className="tag v">{t("arch.s.dedupe")}</span>
        </At>
        <At s={1} e={2} className="ps">
          <span className="k">{t("arch.s.tree")}</span>
          <ul className="j-tree">
            <li>CHÍNH SÁCH BẢO HÀNH SẢN PHẨM<ul>
              <li className="dim">CHƯƠNG I. QUY ĐỊNH CHUNG</li>
              <li>CHƯƠNG II. THỜI HẠN VÀ ĐIỀU KIỆN BẢO HÀNH<ul>
                <li className="hit">Điều 4. Thời hạn bảo hành theo nhóm sản phẩm</li>
                <li className="dim">Điều 5. Điều kiện được bảo hành</li>
                <li className="dim">Điều 6. …</li>
              </ul></li>
              <li className="dim">CHƯƠNG III. QUY TRÌNH TIẾP NHẬN VÀ XỬ LÝ</li>
              <li className="dim">…</li>
            </ul></li>
          </ul>
        </At>
        <At s={2} e={3} className="ps">
          <div className="card j-record">
            <div className="j-rhead"><span className="tag b">{t("arch.s.record")}</span><code className="j-id">53cb…:CHÍNH SÁCH BẢO HÀNH SẢN PHẨM &gt; CHƯƠNG II. … &gt; Điều 4. Thời hạn bảo hành theo nhóm sản phẩm</code></div>
            <span className="small">{t("arch.s.kind")}</span>
            <div className="j-text">{ART4.map((l, i) => <p key={i} className={clsx(i === 0 && "b", i === 2 && "mk-line")}>{l}</p>)}</div>
          </div>
        </At>
        <At s={3} e={4} className="ps j-chunks">
          <div className="parent">
            <span className="k">{t("arch.s.parent")} · <span className="mono">#p0000</span></span>
            <div className="j-child"><span className="k">{t("arch.s.child")} · <span className="mono">#p0000#c0000</span></span><div className="j-text small">{ART4.slice(0, 3).join(" ")} …</div></div>
          </div>
          <div className="j-longer">
            <span className="k">{t("arch.s.longer")}</span>
            <div className="j-strip">{[0, 1, 2].map((k) => <i key={k} style={{ left: `${k * 30}%`, top: k % 2 ? 26 : 6 } as CSSProperties}>c000{k}</i>)}</div>
            <span className="note">{t("arch.s.overlap")}</span>
          </div>
        </At>
        <At s={4} e={5} className="ps">
          <span className="k">{t("arch.s.prefix")}</span>
          <div className="card j-embedtext"><span className="j-pre">passage: </span><span className="j-ctx">{PREFIX}</span>{"\n"}{ART4.slice(0, 3).join("\n")}{"\n"}…</div>
        </At>
        <At s={5} e={6} className="ps j-embed">
          <div className="j-pipe">
            <div className="card j-in mono small">passage: Chính Sách Bảo Hành… | … Điều 4 …</div>
            <div className="arrow run"><i /></div>
            <div className="card j-model"><b>multilingual-e5-base</b><span className="small">{t("arch.s.model")}</span></div>
            <div className="arrow run"><i /></div>
            <div className="card j-out"><div className="vec">{bars.map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}</div><span className="small">{t("arch.s.vector")} · {t("arch.s.len1")}</span></div>
          </div>
          <div className="j-notes"><span className="tag v">{t("arch.s.saved")}</span><span className="tag">{t("arch.s.parentWaits")}</span></div>
        </At>
        <At s={6} e={7} className="ps">
          <span className="k">{t("arch.s.tokens")}</span>
          <div className="j-toks">{BM25_TOKENS.map((w) => <span key={w} className={clsx("tok", ["tivi", "bảo_hành", "24", "tháng"].includes(w) && "sku")}>{w}</span>)}</div>
          <span className="note">{t("arch.s.ram")}</span>
        </At>
        <At s={7} e={8} className="ps j-excel">
          <span className="k">{t("arch.s.row")} · <span className="mono">01_bang_gia_sku.xlsx › BangGiaSKU!A3</span></span>
          <table className="sheet"><thead><tr><th>sku</th><th>ten_san_pham</th><th>gia</th><th>category</th></tr></thead>
            <tbody><tr><td>ĐT-018</td><td>Loa bluetooth di động 30W - NovaPhone</td><td>1440000</td><td>Điện tử</td></tr></tbody></table>
          <div className="j-two">
            <div className="card"><b>{t("arch.s.asRecord")}</b><code className="small">sku là ĐT-018, ten_san_pham là Loa bluetooth di động 30W - NovaPhone, gia là 1440000, category là Điện tử</code></div>
            <div className="card"><b>{t("arch.s.asTable")}</b><code className="small">bang_gia_thang_8_banggiasku</code><span className="small">{t("arch.s.tableInfo")}</span></div>
          </div>
        </At>
        <At s={8} className="ps">
          <div className="counts">
            {[["25", "arch.s.files"], ["641", "arch.s.records"], ["1.046", "arch.s.chunks"], ["530", "arch.s.vectors"]].map(([n, k], i) => (
              <div key={k} className="c-wrap">{i > 0 && <span className="arrow" />}<div className="c"><b className="big">{n}</b><span>{t(k)}</span>{k === "arch.s.chunks" && <span className="note">{t("arch.s.chunksSplit")}</span>}</div></div>
            ))}
          </div>
        </At>
      </div>
    </>
  );
}

function AskScene({ r }: { r?: number }) {
  const { t } = useI18n();
  const bars = useBars(64, 29);
  return (
    <>
      <Head id="ask" r={r} />
      <div className="body">
        <At s={0} e={1} className="ps j-prep">
          <div className="bubble">{t("arch.s.q")}</div>
          <div className="j-chips">{[1, 2, 3].map((k) => <span key={k} className="tag">{t(`arch.s.prep${k}`)}</span>)}</div>
        </At>
        <At s={1} e={2} className="ps">
          <span className="k">{t("arch.s.tools")}</span>
          <div className="j-tools">{TOOLS.map((n) => <span key={n} className={clsx("tok", n === "search_documents" && "pick")}>{n}</span>)}</div>
          <div className="code">{"{ "}<span className="kw">"type"</span>: <span className="s">"tool_use"</span>, <span className="kw">"name"</span>: <span className="s">"search_documents"</span>,{"\n  "}<span className="kw">"input"</span>: {"{ "}<span className="kw">"query"</span>: <span className="s">"Tivi bảo hành bao lâu"</span>, <span className="kw">"top_k"</span>: 6 {"} }"}</div>
          <span className="note">{t("arch.s.writes")} ({t("arch.s.example")})</span>
        </At>
        <At s={2} e={3} className="ps">
          <span className="k">{t("arch.s.pieces")}</span>
          <div className="j-toks">{QUERY_TOKENS.map((w, i) => <span key={i} className={clsx("tok", i < 3 && "pre")}>{w}</span>)}</div>
          <div className="card j-out wide"><div className="vec">{bars.map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}</div>
            <span className="small mono">[-0.001, 0.045, -0.017, 0.039, 0.022, …] · {t("arch.s.vector")}</span></div>
        </At>
        <At s={3} e={4} className="ps j-search">
          <div className="card">
            <b>{t("arch.s.hnsw")}</b><span className="note">{t("arch.s.layers")}</span>
            <svg viewBox="0 0 400 190" className="j-hnsw" aria-hidden>
              {[30, 95, 160].map((y, li) => (
                <g key={y}>
                  <line x1="10" y1={y} x2="390" y2={y} className="lay" />
                  {Array.from({ length: [4, 9, 18][li] }, (_, k) => <circle key={k} cx={20 + k * (360 / [3, 8, 17][li])} cy={y} r={li === 2 ? 4 : 5} className="dot" />)}
                </g>
              ))}
              <path d="M20 30 L260 30 L260 95 L305 95 L305 160 L326 160" className="walk" />
              <circle cx="326" cy="160" r="8" className="goal" />
            </svg>
            <span className="tag b">{t("arch.s.cand50")}</span>
          </div>
          <div className="card">
            <b>{t("arch.s.bm25")}</b>
            <div className="j-toks">{["tivi", "bảo_hành", "bao_lâu"].map((w) => <span key={w} className="tok sku">{w}</span>)}</div>
            <span className="tag b">{t("arch.s.cand42")}</span>
          </div>
          <span className="tag v j-art4">{t("arch.s.art4")}</span>
        </At>
        <At s={4} e={5} className="ps j-rrf">
          <div className="j-lists">
            <div className="card">{[1, 2, 3, 4, 5].map((k) => <i key={k} className={clsx(k === 1 && "hit")} />)}<span className="note">HNSW · 50</span></div>
            <div className="card">{[1, 2, 3, 4, 5].map((k) => <i key={k} className={clsx(k === 1 && "hit")} />)}<span className="note">BM25 · 42</span></div>
            <div className="arrow run"><i /></div>
            <div className="card j-formula"><b>{t("arch.s.rrf")}</b><code>score = Σ 1 / (60 + rank)</code><span className="tag b">{t("arch.s.keep40")}</span></div>
          </div>
        </At>
        <At s={5} e={6} className="ps">
          <span className="k">{t("arch.s.rerank")}</span>
          <div className="list j-rr">
            {RERANK.map(([k, v], i) => (
              <div key={k} className={clsx("li", i === 0 && "top")}><span className="r">{i + 1}</span><span className="t">{t(k)}</span>
                <span className="barw"><i style={{ "--w": `${v * 100}%` } as CSSProperties} /></span><span className="score">{v.toFixed(3)}</span></div>
            ))}
          </div>
          <span className="note j-thr"><i />{t("arch.s.threshold")}</span>
        </At>
        <At s={6} className="ps">
          <div className="j-pipe">
            <div className="card j-in"><span className="k">#p0000#c0000</span><span className="small">{t("arch.s.child")}</span></div>
            <div className="arrow run"><i /></div>
            <div className="card j-in"><span className="k">#p0000</span><span className="small">{t("arch.s.parentSwap")}</span></div>
            <div className="arrow run"><i /></div>
            <div className="card j-result">
              <span className="k">{t("arch.s.toolResult")}</span>
              <code className="small">[S1] Chính Sách Bảo Hành Sản Phẩm, Điều 4{"\n"}&lt;untrusted_document&gt; Điều 4. Thời hạn bảo hành … 1. Tivi, tủ lạnh, máy giặt: 24 tháng. … &lt;/untrusted_document&gt;{"\n"}[S2] … [S6]</code>
            </div>
          </div>
        </At>
      </div>
    </>
  );
}

function AnswerScene({ r }: { r?: number }) {
  const { t } = useI18n();
  return (
    <>
      <Head id="answer" r={r} />
      <div className="body">
        <At s={0} e={1} className="ps">
          <span className="k">{t("arch.s.turn2")}</span>
          <div className="card j-answer">{rich(t("arch.s.answer"))}</div>
          <span className="tag">{t("arch.s.gate")}</span>
        </At>
        <At s={1} e={2} className="ps j-checks">
          <div className="card j-answer">{rich(t("arch.s.answer"))}</div>
          {[1, 2, 3].map((k) => <div key={k} className="j-check"><span className="ok">✓</span>{t(`arch.s.check${k}`)}</div>)}
        </At>
        <At s={2} e={3} className="ps j-conf">
          <span className="k">{t("arch.s.conf")}</span>
          <div className="code">0,973 × (0,5 + 0,5 × 1,0) = <span className="kw">0,973</span></div>
          <div className="j-meter"><i style={{ "--w": "97.3%" } as CSSProperties} /></div>
          <div className="j-chips"><span className="tag v">{t("arch.s.high")}</span><span className="tag">answered</span><span className="tag">{t("arch.s.saved2")}</span></div>
        </At>
        <At s={3} className="ps j-final">
          <div className="bubble small-q">{t("arch.s.q")}</div>
          <div className="card j-answer">{rich(t("arch.s.final"))} <span className="cite">1</span>.</div>
          <div className="card j-src"><span className="k">[1] {t("arch.s.source")}</span><p>{ART4[1]}</p><p><span className="mk">{ART4[2]}</span></p></div>
          <span className="note">{t("arch.s.found")}</span>
        </At>
      </div>
    </>
  );
}

function Journey() {
  const { t } = useI18n();
  const scenes: Record<string, (r: number | undefined) => ReactNode> = {
    file: (r) => <FileScene r={r} />, ask: (r) => <AskScene r={r} />, answer: (r) => <AnswerScene r={r} />,
  };
  return (
    <VideoPlayer chapters={CHAPTERS} label={t("arch.player")} chapterTitle={(id) => t(`arch.ch.${id}`)}
      caption={(id, bi) => t(`arch.cap.${id}.${bi}`)} renderScene={(id, r) => scenes[id](r)} />
  );
}

export function Architecture() {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-12">
      <section className="flex flex-col gap-5">
        <div>
          <h2 className="font-display text-[28px] font-bold leading-tight text-ink-900">{t("arch.title")}</h2>
          <p className="mt-2 max-w-3xl text-[16px] leading-7 text-ink-600">{t("arch.lead")}</p>
        </div>
        <div className="panel p-4">
          <Diagram />
          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink-700">
            <span className="inline-flex items-center gap-2"><i className="h-0.5 w-6 bg-brand-600" />{t("arch.legend.ingest")}</span>
            <span className="inline-flex items-center gap-2"><i className="h-0.5 w-6 bg-verify-600" />{t("arch.legend.ask")}</span>
            <span className="inline-flex items-center gap-2"><i className="h-0 w-6 border-t-2 border-dashed border-ink-300" />{t("arch.legend.ext")}</span>
            <span className="text-ink-400 md:hidden">{t("arch.scroll")}</span>
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {([["in", 5, "bg-brand-600"], ["q", 4, "bg-verify-600"]] as const).map(([k, n, dot]) => (
            <div key={k} className="panel p-5">
              <h3 className="flex items-center gap-2 font-display text-[18px] font-bold text-ink-900"><i className={clsx("h-2.5 w-2.5 rounded-full", dot)} />{t(k === "in" ? "arch.legend.ingest" : "arch.legend.ask")}</h3>
              <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-[14.5px] leading-6 text-ink-700">
                {Array.from({ length: n }, (_, i) => <li key={i}>{rich(t(`arch.flow.${k}.${i + 1}`))}</li>)}
              </ol>
            </div>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-5">
        <div>
          <h2 className="font-display text-[28px] font-bold leading-tight text-ink-900">{t("arch.video.title")}</h2>
          <p className="mt-2 max-w-3xl text-[16px] leading-7 text-ink-600">{t("arch.video.lead")}</p>
        </div>
        <Journey />
      </section>
    </div>
  );
}
