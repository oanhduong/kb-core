import { useCallback, useEffect, useRef, useState } from "react";
import { api, decodeToken, getToken, setToken, streamChat } from "./api";
import type { ChatMsg, Citation, ChunkView, DecisionTrace } from "./types";

const SESSION_KEY = "retailkb.session";
const uid = () => Math.random().toString(36).slice(2);

// ============================================================================ Login
function Login({ onDone }: { onDone: () => void }) {
  const [userId, setUserId] = useState("nv01");
  const [store, setStore] = useState("GM-HN01");
  const [role, setRole] = useState("staff");
  const [token, setTok] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const dev = async () => {
    setErr(null);
    try {
      const r = await api.devLogin(userId.trim(), store.trim(), role);
      setToken(r.access_token);
      onDone();
    } catch (e: any) {
      setErr(e.message);
    }
  };
  const paste = () => {
    if (!decodeToken(token.trim())) return setErr("Token không hợp lệ hoặc đã hết hạn");
    setToken(token.trim());
    onDone();
  };
  return (
    <div className="login">
      <h1>RetailKB Copilot</h1>
      <p className="muted">Trợ lý tra cứu chính sách, giá, khuyến mãi, tồn kho cho nhân viên cửa hàng.</p>
      <section className="card">
        <h2>Đăng nhập nhanh (môi trường thử nghiệm)</h2>
        <label>Mã nhân viên<input value={userId} onChange={(e) => setUserId(e.target.value)} /></label>
        <label>Mã cửa hàng<input value={store} onChange={(e) => setStore(e.target.value)} placeholder="GM-HN01" /></label>
        <label>
          Vai trò
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="staff">Nhân viên</option>
            <option value="supervisor">Supervisor</option>
          </select>
        </label>
        <button className="primary" onClick={dev}>Vào</button>
      </section>
      <section className="card">
        <h2>Hoặc dán token đăng nhập</h2>
        <textarea rows={3} value={token} onChange={(e) => setTok(e.target.value)} placeholder="eyJhbGciOi..." />
        <button onClick={paste}>Dùng token</button>
      </section>
      {err && <div className="error">{err}</div>}
    </div>
  );
}

// ============================================================================ Answer text with [n]
function AnswerText({ text, citations, onCite }: { text: string; citations: Citation[]; onCite: (c: Citation) => void }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <div className="answer-text">
      {parts.map((p, i) => {
        const m = p.match(/^\[(\d+)\]$/);
        if (m) {
          const c = citations.find((x) => x.n === Number(m[1]));
          return c ? (
            <button key={i} className="cite-chip" title={c.label} onClick={() => onCite(c)}>{m[1]}</button>
          ) : (
            <sup key={i} className="cite-pending">{m[1]}</sup>
          );
        }
        return <span key={i}>{p}</span>;
      })}
    </div>
  );
}

