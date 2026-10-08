// Client HTTP + SSE cho API (qua /api: Vite proxy khi dev, nginx khi production).
import type {
  AnalyticsAnswer, ApiKey, Artifact, AssistantResponse, Chart, ChatSession, ChunkView, Citation, Conflict, Confidence,
  DecisionTrace, Document, DocumentDetail, DomainPack, Entity, EntityDetail, GraphOverview, Invite, Job, KBHealth,
  KBRecord, Member, Neighborhood, Paged, QueryResult, Report, ReportBlock, ReviewItem, SavedQuery, SessionMessage,
  TableDetail, TableInfo, TraceSummary, User, Workspace,
} from "./types";

import { getLang, translate } from "@/i18n";
import type { DataModel, DataModelDetail, Insights, ModelConnection, RuleSpec, RuleTest } from "./types";

export const BASE = "/api";
const TOKEN_KEY = "kbx.token";
const WS_KEY = "kbx.workspace";

export const auth = {
  token: (): string | null => localStorage.getItem(TOKEN_KEY),
  setToken(t: string | null) {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  },
  workspace: (): string | null => localStorage.getItem(WS_KEY),
  setWorkspace(id: string | null) {
    if (id) localStorage.setItem(WS_KEY, id);
    else localStorage.removeItem(WS_KEY);
  },
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public requestId?: string) {
    super(message);
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function parseError(res: Response): Promise<ApiError> {
  let msg = translate("error.http", { status: res.status });
  let code: string | undefined;
  let rid: string | undefined;
  try {
    const body = await res.json();
    msg = body?.error?.message || body?.detail?.[0]?.msg || body?.detail || msg;
    code = body?.error?.code;
    rid = body?.error?.request_id;
  } catch {
    /* ignore */
  }
  if (typeof msg !== "string") msg = JSON.stringify(msg);
  return new ApiError(res.status, msg, code, rid);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = auth.token();
  const headers: Record<string, string> = { "Accept-Language": getLang(), ...(init.headers as Record<string, string> | undefined) };
  if (!(init.body instanceof FormData)) headers["Content-Type"] = headers["Content-Type"] || "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(BASE + path, { ...init, headers });
  if (res.status === 401 && token) onUnauthorized?.();
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

const get = <T>(p: string) => request<T>(p);
const post = <T>(p: string, body?: unknown) => request<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T>(p: string, body: unknown) => request<T>(p, { method: "PATCH", body: JSON.stringify(body) });
const del = <T>(p: string) => request<T>(p, { method: "DELETE" });

export function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

/** Tải file có Authorization (fetch -> blob -> a[download]). */
export async function download(path: string, fallbackName: string): Promise<void> {
  const token = auth.token();
  const res = await fetch(BASE + path, { headers: { "Accept-Language": getLang(), ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  if (!res.ok) throw await parseError(res);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd) || /filename="?([^";]+)"?/.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------------------------------------------------------------- auth / accounts
export const api = {
  signup: (b: { email: string; password: string; name: string; workspace_name?: string }) =>
    post<{ access_token: string; user: User; workspace?: Workspace }>("/auth/signup", b),
  login: (b: { email: string; password: string }) => post<{ access_token: string; user: User }>("/auth/login", b),
  me: () => get<{ user: User; workspaces: Workspace[] }>("/auth/me"),
  updateMe: (b: { name?: string; old_password?: string; new_password?: string }) => patch<User>("/auth/me", b),
  workspaces: () => get<Workspace[]>("/workspaces"),
  createWorkspace: (b: { name: string; description?: string; domain_pack: string }) => post<Workspace>("/workspaces", b),
  workspace: (ws: string) => get<Workspace>(`/w/${ws}`),
  updateWorkspace: (ws: string, b: Partial<Pick<Workspace, "name" | "description" | "domain_pack" | "settings">>) => patch<Workspace>(`/w/${ws}`, b),
  archiveWorkspace: (ws: string) => del<{ archived: string }>(`/w/${ws}`),
  members: (ws: string) => get<{ members: Member[]; pending_invites: Invite[] }>(`/w/${ws}/members`),
  invite: (ws: string, b: { email: string; role: string; attributes?: Record<string, unknown> }) => post<unknown>(`/w/${ws}/members`, b),
  updateMember: (ws: string, uid: string, b: { role?: string; attributes?: Record<string, unknown> }) => patch<unknown>(`/w/${ws}/members/${uid}`, b),
  removeMember: (ws: string, uid: string) => del<unknown>(`/w/${ws}/members/${uid}`),
  cancelInvite: (ws: string, id: string) => del<unknown>(`/w/${ws}/invites/${id}`),
  apiKeys: (ws: string) => get<ApiKey[]>(`/w/${ws}/api-keys`),
  createApiKey: (ws: string, b: { name: string; role: string; rate_limit_per_minute?: number }) => post<ApiKey & { api_key: string }>(`/w/${ws}/api-keys`, b),
  revokeApiKey: (ws: string, id: string) => del<unknown>(`/w/${ws}/api-keys/${id}`),
  domainPacks: () => get<DomainPack[]>("/domain-packs"),
  config: () => get<Record<string, unknown>>("/config"),
  adminUsers: () => get<User[]>("/admin/users"),
  adminUpdateUser: (id: string, b: { is_active?: boolean; is_superadmin?: boolean }) => patch<User>(`/admin/users/${id}`, b),
  adminWorkspaces: () => get<Workspace[]>("/admin/workspaces"),
  adminCosts: (days = 30) => get<{ today_usd: number; daily_budget_usd: number; by_role: unknown[] }>(`/admin/costs${qs({ days })}`),
  adminJobs: (p: { status?: string; limit?: number }) => get<Job[]>(`/admin/jobs${qs(p)}`),

  // ---------------------------------------------------------------- qa
  ask: (ws: string, b: { question: string; document_ids?: string[]; scope?: string; mode?: "agent" | "strict"; as_of?: string }) =>
    post<AssistantResponse>(`/w/${ws}/ask`, b),
  feedback: (ws: string, b: { trace_id: string; rating: "up" | "down"; comment?: string }) => post<unknown>(`/w/${ws}/feedback`, b),
  sessions: (ws: string) => get<ChatSession[]>(`/w/${ws}/chat/sessions`),
  sessionMessages: (ws: string, sid: string) => get<SessionMessage[]>(`/w/${ws}/chat/sessions/${sid}/messages`),
  deleteSession: (ws: string, sid: string) => del<unknown>(`/w/${ws}/chat/sessions/${sid}`),
  answer: (ws: string, traceId: string) => get<AssistantResponse & { conflicts: unknown[] }>(`/w/${ws}/answers/${traceId}`),
  chunk: (ws: string, chunkId: string) => get<ChunkView>(`/w/${ws}/chunks/${encodeURIComponent(chunkId)}`),

  // ---------------------------------------------------------------- ingest / kb
  upload: (ws: string, files: File[], opts: Record<string, string | undefined>) => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    for (const [k, v] of Object.entries(opts)) if (v) fd.append(k, v);
    return request<{ batch_id: string; jobs: { job_id: string; document_id: string; filename: string; duplicate_of_existing: boolean; status: string }[] }>(
      `/w/${ws}/ingest`, { method: "POST", body: fd });
  },
  jobs: (ws: string, p: { status?: string; type?: string; batch_id?: string; document_id?: string; limit?: number }) => get<Job[]>(`/w/${ws}/ingest${qs(p)}`),
  job: (ws: string, id: string) => get<Job>(`/w/${ws}/ingest/${id}`),
  requeueJob: (ws: string, id: string) => post<Job>(`/w/${ws}/ingest/${id}/requeue`),
  health: (ws: string, days = 7) => get<KBHealth>(`/w/${ws}/kb/health${qs({ days })}`),
  documents: (ws: string, p: { status?: string; doc_type?: string; q?: string; include_deleted?: boolean; page?: number; page_size?: number }) =>
    get<Paged<Document>>(`/w/${ws}/documents${qs(p)}`),
  document: (ws: string, id: string) => get<DocumentDetail>(`/w/${ws}/documents/${id}`),
  updateDocument: (ws: string, id: string, b: Record<string, string | null>) => patch<Document>(`/w/${ws}/documents/${id}`, b),
  deleteDocument: (ws: string, id: string) => del<unknown>(`/w/${ws}/documents/${id}`),
  restoreDocument: (ws: string, id: string) => post<unknown>(`/w/${ws}/documents/${id}/restore`),
  approveDocument: (ws: string, id: string) => post<unknown>(`/w/${ws}/documents/${id}/approve`),
  reprocessDocument: (ws: string, id: string) => post<Job>(`/w/${ws}/documents/${id}/reprocess`),
  documentRecords: (ws: string, id: string, p: { page?: number; page_size?: number }) => get<Paged<KBRecord>>(`/w/${ws}/documents/${id}/records${qs(p)}`),
  records: (ws: string, p: Record<string, unknown>) => get<Paged<KBRecord>>(`/w/${ws}/records${qs(p)}`),
  reindex: (ws: string, b: { document_id?: string; doc_type?: string; rechunk?: string; force?: boolean }) => post<Job>(`/w/${ws}/kb/reindex`, b),
  conflicts: (ws: string, status = "open") => get<Conflict[]>(`/w/${ws}/conflicts${qs({ status })}`),
  resolveConflict: (ws: string, id: string, b: { action: string; keep_record_id?: string; note?: string }) => post<unknown>(`/w/${ws}/conflicts/${id}/resolve`, b),
  review: (ws: string, status = "pending") => get<ReviewItem[]>(`/w/${ws}/review${qs({ status })}`),
  approveReview: (ws: string, id: string, b: { edited_answer?: string; note?: string }) => post<ReviewItem>(`/w/${ws}/review/${id}/approve`, b),
  rejectReview: (ws: string, id: string, b: { note?: string }) => post<ReviewItem>(`/w/${ws}/review/${id}/reject`, b),

  // ---------------------------------------------------------------- analytics
  tables: (ws: string, documentId?: string) => get<TableInfo[]>(`/w/${ws}/tables${qs({ document_id: documentId })}`),
  table: (ws: string, id: string) => get<TableDetail>(`/w/${ws}/tables/${id}`),
  tableRows: (ws: string, id: string, p: { limit?: number; offset?: number }) => get<QueryResult>(`/w/${ws}/tables/${id}/rows${qs(p)}`),
  runSql: (ws: string, b: { sql: string; limit?: number; chart?: Record<string, unknown> }) => post<{ result: QueryResult; chart: Chart | null }>(`/w/${ws}/analytics/query`, b),
  askData: (ws: string, b: { question: string; document_ids?: string[] }) => post<AnalyticsAnswer>(`/w/${ws}/analytics/ask`, b),
  savedQueries: (ws: string) => get<SavedQuery[]>(`/w/${ws}/analytics/saved`),
  saveQuery: (ws: string, b: { title: string; sql: string; question?: string; chart_spec?: Record<string, unknown> }) => post<{ id: string }>(`/w/${ws}/analytics/saved`, b),
  runSaved: (ws: string, id: string) => post<{ result: QueryResult; chart: Chart | null }>(`/w/${ws}/analytics/saved/${id}/run`),
  deleteSaved: (ws: string, id: string) => del<unknown>(`/w/${ws}/analytics/saved/${id}`),
  exportTablePath: (ws: string, id: string, fmt: "csv" | "xlsx") => `/w/${ws}/tables/${id}/export?fmt=${fmt}`,
  exportQuery: async (ws: string, sql: string, fmt: "csv" | "xlsx") => {
    const token = auth.token();
    const res = await fetch(`${BASE}/w/${ws}/analytics/export?fmt=${fmt}`, {
      method: "POST", headers: { "Content-Type": "application/json", "Accept-Language": getLang(), ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify({ sql }),
    });
    if (!res.ok) throw await parseError(res);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `query.${fmt}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },

  // ---------------------------------------------------------------- knowledge graph
  entities: (ws: string, p: { q?: string; type?: string; document_id?: string; limit?: number; offset?: number }) => get<Paged<Entity>>(`/w/${ws}/entities${qs(p)}`),
  entity: (ws: string, id: string) => get<EntityDetail>(`/w/${ws}/entities/${id}`),
  updateEntity: (ws: string, id: string, b: { name?: string; type?: string; description?: string; aliases?: string[] }) => patch<Entity>(`/w/${ws}/entities/${id}`, b),
  deleteEntity: (ws: string, id: string) => del<unknown>(`/w/${ws}/entities/${id}`),
  mergeEntities: (ws: string, keepId: string, mergeIds: string[]) => post<Entity>(`/w/${ws}/entities/${keepId}/merge`, { merge_ids: mergeIds }),
  neighborhood: (ws: string, ids: string[], depth = 1, limit = 150) => post<Neighborhood>(`/w/${ws}/graph/neighborhood`, { entity_ids: ids, depth, limit }),
  graphOverview: (ws: string, top = 40) => get<GraphOverview>(`/w/${ws}/graph/overview${qs({ top })}`),

  // ---------------------------------------------------------------- reports
  reports: (ws: string) => get<Report[]>(`/w/${ws}/reports`),
  report: (ws: string, id: string) => get<Report>(`/w/${ws}/reports/${id}`),
  createReport: (ws: string, b: { title?: string; prompt?: string; description?: string }) => post<Report>(`/w/${ws}/reports`, b),
  updateReport: (ws: string, id: string, b: { title?: string; description?: string; blocks?: ReportBlock[] }) => patch<Report>(`/w/${ws}/reports/${id}`, b),
  deleteReport: (ws: string, id: string) => del<unknown>(`/w/${ws}/reports/${id}`),
  pinToReport: (ws: string, id: string, b: { trace_id: string; heading?: string; include_answer?: boolean; include_artifacts?: boolean }) => post<Report>(`/w/${ws}/reports/${id}/pin`, b),
  regenerateReport: (ws: string, id: string) => post<Report>(`/w/${ws}/reports/${id}/regenerate`),
  exportReportPath: (ws: string, id: string, fmt: string) => `/w/${ws}/reports/${id}/export?fmt=${fmt}`,

  // ---------------------------------------------------------------- data models
  models: (ws: string) => get<DataModel[]>(`/w/${ws}/models`),
  model: (ws: string, id: string) => get<DataModelDetail>(`/w/${ws}/models/${id}`),
  createModel: (ws: string, b: { document_ids: string[]; name?: string; description?: string }) => post<DataModel>(`/w/${ws}/models`, b),
  updateModel: (ws: string, id: string, b: { name?: string; description?: string }) => patch<DataModel>(`/w/${ws}/models/${id}`, b),
  deleteModel: (ws: string, id: string) => del<unknown>(`/w/${ws}/models/${id}`),
  reanalyzeModel: (ws: string, id: string, main_table?: string) => post<DataModel>(`/w/${ws}/models/${id}/reanalyze`, { main_table }),
  setModelParams: (ws: string, id: string, as_of_date: string | null) => request<DataModelDetail>(`/w/${ws}/models/${id}/parameters`, { method: "PUT", body: JSON.stringify({ as_of_date }) }),
  patchModelColumn: (ws: string, id: string, name: string, b: { rule?: RuleSpec; status?: string; note?: string; alternative?: number }) =>
    patch<DataModelDetail>(`/w/${ws}/models/${id}/columns/${encodeURIComponent(name)}`, b),
  testModelColumn: (ws: string, id: string, name: string, rule: RuleSpec) => post<RuleTest>(`/w/${ws}/models/${id}/columns/${encodeURIComponent(name)}/test`, { rule }),
  describeModelColumn: (ws: string, id: string, name: string, text: string) =>
    post<RuleTest & { rule: RuleSpec; why: string | null; new_relationships: string[] }>(`/w/${ws}/models/${id}/columns/${encodeURIComponent(name)}/describe`, { text }),
  confirmModel: (ws: string, id: string, columns?: string[]) => post<{ confirmed: number; model: DataModelDetail }>(`/w/${ws}/models/${id}/confirm`, { columns }),
  patchModelRelationship: (ws: string, id: string, rid: string, b: { status?: string; keys?: unknown[] }) => patch<DataModelDetail>(`/w/${ws}/models/${id}/relationships/${rid}`, b),
  addModelRelationship: (ws: string, id: string, b: { source_table: string; keys: unknown[] }) => post<DataModelDetail>(`/w/${ws}/models/${id}/relationships`, b),
  patchModelTable: (ws: string, id: string, key: string, role: "source" | "ignored") => patch<DataModelDetail>(`/w/${ws}/models/${id}/tables/${key}`, { role }),
  buildModel: (ws: string, id: string) => post<DataModel>(`/w/${ws}/models/${id}/build`),
  modelPreview: (ws: string, id: string, p: { limit?: number; offset?: number; live?: boolean }) =>
    get<{ columns: string[]; rows: unknown[][]; source: string; total: number }>(`/w/${ws}/models/${id}/preview${qs(p)}`),
  modelTableRows: (ws: string, id: string, key: string, p: { limit?: number; offset?: number }) =>
    get<{ columns: string[]; rows: unknown[][]; total: number }>(`/w/${ws}/models/${id}/tables/${key}/rows${qs(p)}`),
  modelDocumentPath: (ws: string, id: string, fmt: string, lang: string) => `/w/${ws}/models/${id}/document?fmt=${fmt}&lang=${lang}`,
  modelConnection: (ws: string, id: string) => get<ModelConnection>(`/w/${ws}/models/${id}/connection`),
  insights: (ws: string, days = 30) => get<Insights>(`/w/${ws}/insights${qs({ days })}`),

  // ---------------------------------------------------------------- ops
  abstentions: (ws: string, days = 7) => get<{ trace_id: string; question: string; reason: string; top_score: number | null; created_at: string }[]>(`/w/${ws}/ops/abstentions${qs({ days })}`),
  opsFeedback: (ws: string, p: { rating?: string; status?: string; days?: number }) => get<Record<string, unknown>[]>(`/w/${ws}/ops/feedback${qs(p)}`),
  updateFeedback: (ws: string, id: string, b: { status: string; admin_note?: string }) => patch<unknown>(`/w/${ws}/ops/feedback/${id}`, b),
  traces: (ws: string, p: { q?: string; status?: string; user_id?: string; limit?: number }) => get<TraceSummary[]>(`/w/${ws}/ops/traces${qs(p)}`),
  trace: (ws: string, id: string) => get<Record<string, unknown>>(`/w/${ws}/ops/traces/${id}`),
  costs: (ws: string, days = 30) => get<{ days: number; by_day: { day: string; requests: number; cost_usd: number }[]; [k: string]: unknown }>(`/w/${ws}/ops/costs${qs({ days })}`),
  aliases: (ws: string) => get<{ id: string; kind: string; alias: string; canonical: string; note: string | null }[]>(`/w/${ws}/graph/aliases`),
  addAlias: (ws: string, b: { kind: string; alias: string; canonical: string; note?: string }) => post<unknown>(`/w/${ws}/graph/aliases`, b),
  deleteAlias: (ws: string, id: string) => del<unknown>(`/w/${ws}/graph/aliases/${id}`),
  ruleStats: (ws: string) => get<Record<string, unknown>>(`/w/${ws}/graph/rule-stats`),
  rebuildGraph: (ws: string, llm: boolean) => post<Job>(`/w/${ws}/graph/rebuild`, { llm }),
};

// ---------------------------------------------------------------- SSE chat
export type ChatEvent =
  | { event: "meta"; data: { trace_id: string; session_id: string } }
  | { event: "status"; data: { tool: string; message: string; args: Record<string, unknown> } }
  | { event: "artifact"; data: Artifact }
  | { event: "delta"; data: { text: string } }
  | { event: "replace"; data: { text: string } }
  | { event: "pending_review"; data: { review_id: string; message: string; message_id: number } }
  | { event: "citations"; data: { citations: Citation[] } }
  | { event: "conflicts"; data: { conflicts: unknown[] } }
  | { event: "trace"; data: { decision_trace: DecisionTrace; confidence: Confidence; trace_url: string | null; intent: string | null } }
  | { event: "error"; data: { message: string; code?: string; trace_id?: string } }
  | { event: "done"; data: { status: string; trace_id: string; answer?: string; review_id?: string | null; session_id?: string; message_id?: number; artifacts?: Artifact[] } };

export interface ChatParams {
  question: string;
  session_id?: string | null;
  document_ids?: string[];
  scope?: string;
  mode?: "agent" | "strict";
}

/** POST /w/{ws}/chat và đọc luồng SSE; gọi onEvent cho mỗi event. Trả về hàm abort. */
export function streamChat(ws: string, params: ChatParams, onEvent: (e: ChatEvent) => void, onEnd: (err?: Error) => void): () => void {
  const ctrl = new AbortController();
  const token = auth.token();
  (async () => {
    try {
      const res = await fetch(`${BASE}/w/${ws}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", "Accept-Language": getLang(), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ question: params.question, session_id: params.session_id || undefined, document_ids: params.document_ids?.length ? params.document_ids : undefined, scope: params.scope || undefined, mode: params.mode || "agent" }),
        signal: ctrl.signal,
      });
      if (!res.ok) throw await parseError(res);
      if (!res.body) throw new Error(translate("chat.noStream"));
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let ev = "message";
          const dataLines: string[] = [];
          for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) ev = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
          }
          if (!dataLines.length) continue; // heartbeat/comment
          try {
            onEvent({ event: ev, data: JSON.parse(dataLines.join("\n")) } as ChatEvent);
          } catch {
            /* bỏ event hỏng */
          }
        }
      }
      onEnd();
    } catch (e) {
      if ((e as Error).name === "AbortError") onEnd();
      else onEnd(e as Error);
    }
  })();
  return () => ctrl.abort();
}
