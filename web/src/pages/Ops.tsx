// Giám sát & duyệt (editor+): việc nền, hàng chờ duyệt câu trả lời, xung đột dữ liệu, câu hỏi bị từ chối,
// phản hồi người dùng, lịch sử trace, chi phí (owner).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, ExternalLink, RefreshCw, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Markdown } from "@/components/Markdown";
import { Badge, Drawer, Empty, ErrorBox, Field, KPI, Loading, Modal, PageHeader, StatusBadge, useToast } from "@/components/ui";
import { VegaChart } from "@/components/VegaChart";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtDate, fmtRelative, fmtUsd, truncate } from "@/lib/format";
import type { Conflict, ReviewItem } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

const TABS = [
  { to: "jobs", label: "Việc nền" }, { to: "review", label: "Chờ duyệt" }, { to: "conflicts", label: "Xung đột" },
  { to: "abstentions", label: "Không trả lời được" }, { to: "feedback", label: "Phản hồi" }, { to: "traces", label: "Lịch sử hỏi đáp" }, { to: "costs", label: "Chi phí", owner: true },
];

export function OpsPage() {
  const { can } = useWorkspace();
  if (!can("editor")) return <Navigate to=".." replace />;
  return (
    <Page wide>
      <PageHeader title="Giám sát & duyệt" subtitle="Theo dõi xử lý nền, duyệt câu trả lời nhạy cảm, xử lý xung đột dữ liệu và xem KB còn thiếu gì." />
      <div className="mb-4 flex gap-1 overflow-x-auto border-b border-slate-200">
        {TABS.filter((t) => !t.owner || can("owner")).map((t) => (
          <NavLink key={t.to} to={t.to} className={({ isActive }) => clsx("-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium", isActive ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800")}>{t.label}</NavLink>
        ))}
      </div>
      <Routes>
        <Route index element={<Navigate to="jobs" replace />} />
        <Route path="jobs" element={<JobsTab />} />
        <Route path="review" element={<ReviewTab />} />
        <Route path="conflicts" element={<ConflictsTab />} />
        <Route path="abstentions" element={<AbstentionsTab />} />
        <Route path="feedback" element={<FeedbackTab />} />
        <Route path="traces" element={<TracesTab />} />
        <Route path="costs" element={<CostsTab />} />
      </Routes>
    </Page>
  );
}

function JobsTab() {
  const { wsId } = useWorkspace();
  const toast = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const jobs = useQuery({ queryKey: ["jobs", wsId, "ops", status], queryFn: () => api.jobs(wsId, { status: status || undefined, limit: 200 }), refetchInterval: 5000 });
  const requeue = useMutation({ mutationFn: (id: string) => api.requeueJob(wsId, id), onSuccess: () => { toast.ok("Đã xếp lại hàng"); void qc.invalidateQueries({ queryKey: ["jobs", wsId] }); }, onError: toast.err });
  const counts = (jobs.data || []).reduce<Record<string, number>>((a, j) => { a[j.status] = (a[j.status] || 0) + 1; return a; }, {});
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        {["", "queued", "processing", "done", "blocked", "error"].map((s) => <button key={s} className={clsx("badge cursor-pointer", status === s ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600")} onClick={() => setStatus(s)}>{s ? `${s} ${counts[s] || ""}` : `Tất cả ${jobs.data?.length || ""}`}</button>)}
      </div>
      {jobs.isLoading && <Loading />}
      {jobs.data && jobs.data.length === 0 && <Empty title="Không có việc nền" />}
      {jobs.data && jobs.data.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full"><thead><tr><th className="th">Loại</th><th className="th">Trạng thái</th><th className="th">Tiến độ</th><th className="th">Tài liệu</th><th className="th">Thử</th><th className="th">Tạo</th><th className="th">Kết quả / lỗi</th><th className="th" /></tr></thead>
            <tbody>{jobs.data.map((j) => (
              <tr key={j.job_id} className="hover:bg-slate-50">
                <td className="td font-mono text-xs">{j.type}</td><td className="td"><StatusBadge status={j.status} /></td>
                <td className="td text-xs">{j.progress.total ? `${j.progress.done}/${j.progress.total}` : "—"}</td>
                <td className="td text-xs">{j.document_id ? <a className="text-brand-700 hover:underline" href={`/w/${wsId}/documents/${j.document_id}`}>{j.document_id.slice(0, 8)}…</a> : "—"}</td>
                <td className="td text-xs">{j.attempts}/{j.max_attempts}</td><td className="td whitespace-nowrap text-xs">{fmtRelative(j.created_at)}</td>
                <td className="td max-w-md text-xs">{j.error ? <span className="text-red-600">{truncate(j.error, 160)}</span> : j.result ? <code className="text-slate-500">{truncate(JSON.stringify(j.result), 120)}</code> : "—"}</td>
                <td className="td">{(j.status === "error" || j.status === "blocked") && <button className="btn-secondary btn-sm" onClick={() => requeue.mutate(j.job_id)}><RefreshCw className="h-3 w-3" /> Chạy lại</button>}</td>
              </tr>
            ))}</tbody></table>
        </div>
      )}
    </div>
  );
}

function ReviewTab() {
  const { wsId } = useWorkspace();
  const toast = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState<"pending" | "approved" | "rejected">("pending");
  const [item, setItem] = useState<ReviewItem | null>(null);
  const [edited, setEdited] = useState("");
  const [note, setNote] = useState("");
  const list = useQuery({ queryKey: ["review", wsId, status], queryFn: () => api.review(wsId, status), refetchInterval: status === "pending" ? 10000 : false });
  const done = () => { setItem(null); void qc.invalidateQueries({ queryKey: ["review", wsId] }); };
  const approve = useMutation({ mutationFn: () => api.approveReview(wsId, item!.id, { edited_answer: edited.trim() !== item!.draft_answer.trim() ? edited : undefined, note: note || undefined }), onSuccess: () => { toast.ok("Đã duyệt và gửi cho người hỏi"); done(); }, onError: toast.err });
  const reject = useMutation({ mutationFn: () => api.rejectReview(wsId, item!.id, { note: note || undefined }), onSuccess: () => { toast.ok("Đã từ chối"); done(); }, onError: toast.err });
  return (
    <div>
      <div className="mb-3 flex gap-2">{(["pending", "approved", "rejected"] as const).map((s) => <button key={s} className={clsx("badge cursor-pointer", status === s ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600")} onClick={() => setStatus(s)}>{{ pending: "Chờ duyệt", approved: "Đã duyệt", rejected: "Từ chối" }[s]}</button>)}</div>
      {list.isLoading && <Loading />}
      {list.data && list.data.length === 0 && <Empty title="Không có mục nào" hint="Câu hỏi liên quan tiền/giá/chi phí (theo domain pack) được giữ lại để người có quyền duyệt trước khi gửi." />}
      <div className="space-y-2">
        {list.data?.map((r) => (
          <button key={r.id} className="card w-full p-4 text-left hover:border-brand-300" onClick={() => { setItem(r); setEdited(r.draft_answer); setNote(""); }}>
            <div className="flex items-start justify-between gap-2"><div className="font-medium text-slate-900">{r.question}</div><StatusBadge status={r.status} /></div>
            <div className="mt-1 text-xs text-slate-500">{r.user_id} · {fmtRelative(r.created_at)} · {r.reason}{r.overridden && <Badge tone="amber" className="ml-2">đã sửa khi duyệt</Badge>}</div>
            <p className="mt-2 text-sm text-slate-600">{truncate(r.final_answer || r.draft_answer, 240)}</p>
          </button>
        ))}
      </div>
      <Drawer open={!!item} onClose={() => setItem(null)} title="Duyệt câu trả lời" width="max-w-2xl">
        {item && (
          <div className="space-y-4 text-sm">
            <div><div className="label">Câu hỏi</div><div className="rounded-lg bg-slate-50 p-3">{item.question}</div></div>
            <div><div className="label">Bản nháp của hệ thống</div><div className="rounded-lg border border-slate-200 p-3"><Markdown text={item.draft_answer} citations={item.draft_citations} /></div>
              {item.draft_citations?.length > 0 && <ul className="mt-1 text-xs text-slate-500">{item.draft_citations.map((c) => <li key={c.n}>[{c.n}] {c.label}</li>)}</ul>}</div>
            {item.decision_trace?.summary?.length > 0 && <div><div className="label">Hệ thống đã làm gì</div><ol className="list-decimal space-y-0.5 pl-5 text-xs text-slate-600">{item.decision_trace.summary.map((s, i) => <li key={i}>{s}</li>)}</ol></div>}
            {item.status === "pending" && (
              <>
                <Field label="Câu trả lời gửi cho người hỏi (sửa nếu cần — sẽ được ghi nhận là override)"><textarea className="input font-mono text-xs" rows={8} value={edited} onChange={(e) => setEdited(e.target.value)} /></Field>
                <Field label="Ghi chú nội bộ"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
                <div className="flex justify-end gap-2"><button className="btn-danger" disabled={reject.isPending} onClick={() => reject.mutate()}><X className="h-4 w-4" /> Từ chối</button><button className="btn-primary" disabled={approve.isPending} onClick={() => approve.mutate()}><Check className="h-4 w-4" /> Duyệt & gửi</button></div>
              </>
            )}
            {item.status !== "pending" && <div className="text-xs text-slate-500">Người duyệt: {item.reviewer_id} · {fmtDate(item.reviewed_at, true)}{item.review_note ? ` · ${item.review_note}` : ""}</div>}
          </div>
        )}
      </Drawer>
    </div>
  );
}

function ConflictsTab() {
  const { wsId } = useWorkspace();
  const toast = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [item, setItem] = useState<Conflict | null>(null);
  const list = useQuery({ queryKey: ["conflicts", wsId, status], queryFn: () => api.conflicts(wsId, status) });
  const resolve = useMutation({ mutationFn: (b: { action: string; keep_record_id?: string }) => api.resolveConflict(wsId, item!.id, b), onSuccess: () => { toast.ok("Đã xử lý xung đột"); setItem(null); void qc.invalidateQueries({ queryKey: ["conflicts", wsId] }); }, onError: toast.err });
  return (
    <div>
      <div className="mb-3 flex gap-2">{(["open", "resolved"] as const).map((s) => <button key={s} className={clsx("badge cursor-pointer", status === s ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600")} onClick={() => setStatus(s)}>{s === "open" ? "Đang mở" : "Đã xử lý"}</button>)}</div>
      {list.isLoading && <Loading />}
      {list.data && list.data.length === 0 && <Empty title="Không có xung đột" hint="Xung đột xuất hiện khi hai bản ghi đang hoạt động mô tả cùng một đối tượng nhưng giá trị khác nhau; câu trả lời sẽ nêu rõ và ưu tiên bản mới hơn." />}
      <div className="space-y-2">
        {list.data?.map((c) => (
          <button key={c.id} className="card w-full p-4 text-left hover:border-brand-300" onClick={() => setItem(c)}>
            <div className="flex items-center gap-2"><Badge tone="amber">{c.kind}</Badge><span className="font-medium text-slate-900">{c.entity_key}</span><StatusBadge status={c.status} className="ml-auto" /></div>
            <div className="mt-1 text-xs text-slate-500">{c.record_ids.length} bản ghi · {fmtRelative(c.created_at)}{c.resolution ? ` · ${c.resolution}` : ""}</div>
          </button>
        ))}
      </div>
      <Modal open={!!item} onClose={() => setItem(null)} title="Xung đột dữ liệu" width="max-w-2xl">
        {item && (
          <div className="space-y-3 text-sm">
            <div><Badge tone="amber">{item.kind}</Badge> <b>{item.entity_key}</b></div>
            <pre className="max-h-80 overflow-auto rounded-lg bg-slate-50 p-3 text-xs">{JSON.stringify(item.details, null, 2)}</pre>
            {item.status === "open" && (
              <div>
                <div className="label">Xử lý</div>
                <div className="flex flex-wrap gap-2">
                  {item.record_ids.map((rid) => <button key={rid} className="btn-secondary btn-sm" onClick={() => resolve.mutate({ action: "keep_one", keep_record_id: rid })}>Giữ bản ghi {rid.slice(0, 8)}…</button>)}
                  <button className="btn-secondary btn-sm" onClick={() => resolve.mutate({ action: "resolved" })}>Đã xử lý ở nguồn</button>
                  <button className="btn-ghost btn-sm" onClick={() => resolve.mutate({ action: "dismiss" })}>Bỏ qua (không phải xung đột)</button>
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

function AbstentionsTab() {
  const { wsId } = useWorkspace();
  const [days, setDays] = useState(7);
  const list = useQuery({ queryKey: ["abstentions", wsId, days], queryFn: () => api.abstentions(wsId, days) });
  return (
    <div>
      <div className="mb-3 flex items-center gap-2 text-sm"><span className="text-slate-600">Trong</span>{[7, 30, 90].map((d) => <button key={d} className={clsx("badge cursor-pointer", days === d ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600")} onClick={() => setDays(d)}>{d} ngày</button>)}</div>
      {list.isLoading && <Loading />}
      {list.data && list.data.length === 0 && <Empty title="Không có câu hỏi bị từ chối" hint="Khi KB không đủ căn cứ, hệ thống từ chối trả lời thay vì đoán; các câu đó xuất hiện ở đây để bạn biết cần bổ sung tài liệu gì." />}
      {list.data && list.data.length > 0 && (
        <div className="card overflow-hidden"><table className="w-full"><thead><tr><th className="th">Câu hỏi</th><th className="th">Lý do</th><th className="th text-right">Điểm cao nhất</th><th className="th">Lúc</th></tr></thead>
          <tbody>{list.data.map((a) => <tr key={a.trace_id} className="hover:bg-slate-50"><td className="td">{a.question}</td><td className="td text-xs">{a.reason}</td><td className="td text-right font-mono text-xs">{a.top_score?.toFixed(2) ?? "—"}</td><td className="td whitespace-nowrap text-xs">{fmtRelative(a.created_at)}</td></tr>)}</tbody></table></div>
      )}
    </div>
  );
}

function FeedbackTab() {
  const { wsId } = useWorkspace();
  const toast = useToast();
  const qc = useQueryClient();
  const [rating, setRating] = useState<"down" | "up" | "">("down");
  const list = useQuery({ queryKey: ["ops-feedback", wsId, rating], queryFn: () => api.opsFeedback(wsId, { rating: rating || undefined, days: 90 }) });
  const upd = useMutation({ mutationFn: ({ id, status }: { id: string; status: string }) => api.updateFeedback(wsId, id, { status }), onSuccess: () => void qc.invalidateQueries({ queryKey: ["ops-feedback", wsId] }), onError: toast.err });
  return (
    <div>
      <div className="mb-3 flex gap-2">{([["down", "Chưa đúng 👎"], ["up", "Hữu ích 👍"], ["", "Tất cả"]] as const).map(([v, l]) => <button key={v} className={clsx("badge cursor-pointer", rating === v ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600")} onClick={() => setRating(v)}>{l}</button>)}</div>
      {list.isLoading && <Loading />}
      {list.data && list.data.length === 0 && <Empty title="Chưa có phản hồi" />}
      <div className="space-y-2">
        {list.data?.map((f) => (
          <div key={String(f.id)} className="card p-4 text-sm">
            <div className="flex items-start justify-between gap-2"><div className="font-medium text-slate-900">{String(f.question)}</div><div className="flex items-center gap-2"><Badge tone={f.rating === 1 ? "emerald" : "red"}>{f.rating === 1 ? "👍" : "👎"}</Badge><StatusBadge status={String(f.status)} /></div></div>
            {typeof f.comment === "string" && f.comment ? <p className="mt-1 italic text-slate-600">“{f.comment}”</p> : null}
            <p className="mt-2 text-xs text-slate-500">{truncate(String(f.answer || ""), 300)}</p>
            <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">{String(f.user_id)} · {fmtRelative(String(f.created_at))}<span className="flex-1" />
              {typeof f.trace_url === "string" && f.trace_url ? <a className="inline-flex items-center gap-1 text-brand-700 hover:underline" href={f.trace_url} target="_blank" rel="noreferrer">Langfuse <ExternalLink className="h-3 w-3" /></a> : null}
              {f.status === "new" && <><button className="btn-secondary btn-sm" onClick={() => upd.mutate({ id: String(f.id), status: "acknowledged" })}>Đã xem</button><button className="btn-secondary btn-sm" onClick={() => upd.mutate({ id: String(f.id), status: "resolved" })}>Đã xử lý</button></>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TracesTab() {
  const { wsId } = useWorkspace();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["traces", wsId, q, status], queryFn: () => api.traces(wsId, { q: q || undefined, status: status || undefined, limit: 200 }) });
  const detail = useQuery({ queryKey: ["trace", wsId, open], queryFn: () => api.trace(wsId, open!), enabled: !!open });
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2"><input className="input w-72" placeholder="Tìm theo câu hỏi…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input w-48" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Mọi trạng thái</option>{["answered", "answered_general", "abstained", "no_citation", "pending_review", "error"].map((s) => <option key={s} value={s}>{s}</option>)}</select></div>
      {list.isLoading && <Loading />}
      {list.data && (
        <div className="card overflow-x-auto"><table className="w-full"><thead><tr><th className="th">Lúc</th><th className="th">Người hỏi</th><th className="th">Câu hỏi</th><th className="th">Trạng thái</th><th className="th">Loại</th><th className="th text-right">ms</th><th className="th text-right">USD</th></tr></thead>
          <tbody>{list.data.map((t) => (
            <tr key={t.trace_id} className="cursor-pointer hover:bg-slate-50" onClick={() => setOpen(t.trace_id)}>
              <td className="td whitespace-nowrap text-xs">{fmtDate(t.created_at, true)}</td><td className="td text-xs">{t.user_id}</td><td className="td max-w-md truncate">{t.question}</td>
              <td className="td"><StatusBadge status={t.status} /></td><td className="td text-xs">{String(t.question_type || t.route || "")}</td>
              <td className="td text-right font-mono text-xs">{t.latency_ms ?? "—"}</td><td className="td text-right font-mono text-xs">{fmtUsd(t.cost_usd)}</td>
            </tr>
          ))}</tbody></table></div>
      )}
      <Drawer open={!!open} onClose={() => setOpen(null)} title="Chi tiết trace" width="max-w-3xl">
        {detail.isLoading && <Loading />}
        {detail.data && (
          <div className="space-y-3 text-sm">
            <div><div className="label">Câu hỏi</div><div className="rounded-lg bg-slate-50 p-3">{String(detail.data.question)}</div></div>
            <div><div className="label">Trả lời</div><div className="rounded-lg border border-slate-200 p-3"><Markdown text={String(detail.data.answer || "")} /></div></div>
            {typeof detail.data.langfuse_url === "string" && detail.data.langfuse_url ? <a className="inline-flex items-center gap-1 text-brand-700 hover:underline" href={detail.data.langfuse_url} target="_blank" rel="noreferrer">Mở trong Langfuse <ExternalLink className="h-3 w-3" /></a> : null}
            <details><summary className="cursor-pointer text-xs text-slate-500">Toàn bộ dữ kiện (JSON)</summary><pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">{JSON.stringify(detail.data, null, 2)}</pre></details>
          </div>
        )}
      </Drawer>
    </div>
  );
}

function CostsTab() {
  const { wsId } = useWorkspace();
  const [days, setDays] = useState(30);
  const c = useQuery({ queryKey: ["costs", wsId, days], queryFn: () => api.costs(wsId, days) });
  if (c.isLoading) return <Loading />;
  if (c.error || !c.data) return <ErrorBox error={c.error || "—"} />;
  const total = c.data.by_day.reduce((a, b) => a + b.cost_usd, 0);
  const reqs = c.data.by_day.reduce((a, b) => a + b.requests, 0);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm"><span className="text-slate-600">Trong</span>{[7, 30, 90].map((d) => <button key={d} className={clsx("badge cursor-pointer", days === d ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600")} onClick={() => setDays(d)}>{d} ngày</button>)}</div>
      <div className="grid gap-3 sm:grid-cols-3">
        <KPI label={`Chi phí LLM ${days} ngày`} value={fmtUsd(total)} hint={`${reqs} lượt hỏi`} />
        <KPI label="Trung bình / lượt" value={fmtUsd(reqs ? total / reqs : 0)} />
        <KPI label="Toàn hệ thống hôm nay" value={fmtUsd(Number(c.data.system_today_usd || 0))} hint={`ngân sách ngày ${fmtUsd(Number(c.data.daily_budget_usd || 0))}`} tone={Number(c.data.system_today_usd) > Number(c.data.daily_budget_usd) ? "red" : undefined} />
      </div>
      {c.data.by_day.length > 0 && (
        <div className="card p-4">
          <VegaChart height={240} spec={{ $schema: "https://vega.github.io/schema/vega-lite/v5.json", data: { values: c.data.by_day }, mark: { type: "bar", color: "#0d9488" },
            encoding: { x: { field: "day", type: "ordinal", title: "Ngày", axis: { labelAngle: -45 } }, y: { field: "cost_usd", type: "quantitative", title: "USD" }, tooltip: [{ field: "day" }, { field: "requests", title: "lượt" }, { field: "cost_usd", title: "USD", format: ".4f" }] } }} />
        </div>
      )}
    </div>
  );
}