function TracePanel({ trace, confidence }: { trace?: DecisionTrace; confidence?: { level: string } }) {
  if (!trace?.summary?.length) return null;
  return (
    <details className="trace">
      <summary>
        Vì sao có câu trả lời này? {confidence ? <span className={`conf conf-${confidence.level.replace(" ", "-")}`}>Tin cậy: {confidence.level}</span> : null}
      </summary>
      <ul>{trace.summary.map((l, i) => <li key={i}>{l}</li>)}</ul>
      {trace.graph_paths?.length ? (
        <>
          <h4>Đường đi trên đồ thị tri thức</h4>
          <ul className="paths">{trace.graph_paths.slice(0, 5).map((p, i) => <li key={i}>{p}</li>)}</ul>
        </>
      ) : null}
      {trace.found?.length ? (
        <>
          <h4>Nguồn đã tìm</h4>
          <ul className="found">
            {trace.found.slice(0, 8).map((f: any, i) => (
              <li key={i} className={f.used_in_answer ? "used" : ""}>
                {f.used_in_answer ? "✔ " : "· "}{f.citation}{f.score != null ? ` (điểm ${Number(f.score).toFixed(2)})` : ""}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </details>
  );
}

function Message({ m, onCite, onRate }: { m: ChatMsg; onCite: (c: Citation) => void; onRate: (m: ChatMsg, r: "up" | "down") => void }) {
  if (m.role === "user") return <div className="msg user"><div className="bubble">{m.text}</div></div>;
  const waiting = m.status === "streaming" && !m.text;
  return (
    <div className="msg assistant">
      <div className={`bubble status-${m.status}`}>
        {waiting ? <div className="typing"><span /><span /><span /></div> : <AnswerText text={m.text} citations={m.citations} onCite={onCite} />}
        {m.status === "pending_review" && <div className="banner warn">⏳ Đang chờ supervisor duyệt — câu trả lời sẽ hiện ở đây khi được duyệt.</div>}
        {m.status === "rejected" && <div className="banner warn">Supervisor chưa xác nhận câu trả lời này.</div>}
        {m.conflicts.length > 0 && (
          <div className="banner conflict">
            ⚠️ Các nguồn đang ghi khác nhau:
            {m.conflicts.map((c, i) => (
              <ul key={i}>
                {c.values.map((v, j) => (
                  <li key={j}>{v.display} — {v.source}{v.effective_date ? `, hiệu lực ${v.effective_date}` : ""}{v.is_newest ? " (mới hơn)" : ""}</li>
                ))}
              </ul>
            ))}
          </div>
        )}
        {m.citations.length > 0 && (
          <div className="sources">
            {m.citations.map((c) => (
              <button key={c.n} className="source" onClick={() => onCite(c)}>
                <b>[{c.n}]</b> {c.label}
              </button>
            ))}
          </div>
        )}
        <TracePanel trace={m.trace} confidence={m.confidence} />
        {m.traceId && m.status !== "streaming" && (
          <div className="rate">
            <button className={m.rating === "up" ? "on" : ""} onClick={() => onRate(m, "up")} aria-label="Hữu ích">👍</button>
            <button className={m.rating === "down" ? "on" : ""} onClick={() => onRate(m, "down")} aria-label="Chưa đúng">👎</button>
          </div>
        )}
      </div>
    </div>
  );
}

function SourceSheet({ cite, onClose }: { cite: Citation | null; onClose: () => void }) {
  const [data, setData] = useState<ChunkView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    setErr(null);
    if (cite) api.chunk(cite.url).then(setData).catch((e) => setErr(e.message));
  }, [cite]);
  if (!cite) return null;
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <b>[{cite.n}] {cite.label}</b>
          <button onClick={onClose} aria-label="Đóng">✕</button>
        </div>
        {err && <div className="error">{err}</div>}
        {!data && !err && <div className="muted">Đang tải đoạn gốc…</div>}
        {data && (
          <>
            <div className="muted small">
              {data.document?.title} · {data.location}
              {data.effective_date ? ` · hiệu lực ${data.effective_date}` : ""}
              {!data.is_active ? " · ĐÃ HẾT HIỆU LỰC" : ""}
            </div>
            <pre className="chunk">{data.parent_text || data.record_content || data.text}</pre>
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================================ Chat
function Chat({ onLogout }: { onLogout: () => void }) {
  const info = decodeToken(getToken());
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(localStorage.getItem(SESSION_KEY));
  const [cite, setCite] = useState<Citation | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const patch = useCallback((id: string, upd: Partial<ChatMsg> | ((m: ChatMsg) => Partial<ChatMsg>)) => {
    setMsgs((all) => all.map((m) => (m.id === id ? { ...m, ...(typeof upd === "function" ? upd(m) : upd) } : m)));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  // khôi phục lịch sử session khi tải lại trang
  useEffect(() => {
    if (!sessionId) return;
    api.messages(sessionId)
      .then((rows) =>
        setMsgs(rows.map((r) => ({
          id: uid(), role: r.role, text: r.content, traceId: r.trace_id || undefined, citations: r.citations || [], conflicts: [],
          status: (r.status === "final" ? "answered" : r.status) as ChatMsg["status"],
        }))),
      )
      .catch(() => {
        localStorage.removeItem(SESSION_KEY);
        setSessionId(null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // poll câu trả lời đang chờ supervisor duyệt
  useEffect(() => {
    const pending = msgs.filter((m) => m.status === "pending_review" && m.traceId);
    if (!pending.length) return;
    const t = setInterval(async () => {
      for (const m of pending) {
        try {
          const a = await api.answer(m.traceId!);
          if (a.status !== "pending_review" && a.answer) {
            patch(m.id, { text: a.answer, citations: a.citations, conflicts: a.conflicts || [], status: a.status === "rejected" ? "rejected" : "answered" });
          }
        } catch {
          /* thử lại lần sau */
        }
      }
    }, 5000);
    return () => clearInterval(t);
  }, [msgs, patch]);

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    const aid = uid();
    setMsgs((all) => [...all,
      { id: uid(), role: "user", text: q, status: "answered", citations: [], conflicts: [] },
      { id: aid, role: "assistant", text: "", status: "streaming", citations: [], conflicts: [] }]);
    try {
      await streamChat({ question: q, session_id: sessionId }, (ev, data) => {
        switch (ev) {
          case "meta":
            if (data.session_id) {
              setSessionId(data.session_id);
              localStorage.setItem(SESSION_KEY, data.session_id);
            }
            patch(aid, { traceId: data.trace_id });
            break;
          case "delta":
            patch(aid, (m) => ({ text: m.text + data.text }));
            break;
          case "replace":
            patch(aid, { text: data.text });
            break;
          case "pending_review":
            patch(aid, { status: "pending_review", reviewId: data.review_id, text: "" });
            break;
          case "citations":
            patch(aid, { citations: data.citations || [] });
            break;
          case "conflicts":
            patch(aid, { conflicts: data.conflicts || [] });
            break;
          case "trace":
            patch(aid, { trace: data.decision_trace, confidence: data.confidence });
            break;
          case "error":
            patch(aid, { text: data.message, status: "error" });
            break;
          case "done":
            patch(aid, (m) => ({
              status: m.status === "pending_review" ? "pending_review" : (data.status as ChatMsg["status"]),
              text: m.status === "pending_review" ? data.answer : m.text || data.answer || m.text,
              traceId: data.trace_id || m.traceId,
            }));
            break;
        }
      });
    } catch (e: any) {
      if (e.status === 401) {
        setToken(null);
        onLogout();
        return;
      }
      patch(aid, { text: e.message || "Không kết nối được máy chủ.", status: "error" });
    } finally {
      setBusy(false);
    }
  };

  const rate = async (m: ChatMsg, r: "up" | "down") => {
    if (!m.traceId) return;
    const comment = r === "down" ? window.prompt("Câu trả lời sai/thiếu ở đâu? (không bắt buộc)") || undefined : undefined;
    try {
      await api.feedback(m.traceId, r, comment);
      patch(m.id, { rating: r });
      setNotice(r === "down" ? "Đã gửi phản hồi cho admin. Cảm ơn bạn!" : "Cảm ơn bạn!");
      setTimeout(() => setNotice(null), 2500);
    } catch (e: any) {
      setNotice(e.message);
    }
  };

  const newChat = () => {
    localStorage.removeItem(SESSION_KEY);
    setSessionId(null);
    setMsgs([]);
  };

  return (
    <div className="app">
      <header>
        <div>
          <b>RetailKB Copilot</b>
          <div className="muted small">{info?.sub} · {info?.store_code || "toàn hệ thống"} · {info?.role}</div>
        </div>
        <div className="actions">
          <button onClick={newChat}>Hội thoại mới</button>
          <button onClick={() => { setToken(null); onLogout(); }}>Thoát</button>
        </div>
      </header>
      <main>
        {msgs.length === 0 && (
          <div className="empty">
            <p>Hỏi về chính sách đổi trả, bảo hành, giá, khuyến mãi, tồn kho…</p>
            {["Thời hạn đổi trả hàng điện tử là bao lâu?", "Loa ĐT-018 đang có khuyến mãi nào?", "Những sản phẩm nào sắp hết hàng ở cửa hàng mình?"].map((s) => (
              <button key={s} className="suggest" onClick={() => setInput(s)}>{s}</button>
            ))}
          </div>
        )}
        {msgs.map((m) => <Message key={m.id} m={m} onCite={setCite} onRate={rate} />)}
        <div ref={endRef} />
      </main>
      {notice && <div className="toast">{notice}</div>}
      <footer>
        <textarea
          rows={1}
          value={input}
          placeholder="Nhập câu hỏi…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button className="primary" onClick={send} disabled={busy || !input.trim()}>{busy ? "…" : "Gửi"}</button>
      </footer>
      <SourceSheet cite={cite} onClose={() => setCite(null)} />
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(() => decodeToken(getToken()) !== null);
  return authed ? <Chat onLogout={() => setAuthed(false)} /> : <Login onDone={() => setAuthed(true)} />;
}
