// Trung tâm tin cậy (editor+): các lớp bảo đảm đang hoạt động + việc cần người xử lý — duyệt câu trả lời nhạy cảm,
// nguồn mâu thuẫn, câu hỏi thiếu tài liệu, phản hồi. Nhật ký hỏi đáp, việc nền và chi phí AI chỉ ở chế độ kỹ thuật.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  BookX, Check, ExternalLink, FileWarning, GitCompareArrows, Gauge, KeyRound, Lock, MessageSquareWarning, RefreshCw, ScrollText,
  ShieldCheck, ThumbsDown, ThumbsUp, UserCheck, X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Markdown } from "@/components/Markdown";
import { Badge, Drawer, Empty, Field, KPI, Loading, Modal, PageHeader, Segmented, StatusBadge, useToast } from "@/components/ui";
import { VegaChart } from "@/components/VegaChart";
import { useI18n } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtDate, fmtMs, fmtNumber, fmtPct, fmtRelative, fmtUsd, statusLabel, truncate } from "@/lib/format";
import type { Conflict, ReviewItem } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { usePrefs } from "@/state/prefs";

export function TrustPage() {
  const { can, wsId } = useWorkspace();
  const { t } = useI18n();
  const { advanced } = usePrefs();
  const ins = useQuery({ queryKey: ["insights", wsId], queryFn: () => api.insights(wsId, 30) });
  if (!can("editor")) return <Navigate to=".." replace />;
  const d = ins.data;
  const tabs = [
    { to: "review", label: t("trust.tab.review"), n: d?.trust.pending_reviews },
    { to: "conflicts", label: t("trust.tab.conflicts"), n: d?.trust.open_conflicts },
    { to: "gaps", label: t("trust.tab.gaps"), n: d?.answers.by_status.abstained },
    { to: "feedback", label: t("trust.tab.feedback"), n: d?.trust.feedback_down },
    ...(advanced ? [{ to: "log", label: t("trust.tab.log") }, { to: "jobs", label: t("trust.tab.jobs") }, ...(can("owner") ? [{ to: "costs", label: t("trust.tab.costs") }] : [])] : []),
  ];
  const guards: { icon: typeof ShieldCheck; title: string; hint: string; value?: string }[] = d ? [
    { icon: ScrollText, title: t("trust.g.citations"), hint: t("trust.g.citationsHint"), value: fmtPct(d.answers.grounded_rate) },
    { icon: BookX, title: t("trust.g.abstain"), hint: t("trust.g.abstainHint"), value: fmtNumber(d.answers.by_status.abstained ?? 0) },
    { icon: UserCheck, title: t("trust.g.review"), hint: t("trust.g.reviewHint"), value: fmtNumber(d.trust.reviewed + d.trust.pending_reviews) },
    { icon: GitCompareArrows, title: t("trust.g.conflicts"), hint: t("trust.g.conflictsHint"), value: fmtNumber(d.trust.open_conflicts + d.trust.resolved_conflicts) },
    { icon: FileWarning, title: t("trust.g.injection"), hint: t("trust.g.injectionHint"), value: fmtNumber(d.trust.suspicious_records) },
    { icon: Lock, title: t("trust.g.access"), hint: t("trust.g.accessHint", { n: Object.values(d.trust.members_by_role).reduce((a, b) => a + b, 0) }) },
    { icon: KeyRound, title: t("trust.g.readonly"), hint: t("trust.g.readonlyHint") },
    { icon: Gauge, title: t("trust.g.speed"), hint: t("trust.g.speedHint", { p95: fmtMs(d.answers.latency_p95_ms) }), value: fmtMs(d.answers.latency_p50_ms) },
  ] : [];
  return (
    <Page wide>
      <PageHeader title={t("nav.trust")} subtitle={t("trust.subtitle")} />
      {ins.isLoading && <Loading />}
      {d && (
        <section className="mb-8 grid gap-px overflow-hidden rounded-2xl border border-ink-100 bg-ink-100 sm:grid-cols-2 xl:grid-cols-4">
          {guards.map((g) => (
            <div key={g.title} className="flex gap-3 bg-white p-4">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-verify-50 text-verify-700"><g.icon className="h-4 w-4" /></span>
              <div className="min-w-0">
                <div className="flex items-baseline gap-2"><span className="text-sm font-semibold text-ink-900">{g.title}</span>{g.value && <span className="font-display text-base font-semibold tabular-nums text-ink-900">{g.value}</span>}</div>
                <p className="mt-0.5 text-xs leading-5 text-ink-600">{g.hint}</p>
              </div>
            </div>
          ))}
        </section>
      )}
      <nav className="mb-5 flex gap-1 overflow-x-auto border-b border-ink-100">
        {tabs.map((tb) => (
          <NavLink key={tb.to} to={tb.to} className={({ isActive }) => clsx("-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium", isActive ? "border-ink-900 text-ink-900" : "border-transparent text-ink-600 hover:text-ink-900")}>
            {tb.label}{tb.n ? <span className="rounded-full bg-marker-200 px-1.5 text-xs text-ink-900">{tb.n}</span> : null}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to="review" replace />} />
        <Route path="review" element={<ReviewTab />} />
        <Route path="conflicts" element={<ConflictsTab />} />
        <Route path="gaps" element={<GapsTab />} />
        <Route path="feedback" element={<FeedbackTab />} />
        <Route path="log" element={<LogTab />} />
        <Route path="jobs" element={<JobsTab />} />
        <Route path="costs" element={<CostsTab />} />
        <Route path="*" element={<Navigate to="review" replace />} />
      </Routes>
    </Page>
  );
}

function ReviewTab() {
  const { wsId } = useWorkspace();
  const { t } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState<"pending" | "approved" | "rejected">("pending");
  const [item, setItem] = useState<ReviewItem | null>(null);
  const [edited, setEdited] = useState("");
  const [note, setNote] = useState("");
  const list = useQuery({ queryKey: ["review", wsId, status], queryFn: () => api.review(wsId, status), refetchInterval: status === "pending" ? 10000 : false });
  const done = () => { setItem(null); void qc.invalidateQueries({ queryKey: ["review", wsId] }); void qc.invalidateQueries({ queryKey: ["insights", wsId] }); };
  const approve = useMutation({ mutationFn: () => api.approveReview(wsId, item!.id, { edited_answer: edited.trim() !== item!.draft_answer.trim() ? edited : undefined, note: note || undefined }), onSuccess: () => { toast.ok(t("trust.review.approved")); done(); }, onError: toast.err });
  const reject = useMutation({ mutationFn: () => api.rejectReview(wsId, item!.id, { note: note || undefined }), onSuccess: () => { toast.ok(t("trust.review.rejected")); done(); }, onError: toast.err });
  return (
    <div>
      <p className="mb-4 max-w-3xl text-sm leading-6 text-ink-600">{t("trust.review.intro")}</p>
      <Segmented className="mb-4 w-fit" value={status} onChange={setStatus} options={[{ key: "pending", label: statusLabel("pending") }, { key: "approved", label: statusLabel("approved") }, { key: "rejected", label: statusLabel("rejected") }]} />
      {list.isLoading && <Loading />}
      {list.data?.length === 0 && <div className="panel"><Empty icon={<UserCheck />} title={t("trust.review.empty")} hint={t("trust.review.emptyHint")} /></div>}
      <ul className="space-y-2">
        {list.data?.map((r) => (
          <li key={r.id}>
            <button className="card w-full p-4 text-left transition hover:shadow-lift" onClick={() => { setItem(r); setEdited(r.draft_answer); setNote(""); }}>
              <div className="flex items-start justify-between gap-3"><div className="font-semibold text-ink-900">{r.question}</div><StatusBadge status={r.status} /></div>
              <div className="mt-1 text-xs text-ink-600">{r.user_id} · {fmtRelative(r.created_at)}{r.overridden && <Badge tone="amber" className="ml-2">{t("trust.review.edited")}</Badge>}</div>
              <p className="mt-2 line-clamp-2 text-sm text-ink-700">{r.final_answer || r.draft_answer}</p>
            </button>
          </li>
        ))}
      </ul>
      <Drawer open={!!item} onClose={() => setItem(null)} title={t("trust.review.title")} width="max-w-2xl">
        {item && (
          <div className="space-y-5 text-sm">
            <div><div className="label">{t("trust.review.question")}</div><div className="rounded-xl bg-paper p-3 text-[15px]">{item.question}</div></div>
            <div><div className="label">{t("trust.review.draft")}</div><div className="rounded-xl border border-ink-100 p-3"><Markdown text={item.draft_answer} citations={item.draft_citations} /></div>
              {item.draft_citations?.length > 0 && <ul className="mt-2 space-y-0.5 text-xs text-ink-600">{item.draft_citations.map((c) => <li key={c.n}><span className="cite-chip">{c.n}</span> {c.label}</li>)}</ul>}</div>
            <p className="rounded-xl bg-marker-100 px-3 py-2 text-ink-800">{t("trust.review.why")}: {item.reason}</p>
            {item.status === "pending" ? (
              <>
                <Field label={t("trust.review.final")} hint={t("trust.review.finalHint")}><textarea className="input" rows={8} value={edited} onChange={(e) => setEdited(e.target.value)} /></Field>
                <Field label={t("trust.review.note")}><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
                <div className="flex justify-end gap-2">
                  <button className="btn-danger" disabled={reject.isPending} onClick={() => reject.mutate()}><X className="h-4 w-4" />{t("trust.review.reject")}</button>
                  <button className="btn-primary" disabled={approve.isPending} onClick={() => approve.mutate()}><Check className="h-4 w-4" />{t("trust.review.approve")}</button>
                </div>
              </>
            ) : <div className="text-xs text-ink-600">{t("trust.review.by", { who: item.reviewer_id || "—", when: fmtDate(item.reviewed_at, true) })}{item.review_note ? ` — ${item.review_note}` : ""}</div>}
          </div>
        )}
      </Drawer>
    </div>
  );
}

function ConflictsTab() {
  const { wsId } = useWorkspace();
  const { t } = useI18n();
  const { advanced } = usePrefs();
  const toast = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [item, setItem] = useState<Conflict | null>(null);
  const list = useQuery({ queryKey: ["conflicts", wsId, status], queryFn: () => api.conflicts(wsId, status) });
  const resolve = useMutation({ mutationFn: (b: { action: string; keep_record_id?: string }) => api.resolveConflict(wsId, item!.id, b), onSuccess: () => { toast.ok(t("trust.conflicts.done")); setItem(null); void qc.invalidateQueries({ queryKey: ["conflicts", wsId] }); }, onError: toast.err });
  return (
    <div>
      <p className="mb-4 max-w-3xl text-sm leading-6 text-ink-600">{t("trust.conflicts.intro")}</p>
      <Segmented className="mb-4 w-fit" value={status} onChange={setStatus} options={[{ key: "open", label: statusLabel("open") }, { key: "resolved", label: statusLabel("resolved") }]} />
      {list.isLoading && <Loading />}
      {list.data?.length === 0 && <div className="panel"><Empty icon={<GitCompareArrows />} title={t("trust.conflicts.empty")} /></div>}
      <ul className="space-y-2">
        {list.data?.map((c) => (
          <li key={c.id}><button className="card w-full p-4 text-left hover:shadow-lift" onClick={() => setItem(c)}>
            <div className="flex items-center gap-2"><span className="font-semibold text-ink-900">{c.entity_key}</span><StatusBadge status={c.status} className="ml-auto" /></div>
            <div className="mt-1 text-xs text-ink-600">{t("trust.conflicts.sources", { n: c.record_ids.length })} · {fmtRelative(c.created_at)}</div>
          </button></li>
        ))}
      </ul>
      <Modal open={!!item} onClose={() => setItem(null)} title={item?.entity_key || ""} width="max-w-2xl">
        {item && (
          <div className="space-y-4 text-sm">
            <ul className="space-y-2">
              {Object.entries(item.details || {}).slice(0, 6).map(([k, v]) => <li key={k} className="rounded-xl bg-paper p-3"><div className="text-xs text-ink-600">{k}</div><div className="mt-0.5 text-ink-900">{typeof v === "string" ? v : JSON.stringify(v)}</div></li>)}
            </ul>
            {advanced && <pre className="max-h-60 overflow-auto rounded-xl bg-ink-950 p-3 text-xs text-ink-100">{JSON.stringify(item.details, null, 2)}</pre>}
            {item.status === "open" && (
              <div>
                <div className="label">{t("trust.conflicts.resolve")}</div>
                <div className="flex flex-wrap gap-2">
                  {item.record_ids.map((rid, i) => <button key={rid} className="btn-secondary btn-sm" onClick={() => resolve.mutate({ action: "keep_one", keep_record_id: rid })}>{t("trust.conflicts.keep", { n: i + 1 })}</button>)}
                  <button className="btn-secondary btn-sm" onClick={() => resolve.mutate({ action: "resolved" })}>{t("trust.conflicts.fixedAtSource")}</button>
                  <button className="btn-ghost btn-sm" onClick={() => resolve.mutate({ action: "dismiss" })}>{t("trust.conflicts.dismiss")}</button>
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

function GapsTab() {
  const { wsId } = useWorkspace();
  const { t } = useI18n();
  const nav = useNavigate();
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const list = useQuery({ queryKey: ["abstentions", wsId, days], queryFn: () => api.abstentions(wsId, Number(days)) });
  return (
    <div>
      <p className="mb-4 max-w-3xl text-sm leading-6 text-ink-600">{t("trust.gaps.intro")}</p>
      <Segmented className="mb-4 w-fit" value={days} onChange={setDays} options={(["7", "30", "90"] as const).map((d) => ({ key: d, label: t("trust.days", { n: d }) }))} />
      {list.isLoading && <Loading />}
      {list.data?.length === 0 && <div className="panel"><Empty icon={<BookX />} title={t("trust.gaps.empty")} hint={t("trust.gaps.emptyHint")} /></div>}
      {list.data && list.data.length > 0 && (
        <ul className="panel divide-y divide-ink-100">
          {list.data.map((a) => (
            <li key={a.trace_id} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
              <span className="min-w-0 flex-1"><span className="block text-[15px] text-ink-900">{a.question}</span><span className="block text-xs text-ink-600">{fmtRelative(a.created_at)} · {a.reason === "no_citation" ? t("trust.gaps.noCitation") : t("trust.gaps.noResults")}</span></span>
              <button className="btn-secondary btn-sm" onClick={() => nav(`/w/${wsId}/documents?upload=1`)}>{t("trust.gaps.add")}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FeedbackTab() {
  const { wsId } = useWorkspace();
  const { t } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const [rating, setRating] = useState<"down" | "up" | "all">("down");
  const list = useQuery({ queryKey: ["ops-feedback", wsId, rating], queryFn: () => api.opsFeedback(wsId, { rating: rating === "all" ? undefined : rating, days: 90 }) });
  const upd = useMutation({ mutationFn: ({ id, status }: { id: string; status: string }) => api.updateFeedback(wsId, id, { status }), onSuccess: () => void qc.invalidateQueries({ queryKey: ["ops-feedback", wsId] }), onError: toast.err });
  return (
    <div>
      <Segmented className="mb-4 w-fit" value={rating} onChange={setRating} options={[{ key: "down", label: t("trust.fb.down") }, { key: "up", label: t("trust.fb.up") }, { key: "all", label: t("trust.fb.all") }]} />
      {list.isLoading && <Loading />}
      {list.data?.length === 0 && <div className="panel"><Empty icon={<MessageSquareWarning />} title={t("trust.fb.empty")} /></div>}
      <ul className="space-y-2">
        {list.data?.map((f) => (
          <li key={String(f.id)} className="card p-4 text-sm">
            <div className="flex items-start justify-between gap-3"><div className="font-semibold text-ink-900">{String(f.question)}</div>{f.rating === 1 ? <ThumbsUp className="h-4 w-4 text-verify-600" /> : <ThumbsDown className="h-4 w-4 text-alert-700" />}</div>
            {typeof f.comment === "string" && f.comment ? <p className="mt-1 italic text-ink-700">“{f.comment}”</p> : null}
            <p className="mt-2 line-clamp-3 text-xs text-ink-600">{truncate(String(f.answer || ""), 400)}</p>
            <div className="mt-2 flex items-center gap-2 text-xs text-ink-600">{String(f.user_id)} · {fmtRelative(String(f.created_at))}<span className="flex-1" />
              {f.status === "new" ? <><button className="btn-secondary btn-sm" onClick={() => upd.mutate({ id: String(f.id), status: "acknowledged" })}>{t("trust.fb.seen")}</button><button className="btn-secondary btn-sm" onClick={() => upd.mutate({ id: String(f.id), status: "resolved" })}>{t("trust.fb.fixed")}</button></> : <StatusBadge status={String(f.status)} />}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LogTab() {
  const { wsId } = useWorkspace();
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["traces", wsId, q], queryFn: () => api.traces(wsId, { q: q || undefined, limit: 200 }) });
  const detail = useQuery({ queryKey: ["trace", wsId, open], queryFn: () => api.trace(wsId, open!), enabled: !!open });
  return (
    <div>
      <input className="input mb-3 w-80" placeholder={t("trust.log.search")} value={q} onChange={(e) => setQ(e.target.value)} />
      {list.isLoading && <Loading />}
      {list.data && (
        <div className="panel overflow-x-auto"><table className="w-full"><thead><tr><th className="th">{t("trust.log.when")}</th><th className="th">{t("trust.log.who")}</th><th className="th">{t("trust.log.question")}</th><th className="th">{t("trust.log.result")}</th><th className="th text-right">ms</th><th className="th text-right">USD</th></tr></thead>
          <tbody>{list.data.map((x) => (
            <tr key={x.trace_id} className="cursor-pointer hover:bg-paper" onClick={() => setOpen(x.trace_id)}>
              <td className="td whitespace-nowrap text-xs">{fmtDate(x.created_at, true)}</td><td className="td text-xs">{x.user_id}</td><td className="td max-w-md truncate">{x.question}</td>
              <td className="td"><StatusBadge status={x.status} /></td><td className="td text-right font-mono text-xs">{x.latency_ms ?? "—"}</td><td className="td text-right font-mono text-xs">{fmtUsd(x.cost_usd)}</td>
            </tr>
          ))}</tbody></table></div>
      )}
      <Drawer open={!!open} onClose={() => setOpen(null)} title={t("trust.log.detail")} width="max-w-3xl">
        {detail.isLoading && <Loading />}
        {detail.data && (
          <div className="space-y-3 text-sm">
            <div className="rounded-xl bg-paper p-3">{String(detail.data.question)}</div>
            <div className="rounded-xl border border-ink-100 p-3"><Markdown text={String(detail.data.answer || "")} /></div>
            {typeof detail.data.langfuse_url === "string" && detail.data.langfuse_url ? <a className="link inline-flex items-center gap-1" href={detail.data.langfuse_url} target="_blank" rel="noreferrer">Langfuse <ExternalLink className="h-3 w-3" /></a> : null}
            <pre className="max-h-[60vh] overflow-auto rounded-xl bg-ink-950 p-3 text-xs text-ink-100">{JSON.stringify(detail.data, null, 2)}</pre>
          </div>
        )}
      </Drawer>
    </div>
  );
}

function JobsTab() {
  const { wsId } = useWorkspace();
  const { t } = useI18n();
  const toast = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState("all");
  const jobs = useQuery({ queryKey: ["jobs", wsId, "ops", status], queryFn: () => api.jobs(wsId, { status: status === "all" ? undefined : status, limit: 200 }), refetchInterval: 5000 });
  const requeue = useMutation({ mutationFn: (id: string) => api.requeueJob(wsId, id), onSuccess: () => { toast.ok(t("trust.jobs.requeued")); void qc.invalidateQueries({ queryKey: ["jobs", wsId] }); }, onError: toast.err });
  return (
    <div>
      <Segmented className="mb-3 w-fit" value={status} onChange={setStatus} options={["all", "queued", "processing", "done", "blocked", "error"].map((s) => ({ key: s, label: s === "all" ? t("trust.fb.all") : statusLabel(s) }))} />
      {jobs.isLoading && <Loading />}
      {jobs.data && (
        <div className="panel overflow-x-auto"><table className="w-full"><thead><tr><th className="th">type</th><th className="th">status</th><th className="th">progress</th><th className="th">tries</th><th className="th">created</th><th className="th">result / error</th><th className="th" /></tr></thead>
          <tbody>{jobs.data.map((j) => (
            <tr key={j.job_id}>
              <td className="td font-mono text-xs">{j.type}</td><td className="td"><StatusBadge status={j.status} /></td>
              <td className="td text-xs">{j.progress.total ? `${j.progress.done}/${j.progress.total}` : "—"}</td><td className="td text-xs">{j.attempts}/{j.max_attempts}</td>
              <td className="td whitespace-nowrap text-xs">{fmtRelative(j.created_at)}</td>
              <td className="td max-w-md text-xs">{j.error ? <span className="text-alert-700">{truncate(j.error, 160)}</span> : j.result ? <code className="text-ink-600">{truncate(JSON.stringify(j.result), 120)}</code> : "—"}</td>
              <td className="td">{(j.status === "error" || j.status === "blocked") && <button className="btn-secondary btn-sm" onClick={() => requeue.mutate(j.job_id)}><RefreshCw className="h-3 w-3" />{t("action.retry")}</button>}</td>
            </tr>
          ))}</tbody></table></div>
      )}
    </div>
  );
}

function CostsTab() {
  const { wsId } = useWorkspace();
  const { t } = useI18n();
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const c = useQuery({ queryKey: ["costs", wsId, days], queryFn: () => api.costs(wsId, Number(days)) });
  if (c.isLoading || !c.data) return <Loading />;
  const total = c.data.by_day.reduce((a, b) => a + b.cost_usd, 0);
  const reqs = c.data.by_day.reduce((a, b) => a + b.requests, 0);
  return (
    <div className="space-y-4">
      <Segmented className="w-fit" value={days} onChange={setDays} options={(["7", "30", "90"] as const).map((d) => ({ key: d, label: t("trust.days", { n: d }) }))} />
      <div className="grid gap-3 sm:grid-cols-3">
        <KPI label={t("trust.costs.total", { n: days })} value={fmtUsd(total)} hint={t("trust.costs.questions", { n: reqs })} />
        <KPI label={t("trust.costs.perQuestion")} value={fmtUsd(reqs ? total / reqs : 0)} />
        <KPI label={t("trust.costs.today")} value={fmtUsd(Number(c.data.system_today_usd || 0))} hint={t("trust.costs.budget", { v: fmtUsd(Number(c.data.daily_budget_usd || 0)) })} tone={Number(c.data.system_today_usd) > Number(c.data.daily_budget_usd) ? "red" : undefined} />
      </div>
      {c.data.by_day.length > 0 && (
        <div className="panel p-4">
          <VegaChart height={240} spec={{ $schema: "https://vega.github.io/schema/vega-lite/v5.json", data: { values: c.data.by_day }, mark: { type: "bar", color: "#5B4CF5", cornerRadiusTopLeft: 3, cornerRadiusTopRight: 3 },
            encoding: { x: { field: "day", type: "ordinal", title: null, axis: { labelAngle: -45 } }, y: { field: "cost_usd", type: "quantitative", title: "USD" }, tooltip: [{ field: "day" }, { field: "requests" }, { field: "cost_usd", format: ".4f" }] } }} />
        </div>
      )}
    </div>
  );
}
