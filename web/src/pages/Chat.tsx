// Hỏi đáp & khai phá: session nhiều lượt (SSE), trạng thái tool đang chạy, artifact chart/bảng, trích dẫn bấm được,
// lọc theo tài liệu, chế độ agent/strict, feedback, ghim vào báo cáo.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bot, Check, FileText, Filter, MessageSquare, Plus, Send, Square, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AnswerView, type AnswerData } from "@/components/Answer";
import { PinDialog } from "@/components/PinDialog";
import { SourceDrawer } from "@/components/SourceDrawer";
import { Confirm, Modal, Spinner, useToast } from "@/components/ui";
import { api, streamChat, type ChatEvent } from "@/lib/api";
import { fmtRelative, truncate } from "@/lib/format";
import type { Artifact, Citation, Document } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

interface Msg {
  id: string;
  role: "user" | "assistant";
  text: string;
  answer?: AnswerData;
  status?: string | null;
  statusLine?: string | null;
  question?: string;
  feedback?: "up" | "down" | null;
  error?: string | null;
}

function fromHistory(rows: Awaited<ReturnType<typeof api.sessionMessages>>): Msg[] {
  const out: Msg[] = [];
  let lastQ = "";
  for (const m of rows) {
    if (m.role === "user") {
      lastQ = m.content;
      out.push({ id: String(m.id), role: "user", text: m.content });
    } else {
      out.push({ id: String(m.id), role: "assistant", text: m.content, status: m.status, question: lastQ,
        answer: { text: m.content, status: m.status, citations: m.citations || [], artifacts: m.artifacts || [], trace_id: m.trace_id } });
    }
  }
  return out;
}

