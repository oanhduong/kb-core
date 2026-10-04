// Client gọi API RetailKB qua /api (Vite proxy khi dev, nginx khi production).
import type { AnswerStatus, ChunkView, Citation, SessionMessage } from "./types";

const BASE = "/api";
const TOKEN_KEY = "retailkb.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export interface TokenInfo {
  sub: string;
  role: string;
  store_code: string | null;
  exp: number;
}

export function decodeToken(t: string | null): TokenInfo | null {
  if (!t) return null;
  try {
    const payload = t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = decodeURIComponent(
      atob(payload)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join(""),
    );
    const info = JSON.parse(json) as TokenInfo;
    return info.exp * 1000 > Date.now() ? info : null;
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    let msg = `Lỗi ${res.status}`;
    try {
      const body = await res.json();
      msg = body?.error?.message || msg;
    } catch {
      /* body không phải JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  devLogin: (user_id: string, store_code: string, role: string) =>
    request<{ access_token: string }>("/auth/dev-login", {
      method: "POST",
      body: JSON.stringify({ user_id, store_code: store_code || null, role }),
    }),
  feedback: (trace_id: string, rating: "up" | "down", comment?: string) =>
    request("/feedback", { method: "POST", body: JSON.stringify({ trace_id, rating, comment }) }),
  answer: (trace_id: string) => request<AnswerStatus>(`/answers/${trace_id}`),
  chunk: (url: string) => request<ChunkView>(url),
  messages: (session_id: string) => request<SessionMessage[]>(`/chat/sessions/${session_id}/messages`),
};

export type StreamHandler = (event: string, data: any) => void;

// POST /chat trả text/event-stream — EventSource không hỗ trợ POST + header nên tự đọc stream.
export async function streamChat(
  body: { question: string; session_id?: string | null },
  onEvent: StreamHandler,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken();
  const res = await fetch(BASE + "/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let msg = `Lỗi ${res.status}`;
    try {
      msg = (await res.json())?.error?.message || msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue; // heartbeat
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) {
        try {
          onEvent(event, JSON.parse(dataLines.join("\n")));
        } catch {
          /* bỏ qua event hỏng */
        }
      }
    }
  }
}

export type { Citation };
