// Trang chủ workspace: ô hỏi là trung tâm; bên dưới là bằng chứng sống cho thấy platform làm gì với dữ liệu —
// dải truy vết của câu trả lời gần nhất, các lớp bảo đảm độ tin cậy (đo từ dữ liệu thật) và kho tri thức theo loại nguồn.
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowUp, Network, Upload, Workflow } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ProvenanceTrail } from "@/components/Provenance";
import { Loading, SourceIcon, StatusBadge } from "@/components/ui";
import { useI18n } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtMs, fmtNumber, fmtPct, fmtRelative, sourceLabel, statusLabel, truncate } from "@/lib/format";
import { useWorkspace } from "@/state/auth";

export function HomePage() {
  const { ws, wsId, can } = useWorkspace();
  const { t } = useI18n();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const ins = useQuery({ queryKey: ["insights", wsId], queryFn: () => api.insights(wsId, 30), refetchInterval: 60_000 });
  const docs = useQuery({ queryKey: ["documents", wsId, "recent"], queryFn: () => api.documents(wsId, { page_size: 6 }) });
  const jobs = useQuery({ queryKey: ["jobs", wsId, "active"], queryFn: () => api.jobs(wsId, { limit: 20 }), refetchInterval: 10_000 });
  const lastId = ins.data?.answers.recent.find((r) => r.status === "answered")?.trace_id || ins.data?.answers.recent[0]?.trace_id;
  const last = useQuery({ queryKey: ["answer", wsId, lastId], queryFn: () => api.answer(wsId, lastId!), enabled: !!lastId, staleTime: 300_000 });
  const active = (jobs.data || []).filter((j) => j.status === "queued" || j.status === "processing");
  const suggestions = useMemo(() => [...new Set([...(docs.data?.items.flatMap((d) => d.semantic_profile?.suggested_questions || []) || []), ...ws.suggested_questions])].slice(0, 4), [docs.data, ws.suggested_questions]);
  const ask = (question: string) => nav(`/w/${wsId}/chat?q=${encodeURIComponent(question)}`);
  const k = ins.data?.knowledge;
  const a = ins.data?.answers;
  const tr = ins.data?.trust;

  return (
    <Page wide>
      {/* ô hỏi */}
      <section className="max-w-4xl pb-10 pt-4">
        <p className="mb-3 text-sm text-ink-600">{ws.name}</p>
        <h1 className="font-display text-[40px] font-semibold leading-[1.08] tracking-tight text-ink-900 sm:text-[52px]">{t("home.hero")}</h1>
        {k && <p className="mt-4 max-w-2xl text-[17px] leading-7 text-ink-600">{t("home.knows", { files: fmtNumber(k.documents), tables: fmtNumber(k.tables), things: fmtNumber(k.entities) })}</p>}
        <form className="mt-7 flex items-end gap-2 rounded-2xl bg-white p-2 pl-4 shadow-lift ring-1 ring-ink-100 focus-within:ring-ink-300" onSubmit={(e) => { e.preventDefault(); if (q.trim()) ask(q.trim()); }}>
          <textarea rows={1} className="min-h-[52px] flex-1 resize-none bg-transparent py-3.5 text-[17px] outline-none placeholder:text-ink-400" placeholder={t("home.askPh")} value={q}
            aria-label={t("home.askPh")} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (q.trim()) ask(q.trim()); } }} />
          <button className="btn-primary h-12 w-12 p-0" disabled={!q.trim()} aria-label={t("action.ask")}><ArrowUp className="h-5 w-5" /></button>
        </form>
        {suggestions.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {suggestions.map((s) => <button key={s} className="rounded-full bg-white px-3.5 py-1.5 text-left text-sm text-ink-700 ring-1 ring-ink-100 transition hover:ring-ink-300" onClick={() => ask(s)}>{truncate(s, 80)}</button>)}
          </div>
        )}
      </section>

      {ins.isLoading && <Loading />}
      {ins.data && (
        <div className="grid gap-8 lg:grid-cols-[1.75fr_1fr]">
          <div className="min-w-0 space-y-8">
            {/* câu trả lời gần nhất */}
            <section>
              <div className="mb-3 flex items-end justify-between gap-3">
                <h2 className="font-display text-xl font-semibold">{t("home.lastAnswer")}</h2>
                <Link to="chat" className="link text-sm">{t("home.openAsk")}</Link>
              </div>
              {last.data ? (
                <div className="panel p-5">
                  <div className="mb-1 flex items-center gap-2 text-xs text-ink-600"><StatusBadge status={last.data.status} /><span>{fmtRelative(ins.data.answers.recent.find((r) => r.trace_id === lastId)?.created_at)}</span></div>
                  <p className="font-display text-lg font-semibold leading-snug text-ink-900">“{ins.data.answers.recent.find((r) => r.trace_id === lastId)?.question}”</p>
                  <p className="mt-2 line-clamp-3 text-[15px] leading-7 text-ink-700">{plain(last.data.answer)}</p>
                  <ProvenanceTrail className="mt-4" trace={last.data.decision_trace} status={last.data.status} confidence={last.data.confidence} citations={last.data.citations.length} animate />
                </div>
              ) : (
                <div className="panel p-6 text-[15px] leading-7 text-ink-600">{lastId ? <Loading /> : t("home.noAnswers")}</div>
              )}
            </section>

            {/* độ tin cậy */}
            <section>
              <div className="mb-3 flex items-end justify-between gap-3">
                <div>
                  <h2 className="font-display text-xl font-semibold">{t("home.trustTitle")}</h2>
                  <p className="mt-0.5 text-sm text-ink-600">{t("home.trustHint", { days: ins.data.days })}</p>
                </div>
                {can("editor") && <Link to="trust" className="link text-sm">{t("nav.trust")}</Link>}
              </div>
              <ul className="panel divide-y divide-ink-100">
                <TrustRow value={fmtPct(a?.grounded_rate ?? null)} title={t("home.t.grounded")} hint={t("home.t.groundedHint", { n: a?.by_status.answered ?? 0 })} good={(a?.grounded_rate ?? 0) >= 0.8} />
                <TrustRow value={fmtNumber(a?.by_status.abstained ?? 0)} title={t("home.t.abstained")} hint={t("home.t.abstainedHint")} />
                <TrustRow value={fmtNumber(a?.skills.tables ?? 0)} title={t("home.t.computed")} hint={t("home.t.computedHint")} />
                <TrustRow value={fmtNumber(a?.multi_skill ?? 0)} title={t("home.t.multi")} hint={t("home.t.multiHint")} />
                <TrustRow value={fmtNumber((tr?.reviewed ?? 0) + (tr?.pending_reviews ?? 0))} title={t("home.t.review")} hint={tr?.pending_reviews ? t("home.t.reviewPending", { n: tr.pending_reviews }) : t("home.t.reviewHint")} />
                <TrustRow value={fmtNumber((tr?.open_conflicts ?? 0) + (tr?.resolved_conflicts ?? 0))} title={t("home.t.conflicts")} hint={t("home.t.conflictsHint")} />
                <TrustRow value={fmtMs(a?.latency_p50_ms ?? null)} title={t("home.t.speed")} hint={a?.latency_p95_ms ? t("home.t.speedHint", { p95: fmtMs(a.latency_p95_ms) }) : t("home.t.speedNone")} />
              </ul>
            </section>
          </div>

          <aside className="space-y-8">
            <section>
              <div className="mb-3 flex items-end justify-between gap-3">
                <h2 className="font-display text-xl font-semibold">{t("home.knowledge")}</h2>
                {can("editor") && <Link to="documents?upload=1" className="btn-secondary btn-sm"><Upload className="h-3.5 w-3.5" />{t("action.upload")}</Link>}
              </div>
              <div className="panel p-5">
                {k && k.documents === 0 ? <p className="text-sm leading-6 text-ink-600">{t("home.noFiles")}</p> : (
                  <ul className="space-y-2.5">
                    {Object.entries(k?.by_source_type || {}).sort((x, y) => y[1] - x[1]).map(([type, n]) => (
                      <li key={type} className="flex items-center gap-3 text-sm">
                        <SourceIcon type={type} size="sm" /><span className="flex-1 text-ink-800">{sourceLabel(type)}</span>
                        <span className="font-display text-base font-semibold tabular-nums">{fmtNumber(n)}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-ink-100 pt-4 text-sm">
                  <Fact label={t("home.k.passages")} value={fmtNumber(k?.records ?? 0)} />
                  <Fact label={t("home.k.tables")} value={t("home.k.tablesValue", { n: fmtNumber(k?.tables ?? 0), rows: fmtNumber(k?.table_rows ?? 0) })} to="analytics" />
                  <Fact label={t("home.k.map")} value={t("home.k.mapValue", { n: fmtNumber(k?.entities ?? 0), r: fmtNumber(k?.relations ?? 0) })} to="graph" icon={<Network className="h-3.5 w-3.5" />} />
                  <Fact label={t("home.k.models")} value={fmtNumber(k?.models_ready ?? 0)} to="models" icon={<Workflow className="h-3.5 w-3.5" />} />
                  {(k?.media_minutes ?? 0) > 0 && <Fact label={t("home.k.media")} value={t("home.k.minutes", { n: fmtNumber(k?.media_minutes ?? 0) })} />}
                  {(k?.superseded ?? 0) > 0 && <Fact label={t("home.k.versions")} value={fmtNumber(k?.superseded ?? 0)} />}
                </dl>
              </div>
            </section>

            {active.length > 0 && (
              <section className="rounded-2xl bg-ink-900 p-5 text-white">
                <h2 className="mb-3 font-display text-base font-semibold">{t("home.working", { n: active.length })}</h2>
                <ul className="space-y-2 text-sm">
                  {active.slice(0, 5).map((j) => (
                    <li key={j.job_id} className="flex items-center gap-3">
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10"><span className="block h-full rounded-full bg-marker-200" style={{ width: `${j.progress.percent ?? 12}%` }} /></span>
                      <span className="w-28 truncate text-right text-xs text-ink-300">{t(`home.job.${j.type}`) === `home.job.${j.type}` ? statusLabel(j.status) : t(`home.job.${j.type}`)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section>
              <div className="mb-3 flex items-end justify-between"><h2 className="font-display text-xl font-semibold">{t("home.recent")}</h2><Link to="documents" className="link text-sm">{t("nav.sources")}</Link></div>
              <ul className="panel divide-y divide-ink-100 overflow-hidden">
                {(docs.data?.items || []).slice(0, 5).map((d) => (
                  <li key={d.document_id}>
                    <Link to={`documents/${d.document_id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-paper">
                      <SourceIcon type={d.source_type} size="sm" />
                      <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-ink-900">{d.title}</span><span className="block text-xs text-ink-600">{fmtRelative(d.updated_at)}</span></span>
                      {d.status !== "active" && <StatusBadge status={d.status} />}
                    </Link>
                  </li>
                ))}
                {docs.data?.items.length === 0 && <li className="p-4 text-sm text-ink-600">{t("home.noFiles")}</li>}
              </ul>
            </section>
          </aside>
        </div>
      )}
    </Page>
  );
}

function TrustRow({ value, title, hint, good }: { value: string; title: string; hint: string; good?: boolean }) {
  return (
    <li className="flex items-center gap-5 px-5 py-4">
      <span className={clsx("w-20 shrink-0 font-display text-2xl font-semibold tabular-nums", good ? "text-verify-700" : "text-ink-900")}>{value}</span>
      <span className="min-w-0"><span className="block text-[15px] font-semibold text-ink-900">{title}</span><span className="block text-sm leading-6 text-ink-600">{hint}</span></span>
    </li>
  );
}

function Fact({ label, value, to, icon }: { label: string; value: string; to?: string; icon?: React.ReactNode }) {
  const body = <><dt className="flex items-center gap-1 text-xs text-ink-600">{icon}{label}</dt><dd className="mt-0.5 font-semibold text-ink-900">{value}</dd></>;
  return to ? <Link to={to} className="group block rounded-lg hover:text-brand-700">{body}</Link> : <div>{body}</div>;
}


/** Bỏ ký hiệu markdown/trích dẫn để xem trước gọn. */
function plain(md: string): string {
  return md.replace(/\[\d+\]/g, "").replace(/\|/g, " ").replace(/^[#>\-*\s]+/gm, "").replace(/[*_`#]+/g, "").replace(/-{3,}/g, " ")
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "").replace(/\s+/g, " ").trim();
}
