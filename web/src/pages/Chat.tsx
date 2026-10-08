// Hỏi đáp: hội thoại nhiều lượt (stream), trạng thái công cụ đang dùng, kết quả số liệu, trích dẫn bấm được, dải truy vết,
// giới hạn theo file, chế độ kỹ lưỡng/tra nhanh, phản hồi, ghim vào báo cáo.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowUp, Check, Database, FileSearch, FileText, Filter, MessageCircle, Network, Plus, Square, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AnswerView, type AnswerData } from "@/components/Answer";
import { PinDialog } from "@/components/PinDialog";
import { SourceDrawer } from "@/components/SourceDrawer";
import { Confirm, Modal, Segmented, SourceIcon, Spinner, useToast } from "@/components/ui";
import { useI18n } from "@/i18n";
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
  const { t } = useI18n();
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
    setMsgs((xs) => [...xs, { id: uid, role: "user", text: q }, { id: aid, role: "assistant", text: "", question: q, statusLine: t("chat.thinking"),
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
          update((m) => ({ ...m, statusLine: t(`chat.tool.${e.data.tool}`) === `chat.tool.${e.data.tool}` ? e.data.message : t(`chat.tool.${e.data.tool}`) }));
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
      update((m) => ({ ...m, statusLine: null, answer: { ...m.answer!, streaming: false, text: m.answer!.text || (err ? err.message : t("chat.stopped")), status: m.answer!.status || (err ? "error" : m.answer!.status) } }));
      void qc.invalidateQueries({ queryKey: ["sessions", wsId] });
    });
  }, [busy, docFilter, mode, nav, qc, wsId, t]);

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
      {/* các cuộc trò chuyện */}
      <aside className="hidden w-[260px] shrink-0 flex-col border-r border-ink-100 bg-white md:flex">
        <div className="p-3">
          <button className="btn-secondary w-full justify-start" onClick={() => { abortRef.current?.(); nav(`/w/${wsId}/chat`); }}><Plus className="h-4 w-4" /> {t("chat.new")}</button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3">
          {sessions.data?.length === 0 && <div className="px-3 py-8 text-center text-xs text-ink-400">{t("chat.noSessions")}</div>}
          {sessions.data?.map((s) => (
            <div key={s.session_id} className={clsx("group flex items-center gap-1 rounded-xl", s.session_id === sessionId ? "bg-paper ring-1 ring-ink-100" : "hover:bg-paper")}>
              <button className="min-w-0 flex-1 px-3 py-2 text-left" onClick={() => nav(`/w/${wsId}/chat/${s.session_id}`)}>
                <span className={clsx("block truncate text-sm", s.session_id === sessionId ? "font-semibold text-ink-900" : "text-ink-800")}>{s.title || t("chat.untitled")}</span>
                <span className="block text-[11px] text-ink-400">{fmtRelative(s.updated_at)}</span>
              </button>
              <button className="btn-ghost btn-sm opacity-0 focus:opacity-100 group-hover:opacity-100" onClick={() => setDel(s.session_id)} aria-label={t("action.delete")}><Trash2 className="h-3.5 w-3.5" /></button>
            </div>
          ))}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-ink-100 bg-white/80 px-4 py-2.5 backdrop-blur">
          <MessageCircle className="h-4 w-4 text-ink-400" />
          <span className="text-sm font-semibold text-ink-900">{t("nav.ask")}</span>
          <span className="flex-1" />
          <button className={clsx("btn-secondary btn-sm", docFilter.length > 0 && "border-ink-900")} onClick={() => setFilterOpen(true)}>
            <Filter className="h-3.5 w-3.5" /> {docFilter.length ? t("chat.filesN", { n: docFilter.length }) : t("chat.allFiles")}
          </button>
          <Segmented value={mode} onChange={setMode} options={[
            { key: "agent", label: t("chat.mode.agent"), title: t("chat.mode.agentHint") },
            { key: "strict", label: t("chat.mode.strict"), title: t("chat.mode.strictHint") },
          ]} />
        </div>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-8">
            {msgs.length === 0 && !history.isLoading && (
              <div className="pt-6">
                <h2 className="font-display text-[32px] font-semibold leading-tight text-ink-900">{t("chat.empty.title")}</h2>
                <p className="mt-2 max-w-xl text-[15px] leading-7 text-ink-600">{t("chat.empty.hint")}</p>
                <ul className="mt-6 grid gap-3 sm:grid-cols-3">
                  {([[FileSearch, "documents"], [Database, "tables"], [Network, "graph"]] as const).map(([I, k]) => (
                    <li key={k} className="rounded-xl bg-white p-3.5 ring-1 ring-ink-100">
                      <I className="h-4 w-4 text-brand-600" />
                      <div className="mt-2 text-sm font-semibold text-ink-900">{t(`chat.skill.${k}`)}</div>
                      <div className="mt-0.5 text-xs leading-5 text-ink-600">{t(`chat.skill.${k}Hint`)}</div>
                    </li>
                  ))}
                </ul>
                {suggestions.length > 0 && (
                  <div className="mt-8">
                    <div className="mb-2 text-[13px] font-medium text-ink-600">{t("chat.tryAsking")}</div>
                    <div className="flex flex-col gap-1.5">
                      {suggestions.map((s) => <button key={s} className="rounded-xl px-3 py-2 text-left text-[15px] text-ink-800 ring-1 ring-ink-100 transition hover:bg-white hover:ring-ink-300" onClick={() => send(s)}>{s}</button>)}
                    </div>
                  </div>
                )}
              </div>
            )}
            {history.isLoading && <div className="py-10 text-center text-sm text-ink-600"><Spinner className="mr-2 inline" /> {t("loading")}</div>}
            <div className="space-y-8">
              {msgs.map((m) => m.role === "user" ? (
                <div key={m.id} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-ink-900 px-4 py-2.5 text-[15px] leading-7 text-white">{m.text}</div>
                </div>
              ) : (
                <div key={m.id} className="min-w-0">
                  {m.statusLine && <div className="mb-2 flex items-center gap-2 text-sm text-ink-600"><Spinner /> {m.statusLine}</div>}
                  {m.answer && (
                    <AnswerView a={m.answer} onCite={(c) => c && setCite(c)} feedback={m.feedback}
                      onFeedback={(r) => m.answer?.trace_id && feedback.mutate({ traceId: m.answer.trace_id, rating: r })}
                      onPin={() => m.answer?.trace_id && setPin({ traceId: m.answer.trace_id, question: m.question })} />
                  )}
                </div>
              ))}
            </div>
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="px-4 pb-4">
          <form className="mx-auto max-w-3xl rounded-2xl bg-white p-2 shadow-lift ring-1 ring-ink-100 focus-within:ring-ink-300" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            {docFilter.length > 0 && (
              <div className="flex flex-wrap gap-1 px-2 pt-1">
                {docFilter.map((id) => (
                  <span key={id} className="chip bg-paper text-ink-800 ring-1 ring-ink-100"><FileText className="h-3 w-3" /> {truncate(docMap.get(id)?.title || id, 40)}
                    <button type="button" onClick={() => setDocFilter((xs) => xs.filter((x) => x !== id))} aria-label={t("action.delete")}><X className="h-3 w-3" /></button></span>
                ))}
              </div>
            )}
            <div className="flex items-end gap-2">
              <textarea ref={taRef} className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent px-2 py-2.5 text-[15px] outline-none placeholder:text-ink-400" rows={1}
                placeholder={t("chat.placeholder")} value={input} aria-label={t("chat.placeholder")}
                onChange={(e) => { setInput(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
              {busy ? (
                <button type="button" className="btn-secondary h-10" onClick={() => abortRef.current?.()}><Square className="h-4 w-4" /> {t("chat.stop")}</button>
              ) : (
                <button className="btn-primary h-10 w-10 p-0" disabled={!input.trim()} aria-label={t("action.ask")}><ArrowUp className="h-5 w-5" /></button>
              )}
            </div>
          </form>
        </div>
      </div>

      <SourceDrawer citation={cite} onClose={() => setCite(null)} />
      <PinDialog traceId={pin?.traceId || null} question={pin?.question} onClose={() => setPin(null)} />
      <Confirm open={!!del} onClose={() => setDel(null)} onConfirm={() => del && deleteSession.mutate(del)} title={t("chat.deleteTitle")} message={t("chat.deleteMsg")} danger busy={deleteSession.isPending} />
      <DocFilterModal open={filterOpen} onClose={() => setFilterOpen(false)} docs={docs.data?.items || []} value={docFilter} onChange={setDocFilter} />
    </div>
  );
}

function DocFilterModal({ open, onClose, docs, value, onChange }: { open: boolean; onClose: () => void; docs: Document[]; value: string[]; onChange: (v: string[]) => void }) {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const list = docs.filter((d) => !q || d.title.toLowerCase().includes(q.toLowerCase()) || d.filename.toLowerCase().includes(q.toLowerCase()));
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  return (
    <Modal open={open} onClose={onClose} title={t("chat.filter.title")} footer={<><button className="btn-secondary" onClick={() => onChange([])}>{t("chat.filter.clear")}</button><button className="btn-primary" onClick={onClose}>{t("action.done")}</button></>}>
      <p className="mb-3 text-sm text-ink-600">{t("chat.filter.hint")}</p>
      <input className="input mb-3" placeholder={t("chat.filter.search")} value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="max-h-80 overflow-y-auto rounded-xl border border-ink-100">
        {list.length === 0 && <div className="p-4 text-center text-sm text-ink-600">{t("empty.nothing")}</div>}
        {list.map((d) => (
          <button key={d.document_id} className="flex w-full items-center gap-3 border-b border-ink-100 px-3 py-2 text-left text-sm last:border-0 hover:bg-paper" onClick={() => toggle(d.document_id)}>
            <span className={clsx("flex h-5 w-5 items-center justify-center rounded-md border", value.includes(d.document_id) ? "border-brand-600 bg-brand-600 text-white" : "border-ink-300")}>{value.includes(d.document_id) && <Check className="h-3 w-3" />}</span>
            <SourceIcon type={d.source_type} size="sm" />
            <span className="min-w-0 flex-1"><span className="block truncate text-ink-900">{d.title}</span><span className="block truncate text-xs text-ink-600">{d.filename}</span></span>
          </button>
        ))}
      </div>
    </Modal>
  );
}