export function ChatPage() {
  const { wsId, ws } = useWorkspace();
  const { sessionId } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();

  const sessions = useQuery({ queryKey: ["sessions", wsId], queryFn: () => api.sessions(wsId) });
  const history = useQuery({ queryKey: ["session", wsId, sessionId], queryFn: () => api.sessionMessages(wsId, sessionId!), enabled: !!sessionId });
  const docs = useQuery({ queryKey: ["documents", wsId, "all-active"], queryFn: () => api.documents(wsId, { status: "active", page_size: 200 }) });

  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"agent" | "strict">("agent");
  const [docFilter, setDocFilter] = useState<string[]>([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const [cite, setCite] = useState<Citation | null>(null);
  const [pin, setPin] = useState<{ traceId: string; question?: string } | null>(null);
  const [del, setDel] = useState<string | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const curSession = useRef<string | null>(sessionId || null);
  // session vừa được tạo bởi lượt chat đang stream: không nạp lại lịch sử đè lên nội dung đang hiển thị
  const liveSession = useRef<string | null>(null);
  const busyRef = useRef(false);

  useEffect(() => { curSession.current = sessionId || null; }, [sessionId]);
  useEffect(() => {
    if (!sessionId) { if (!busyRef.current) setMsgs([]); return; }
    if (liveSession.current === sessionId) return;
    liveSession.current = null;
    if (history.data) setMsgs(fromHistory(history.data));
  }, [sessionId, history.data]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs]);
  // Huỷ stream khi rời trang; trì hoãn 1 nhịp để StrictMode (mount-unmount-mount khi dev) không cắt nhầm
  const pendingAbort = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (pendingAbort.current) { clearTimeout(pendingAbort.current); pendingAbort.current = null; }
    return () => { pendingAbort.current = setTimeout(() => abortRef.current?.(), 100); };
  }, []);

  const send = useCallback((question: string) => {
    const q = question.trim();
    if (!q || busy || busyRef.current) return;
    setBusy(true);
    busyRef.current = true;
    setInput("");
    const uid = `u-${Date.now()}`;
    const aid = `a-${Date.now()}`;
    setMsgs((xs) => [...xs, { id: uid, role: "user", text: q }, { id: aid, role: "assistant", text: "", question: q, statusLine: "Đang suy nghĩ…",
      answer: { text: "", citations: [], artifacts: [], streaming: true } }]);
    const update = (fn: (m: Msg) => Msg) => setMsgs((xs) => xs.map((m) => (m.id === aid ? fn(m) : m)));
    const artifacts: Artifact[] = [];
    abortRef.current = streamChat(wsId, { question: q, session_id: curSession.current, document_ids: docFilter, mode }, (e: ChatEvent) => {
      switch (e.event) {
        case "meta":
          if (!curSession.current && e.data.session_id) {
            curSession.current = e.data.session_id;
            liveSession.current = e.data.session_id;
            nav(`/w/${wsId}/chat/${e.data.session_id}`, { replace: true });
          }
          update((m) => ({ ...m, answer: { ...m.answer!, trace_id: e.data.trace_id } }));
          break;
        case "status":
          update((m) => ({ ...m, statusLine: e.data.message }));
          break;
        case "artifact":
          artifacts.push(e.data);
          update((m) => ({ ...m, answer: { ...m.answer!, artifacts: [...artifacts] } }));
          break;
        case "delta":
          update((m) => ({ ...m, statusLine: null, answer: { ...m.answer!, text: m.answer!.text + e.data.text } }));
          break;
        case "replace":
          update((m) => ({ ...m, statusLine: null, answer: { ...m.answer!, text: e.data.text } }));
          break;
        case "pending_review":
          update((m) => ({ ...m, statusLine: null, answer: { ...m.answer!, text: e.data.message, status: "pending_review" } }));
          break;
        case "citations":
          update((m) => ({ ...m, answer: { ...m.answer!, citations: e.data.citations } }));
          break;
        case "trace":
          update((m) => ({ ...m, answer: { ...m.answer!, trace: e.data.decision_trace, confidence: e.data.confidence, trace_url: e.data.trace_url } }));
          break;
        case "error":
          update((m) => ({ ...m, statusLine: null, error: e.data.message, answer: { ...m.answer!, text: m.answer!.text || e.data.message, status: "error", streaming: false } }));
          break;
        case "done":
          update((m) => ({ ...m, statusLine: null, status: e.data.status, answer: { ...m.answer!, status: e.data.status, streaming: false, artifacts: e.data.artifacts?.length ? e.data.artifacts : m.answer!.artifacts, text: e.data.answer || m.answer!.text } }));
          break;
      }
    }, (err) => {
      setBusy(false);
      busyRef.current = false;
      abortRef.current = null;
      if (curSession.current) void qc.invalidateQueries({ queryKey: ["session", wsId, curSession.current], refetchType: "none" });
      update((m) => ({ ...m, statusLine: null, answer: { ...m.answer!, streaming: false, text: m.answer!.text || (err ? err.message : "Đã dừng."), status: m.answer!.status || (err ? "error" : m.answer!.status) } }));
      void qc.invalidateQueries({ queryKey: ["sessions", wsId] });
    });
  }, [busy, docFilter, mode, nav, qc, wsId]);

  // ?q=... từ trang tổng quan
  useEffect(() => {
    const q = params.get("q");
    if (q && !busy) {
      params.delete("q");
      setParams(params, { replace: true });
      send(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const feedback = useMutation({
    mutationFn: ({ traceId, rating }: { traceId: string; rating: "up" | "down" }) => api.feedback(wsId, { trace_id: traceId, rating }),
    onSuccess: (_d, v) => setMsgs((xs) => xs.map((m) => (m.answer?.trace_id === v.traceId ? { ...m, feedback: v.rating } : m))),
    onError: toast.err,
  });
  const deleteSession = useMutation({
    mutationFn: (sid: string) => api.deleteSession(wsId, sid),
    onSuccess: (_d, sid) => { setDel(null); void qc.invalidateQueries({ queryKey: ["sessions", wsId] }); if (sid === sessionId) nav(`/w/${wsId}/chat`); },
    onError: toast.err,
  });

  const docMap = useMemo(() => new Map((docs.data?.items || []).map((d) => [d.document_id, d])), [docs.data]);
  const suggestions = useMemo(() => {
    const fromDocs = (docs.data?.items || []).flatMap((d) => d.semantic_profile?.suggested_questions || []);
    return [...new Set([...fromDocs, ...ws.suggested_questions])].slice(0, 6);
  }, [docs.data, ws.suggested_questions]);

  return (
    <div className="flex h-full">
      {/* session list */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="p-3">
          <button className="btn-secondary w-full" onClick={() => { abortRef.current?.(); nav(`/w/${wsId}/chat`); }}><Plus className="h-4 w-4" /> Cuộc trò chuyện mới</button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {sessions.data?.length === 0 && <div className="px-3 py-6 text-center text-xs text-slate-400">Chưa có cuộc trò chuyện</div>}
          {sessions.data?.map((s) => (
            <div key={s.session_id} className={clsx("group flex items-center gap-1 rounded-lg", s.session_id === sessionId ? "bg-brand-50" : "hover:bg-slate-100")}>
              <button className="min-w-0 flex-1 px-3 py-2 text-left" onClick={() => nav(`/w/${wsId}/chat/${s.session_id}`)}>
                <span className={clsx("block truncate text-sm", s.session_id === sessionId ? "font-medium text-brand-800" : "text-slate-700")}>{s.title || "Cuộc trò chuyện"}</span>
                <span className="block text-xs text-slate-400">{fmtRelative(s.updated_at)}</span>
              </button>
              <button className="btn-ghost btn-sm invisible group-hover:visible" onClick={() => setDel(s.session_id)}><Trash2 className="h-3.5 w-3.5" /></button>
            </div>
          ))}
        </div>
      </aside>

      {/* conversation */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
          <MessageSquare className="h-4 w-4 text-brand-600" />
          <span className="text-sm font-semibold text-slate-800">Hỏi đáp & khai phá</span>
          <span className="flex-1" />
          <button className={clsx("btn-secondary btn-sm", docFilter.length && "border-brand-400 bg-brand-50 text-brand-700")} onClick={() => setFilterOpen(true)}>
            <Filter className="h-3.5 w-3.5" /> {docFilter.length ? `${docFilter.length} tài liệu` : "Mọi tài liệu"}
          </button>
          <div className="flex rounded-lg border border-slate-300 p-0.5 text-xs">
            <button className={clsx("rounded-md px-2 py-1", mode === "agent" ? "bg-brand-600 text-white" : "text-slate-600")} onClick={() => setMode("agent")} title="Trợ lý dùng công cụ: tìm tài liệu, truy vấn bảng, đồ thị, tóm tắt">Trợ lý</button>
            <button className={clsx("rounded-md px-2 py-1", mode === "strict" ? "bg-brand-600 text-white" : "text-slate-600")} onClick={() => setMode("strict")} title="Tra cứu nhanh: chỉ trả lời từ đoạn văn bản có trích dẫn">Tra cứu</button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6">
            {msgs.length === 0 && !history.isLoading && (
              <div className="py-10 text-center">
                <Bot className="mx-auto h-10 w-10 text-brand-300" />
                <h2 className="mt-3 text-lg font-semibold text-slate-800">Bạn muốn biết gì về dữ liệu của mình?</h2>
                <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">Hỏi tổng quan, tra cứu chi tiết, yêu cầu tóm tắt, thống kê/so sánh số liệu hay tìm mối liên hệ giữa các thực thể. Mọi câu trả lời đều có trích dẫn nguồn.</p>
                <div className="mx-auto mt-6 grid max-w-2xl gap-2 sm:grid-cols-2">
                  {suggestions.map((s) => <button key={s} className="card px-3 py-2 text-left text-sm text-slate-700 hover:border-brand-300 hover:bg-brand-50" onClick={() => send(s)}>{s}</button>)}
                </div>
              </div>
            )}
            {history.isLoading && <div className="py-10 text-center text-sm text-slate-500"><Spinner className="mr-2 inline" /> Đang tải cuộc trò chuyện…</div>}
            <div className="space-y-6">
              {msgs.map((m) => m.role === "user" ? (
                <div key={m.id} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] text-white">{m.text}</div>
                </div>
              ) : (
                <div key={m.id} className="flex gap-3">
                  <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-100 text-brand-700"><Bot className="h-4 w-4" /></span>
                  <div className="min-w-0 flex-1">
                    {m.statusLine && <div className="mb-1 flex items-center gap-2 text-sm text-slate-500"><Spinner /> {m.statusLine}</div>}
                    {m.answer && (
                      <AnswerView a={m.answer} onCite={(c) => c && setCite(c)} feedback={m.feedback}
                        onFeedback={(r) => m.answer?.trace_id && feedback.mutate({ traceId: m.answer.trace_id, rating: r })}
                        onPin={() => m.answer?.trace_id && setPin({ traceId: m.answer.trace_id, question: m.question })} />
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-slate-200 bg-white p-3">
          <form className="mx-auto flex max-w-3xl items-end gap-2" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <textarea ref={taRef} className="input max-h-40 min-h-[44px] flex-1 resize-none" rows={1} placeholder="Nhập câu hỏi… (Enter để gửi, Shift+Enter xuống dòng)" value={input}
              onChange={(e) => { setInput(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
            {busy ? (
              <button type="button" className="btn-secondary" onClick={() => abortRef.current?.()}><Square className="h-4 w-4" /> Dừng</button>
            ) : (
              <button className="btn-primary" disabled={!input.trim()}><Send className="h-4 w-4" /> Gửi</button>
            )}
          </form>
          {docFilter.length > 0 && (
            <div className="mx-auto mt-2 flex max-w-3xl flex-wrap gap-1">
              {docFilter.map((id) => (
                <span key={id} className="badge bg-brand-50 text-brand-700"><FileText className="h-3 w-3" /> {truncate(docMap.get(id)?.title || id, 40)}
                  <button onClick={() => setDocFilter((xs) => xs.filter((x) => x !== id))}><X className="h-3 w-3" /></button></span>
              ))}
            </div>
          )}
        </div>
      </div>

      <SourceDrawer citation={cite} onClose={() => setCite(null)} />
      <PinDialog traceId={pin?.traceId || null} question={pin?.question} onClose={() => setPin(null)} />
      <Confirm open={!!del} onClose={() => setDel(null)} onConfirm={() => del && deleteSession.mutate(del)} title="Xoá cuộc trò chuyện" message="Toàn bộ tin nhắn của cuộc trò chuyện sẽ bị xoá." danger busy={deleteSession.isPending} />
      <DocFilterModal open={filterOpen} onClose={() => setFilterOpen(false)} docs={docs.data?.items || []} value={docFilter} onChange={setDocFilter} />
    </div>
  );
}

function DocFilterModal({ open, onClose, docs, value, onChange }: { open: boolean; onClose: () => void; docs: Document[]; value: string[]; onChange: (v: string[]) => void }) {
  const [q, setQ] = useState("");
  const list = docs.filter((d) => !q || d.title.toLowerCase().includes(q.toLowerCase()) || d.filename.toLowerCase().includes(q.toLowerCase()));
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  return (
    <Modal open={open} onClose={onClose} title="Giới hạn phạm vi tài liệu" footer={<><button className="btn-secondary" onClick={() => onChange([])}>Bỏ lọc</button><button className="btn-primary" onClick={onClose}>Xong</button></>}>
      <input className="input mb-3" placeholder="Tìm tài liệu…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="max-h-80 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200">
        {list.length === 0 && <div className="p-4 text-center text-sm text-slate-500">Không có tài liệu</div>}
        {list.map((d) => (
          <button key={d.document_id} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50" onClick={() => toggle(d.document_id)}>
            <span className={clsx("flex h-4 w-4 items-center justify-center rounded border", value.includes(d.document_id) ? "border-brand-600 bg-brand-600 text-white" : "border-slate-300")}>{value.includes(d.document_id) && <Check className="h-3 w-3" />}</span>
            <span className="min-w-0 flex-1"><span className="block truncate text-slate-800">{d.title}</span><span className="block truncate text-xs text-slate-500">{d.filename} · {d.doc_type}</span></span>
          </button>
        ))}
      </div>
    </Modal>
  );
}
