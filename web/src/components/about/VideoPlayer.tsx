// Bộ phát "video" dựng bằng HTML trên sân khấu 1280x720 (scale theo bề rộng): chương -> bước (beat) có thời lượng.
// Thời gian chạy trong ref (không render lại mỗi frame); chỉ render khi đổi bước. Trong một cảnh, <At s e> hiện từ bước s
// tới trước bước e. Dùng cho video tổng quan (Tour) và video đường đi của dữ liệu (Kiến trúc).
import clsx from "clsx";
import { createContext, forwardRef, useCallback, useContext, useEffect, useImperativeHandle, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { fmtNumber } from "@/lib/format";
import "./tour.css";

export type Beat = { d: number; r?: number };
export type Chapter = { id: string; beats: Beat[] };
export type VideoHandle = { seekTo: (ci: number, bi: number) => void };

export const StepCtx = createContext(-1);

export function At({ s, e, className, style, children }: { s: number; e?: number; className?: string; style?: CSSProperties; children?: ReactNode }) {
  const step = useContext(StepCtx);
  const on = step >= s && step < (e ?? Infinity);
  return <div className={clsx("ti", on && "on", className)} style={style} aria-hidden={!on}>{children}</div>;
}

function Scene({ id, active, step, children }: { id: string; active: boolean; step: number; children: ReactNode }) {
  return (
    <section className={clsx("scene", active && "active")} aria-hidden={!active} data-scene={id}>
      <StepCtx.Provider value={active ? step : -1}>{children}</StepCtx.Provider>
    </section>
  );
}

const fmtClock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export const VideoPlayer = forwardRef<VideoHandle, {
  chapters: Chapter[];
  label: string;
  chapterTitle: (id: string) => string;
  caption: (id: string, beat: number) => ReactNode;
  /** r: bước đang sáng trên thanh quy trình của chương (undefined khi chương không chạy) */
  renderScene: (id: string, r: number | undefined) => ReactNode;
  autoPlay?: boolean;
}>(function VideoPlayer({ chapters, label, chapterTitle, caption, renderScene, autoPlay = true }, ref) {
  const { t } = useI18n();
  const { flat, starts, total } = useMemo(() => {
    const flat: (Beat & { ci: number; bi: number; start: number })[] = [];
    const starts: number[] = [];
    let total = 0;
    chapters.forEach((c, ci) => {
      starts.push(total);
      c.beats.forEach((b, bi) => { flat.push({ ...b, ci, bi, start: total }); total += b.d; });
    });
    return { flat, starts, total };
  }, [chapters]);
  const chapterDur = useCallback((ci: number) => chapters[ci].beats.reduce((s, b) => s + b.d, 0), [chapters]);
  const idxAt = useCallback((x: number) => { for (let k = 0; k < flat.length; k++) if (x < flat[k].start + flat[k].d) return k; return flat.length - 1; }, [flat]);

  const screenRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const segRefs = useRef<(HTMLElement | null)[]>([]);
  const timeRef = useRef<HTMLSpanElement>(null);
  const tRef = useRef(0);
  const playingRef = useRef(false);
  const speedRef = useRef(1);
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const reduce = typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  const paint = useCallback(() => {
    const x = tRef.current;
    const i = idxAt(x);
    setIdx((p) => (p === i ? p : i));
    chapters.forEach((_, ci) => {
      const p = Math.max(0, Math.min(1, (x - starts[ci]) / chapterDur(ci)));
      const bar = segRefs.current[ci]?.querySelector<HTMLElement>(".bar i");
      if (bar) bar.style.width = `${p * 100}%`;
    });
    if (timeRef.current) timeRef.current.textContent = `${fmtClock(x)} / ${fmtClock(total)}`;
  }, [chapters, chapterDur, idxAt, starts, total]);

  const seek = useCallback((x: number) => { tRef.current = Math.max(0, Math.min(total - 0.001, x)); paint(); }, [paint, total]);
  const setPlay = useCallback((on: boolean) => {
    if (on && tRef.current >= total - 0.01) tRef.current = 0;
    playingRef.current = on; setPlaying(on);
  }, [total]);

  useImperativeHandle(ref, () => ({ seekTo: (ci, bi) => { const k = flat.findIndex((f) => f.ci === ci && f.bi === bi); seek(flat[k].start + 0.01); setPlay(true); } }), [flat, seek, setPlay]);

  useEffect(() => {
    const el = screenRef.current;
    if (!el) return;
    const fit = () => { if (stageRef.current) stageRef.current.style.transform = `scale(${el.clientWidth / 1280})`; };
    const ro = new ResizeObserver(fit);
    ro.observe(el); fit();
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      const dt = (now - last) / 1000; last = now;
      if (playingRef.current) {
        tRef.current += dt * speedRef.current;
        if (tRef.current >= total) { tRef.current = total - 0.001; playingRef.current = false; setPlaying(false); }
        paint();
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    paint();
    const auto = reduce || !autoPlay ? 0 : window.setTimeout(() => { if (!playingRef.current && tRef.current === 0) setPlay(true); }, 1200);
    return () => { cancelAnimationFrame(raf); window.clearTimeout(auto); };
  }, [paint, reduce, setPlay, total, autoPlay]);

  const b = flat[idx];
  const ch = chapters[b.ci];
  const next = () => { const i = idxAt(tRef.current); if (i < flat.length - 1) seek(flat[i + 1].start + 0.01); };
  const prev = () => { const i = idxAt(tRef.current); const tgt = tRef.current - flat[i].start > 1.5 || i === 0 ? flat[i].start : flat[i - 1].start; seek(tgt + 0.01); };
  const cycleSpeed = () => { const sp = [1, 1.5, 0.75]; const v = sp[(sp.indexOf(speedRef.current) + 1) % sp.length]; speedRef.current = v; setSpeed(v); };
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).closest("button") && (e.key === " " || e.key === "Enter")) return;
    if (e.key === " ") { e.preventDefault(); setPlay(!playingRef.current); }
    else if (e.key === "ArrowRight") { e.preventDefault(); next(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); prev(); }
  };

  return (
    <section className="tour" tabIndex={0} aria-label={label} onKeyDown={onKey}>
      <div className="screen" ref={screenRef}>
        <div className="stage" ref={stageRef}>
          {chapters.map((c) => (
            <Scene key={c.id} id={c.id} active={ch.id === c.id} step={ch.id === c.id ? b.bi : -1}>{renderScene(c.id, ch.id === c.id ? b.r : undefined)}</Scene>
          ))}
        </div>
      </div>
      <div className="caption" aria-live="polite">
        <span className="who">{b.ci + 1}. {chapterTitle(ch.id)}</span>{caption(ch.id, b.bi)}
      </div>
      <div className="timeline">
        {chapters.map((c, ci) => (
          <button key={c.id} type="button" ref={(el) => { segRefs.current[ci] = el; }} className={clsx("seg", ci === b.ci && "now")}
            style={{ "--w": chapterDur(ci) } as CSSProperties} aria-label={t("about.player.chapter", { n: ci + 1, title: chapterTitle(c.id) })}
            onClick={(e) => { const rect = e.currentTarget.getBoundingClientRect(); const f = e.detail ? Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) : 0; seek(starts[ci] + f * chapterDur(ci)); }}>
            <span className="bar"><i /></span><span className="lbl">{chapterTitle(c.id)}</span>
          </button>
        ))}
      </div>
      <div className="ctrl">
        <button type="button" className="tbtn" onClick={prev} aria-label={t("about.player.prev")}><svg viewBox="0 0 16 16" fill="currentColor"><path d="M3 2h2v12H3zM14 2v12L6 8z" /></svg></button>
        <button type="button" className="tbtn play" onClick={() => setPlay(!playingRef.current)}>
          <svg viewBox="0 0 16 16" fill="currentColor">{playing ? <path d="M3 2h4v12H3zM9 2h4v12H9z" /> : <path d="M4 2l10 6-10 6z" />}</svg>
          {t(playing ? "about.player.pause" : "about.player.play")}
        </button>
        <button type="button" className="tbtn" onClick={next} aria-label={t("about.player.next")}><svg viewBox="0 0 16 16" fill="currentColor"><path d="M11 2h2v12h-2zM2 2v12l8-6z" /></svg></button>
        <button type="button" className="tbtn" onClick={cycleSpeed} aria-label={t("about.player.speed")}>{fmtNumber(speed)}×</button>
        <span className="time" ref={timeRef} />
      </div>
    </section>
  );
});
