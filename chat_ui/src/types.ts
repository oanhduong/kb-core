export interface Citation {
  n: number;
  sid: string;
  label: string;
  chunk_id: string;
  record_id: string;
  document_id: string;
  location: string;
  snippet: string;
  effective_date: string | null;
  url: string;
}

export interface ConflictValue {
  value: number;
  display: string;
  source: string;
  effective_date: string | null;
  is_newest: boolean;
}

export interface ConflictInfo {
  kind: string;
  entity_key: string;
  values: ConflictValue[];
}

export interface DecisionTrace {
  summary?: string[];
  understood?: Record<string, any>;
  searched?: Record<string, any>[];
  found?: Record<string, any>[];
  graph_paths?: string[];
  computed_facts?: string[];
  basis?: string[];
  degraded?: string[];
  confidence?: { level: string; score: number | null; explanation?: string };
}

export type MsgStatus = "streaming" | "answered" | "pending_review" | "abstained" | "no_citation" | "error" | "rejected" | "final";

export interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  text: string;
  status: MsgStatus;
  traceId?: string;
  citations: Citation[];
  conflicts: ConflictInfo[];
  trace?: DecisionTrace;
  confidence?: { level: string; score: number | null };
  reviewId?: string | null;
  rating?: "up" | "down";
}

export interface AnswerStatus {
  trace_id: string;
  status: string;
  answer: string | null;
  citations: Citation[];
  conflicts: ConflictInfo[];
  decision_trace: DecisionTrace;
}

export interface ChunkView {
  chunk_id: string;
  text: string;
  parent_text: string | null;
  record_content: string | null;
  citation: string | null;
  location: string;
  is_active: boolean;
  effective_date: string | null;
  valid_to: string | null;
  document: { title: string; filename: string; doc_type: string; effective_date: string | null; status: string } | null;
}

export interface SessionMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: string;
  trace_id: string | null;
  citations: Citation[];
}
