// Kiểu dữ liệu khớp với JSON API (api/routes/*). Giữ tối giản: chỉ các trường UI dùng.

export type Role = "viewer" | "editor" | "owner";

export interface User {
  id: string;
  email: string;
  name: string;
  is_superadmin: boolean;
  locale: string | null;
  created_at: string;
  last_login_at: string | null;
  is_active: boolean;
}

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  domain_pack: string;
  domain_pack_name: string;
  settings: Record<string, unknown>;
  owner_id: string;
  created_at: string;
  role: Role | null;
  attributes: Record<string, unknown>;
  member_attributes: { key: string; label: string; description: string }[];
  suggested_questions: string[];
  member_count?: number;
  archived_at?: string | null;
}

export interface DomainPack {
  key: string;
  name: string;
  description: string;
  rule_graph: boolean;
  doc_types?: string[];
}

export interface Job {
  job_id: string;
  type: string;
  status: "queued" | "processing" | "done" | "blocked" | "error" | string;
  document_id: string | null;
  workspace_id: string | null;
  batch_id: string | null;
  attempts: number;
  max_attempts: number;
  progress: { done: number; total: number; percent: number | null };
  error: string | null;
  result: Record<string, unknown> | null;
  payload: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Document {
  document_id: string;
  filename: string;
  title: string;
  series_key: string | null;
  source_type: string;
  doc_type: string;
  scope: string | null;
  effective_date: string | null;
  valid_to: string | null;
  version: string | null;
  status: string;
  is_active: boolean;
  replace_mode: string;
  supersedes_id: string | null;
  superseded_by_id: string | null;
  record_count: number;
  table_count: number;
  size_bytes: number;
  uploaded_by: string | null;
  summary: string | null;
  semantic_profile: SemanticProfile;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  metadata: Record<string, unknown>;
}

export interface SemanticProfile {
  kind?: string;
  language?: string;
  topics?: string[];
  key_entities?: { name: string; type: string }[];
  suggested_questions?: string[];
  audience?: string | null;
  structure?: string | null;
  sensitivity?: string;
  quality_notes?: string | null;
  time_range?: { from?: string | null; to?: string | null; note?: string | null } | null;
  stats?: Record<string, unknown>;
  generated_by?: string;
  [k: string]: unknown;
}

export interface DocumentDetail extends Document {
  jobs: Job[];
  record_count_live: number;
  tables: { id: string; name: string; title: string; row_count: number; columns: TableColumn[]; description: string | null; source_location: string | null }[];
  entities: { id: string; type: string; name: string; mentions: number; description: string | null }[];
}

export interface KBRecord {
  record_id: string;
  document_id: string;
  source_file: string;
  source_type: string;
  doc_type: string;
  location: string;
  content: string;
  scope: string | null;
  skus: string[];
  category: string | null;
  effective_date: string | null;
  version: string | null;
  status: string;
  is_active: boolean;
  confidence: number | null;
  suspicious_content: boolean;
  citation: string | null;
  updated_at: string;
}

export interface Paged<T> {
  total: number;
  page?: number;
  page_size?: number;
  items: T[];
}

export interface Citation {
  n: number;
  sid: string;
  label: string;
  chunk_id: string | null;
  record_id: string | null;
  document_id: string | null;
  location: string | null;
  snippet: string | null;
  effective_date: string | null;
  url: string;
}

export interface ChunkView {
  chunk_id: string;
  text: string;
  parent_text: string | null;
  record_content: string | null;
  record_id: string | null;
  location: string;
  role: string;
  citation: string | null;
  document: { document_id: string; title: string; filename: string; doc_type: string; effective_date: string | null; status: string; source_type: string } | null;
  effective_date: string | null;
}

export interface Chart {
  type: "bar" | "line" | "pie" | "scatter" | "kpi" | "table" | string;
  title?: string;
  encoding?: Record<string, unknown>;
  vega_lite?: Record<string, unknown>;
  kpi?: { label: string; value: unknown };
}

export interface TableColumn {
  name: string;
  type: string;
  description?: string;
  examples?: unknown[];
  nullable?: boolean;
  original?: string;
}

export interface QueryResult {
  columns: TableColumn[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  sql: string;
}

export interface QueryArtifact {
  type: "query_result";
  sid: string;
  sql: string;
  purpose: string;
  columns: TableColumn[];
  rows: unknown[][];
  row_count: number;
  truncated?: boolean;
  chart: Chart | null;
  tables?: string[];
  citation?: string;
}

export type Artifact = QueryArtifact | { type: string; [k: string]: unknown };

export interface Confidence {
  level: string;
  score: number | null;
  explanation: string;
}

export interface DecisionTrace {
  understood: Record<string, unknown>;
  searched: { label: string; result?: string; tool?: string; [k: string]: unknown }[];
  found: unknown[];
  graph_paths: unknown[];
  computed_facts: unknown[];
  basis: string[];
  conflicts: unknown[];
  confidence: Confidence;
  degraded: string[];
  outcome: string;
  summary: string[];
}

export type AnswerStatus = "answered" | "answered_general" | "abstained" | "no_citation" | "pending_review" | "error";

export interface AssistantResponse {
  trace_id: string;
  status: AnswerStatus;
  answer: string;
  citations: Citation[];
  artifacts: Artifact[];
  decision_trace: DecisionTrace;
  intent: string | null;
  confidence: Confidence;
  review_id: string | null;
  session_id: string | null;
  message_id: number | null;
  trace_url: string | null;
}

export interface ChatSession {
  session_id: string;
  title: string | null;
  scope: string | null;
  updated_at: string;
  created_at: string;
}

export interface SessionMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: string | null;
  trace_id: string | null;
  citations: Citation[];
  artifacts: Artifact[];
  created_at: string;
}

export interface TableInfo {
  id: string;
  name: string;
  title: string;
  document_id: string;
  document_title: string;
  source_location: string | null;
  columns: TableColumn[];
  row_count: number;
  description: string | null;
  suggested_questions: string[];
}

export interface TableLinkSide {
  table_id: string;
  table: string | null;
  title: string | null;
  document_id: string | null;
  column: string;
  label: string;
  examples: string[];
  transforms: string[];
}

/** Quan hệ giữa 2 bảng (khác file/sheet) dò bằng dữ liệu: left = phía tham chiếu, right = bảng danh mục. */
export interface TableLink {
  id: string;
  left: TableLinkSide;
  right: TableLinkSide;
  cardinality: "one_to_one" | "many_to_one" | "many_to_many" | string;
  matched_keys: number;
  left_coverage: number;
  right_coverage: number;
  confidence: number;
  status: "proposed" | "approved" | "rejected" | string;
  reviewed_by: string | null;
  updated_at: string;
}

export interface TableDetail extends TableInfo {
  stats: Record<string, Record<string, unknown>>;
  preview: QueryResult;
}

export interface AnalyticsAnswer {
  sql: string | null;
  result: QueryResult | null;
  chart: Chart | null;
  reason: string;
  assumptions: string[];
  attempts: number;
  error: string | null;
  tables_used: string[];
}

export interface SavedQuery {
  id: string;
  title: string;
  sql: string;
  question: string | null;
  chart_spec: Record<string, unknown> | null;
  created_by?: string;
  created_at?: string;
}

export interface Entity {
  id: string;
  type: string;
  name: string;
  aliases: string[];
  description: string | null;
  mention_count: number;
  document_count: number;
  properties: Record<string, unknown>;
  is_seed?: boolean;
}

export interface Relation {
  id: string;
  type: string;
  label: string | null;
  evidence: string | null;
  confidence: number | null;
  document_id: string | null;
  record_id: string | null;
  entity: Entity;
}

export interface EntityDetail extends Entity {
  mentions: { id: number; document_id: string; document_title: string; record_id: string | null; location: string | null; citation: string; snippet: string | null }[];
  relations_out: Relation[];
  relations_in: Relation[];
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string | null;
  confidence: number | null;
  document_id: string | null;
  record_id: string | null;
  evidence?: string | null;
}

export interface GraphOverview {
  entity_count: number;
  relation_count: number;
  types: { type: string; count: number }[];
  relation_types: { type: string; count: number }[];
  nodes: Entity[];
  edges: GraphEdge[];
}

export interface Neighborhood {
  seeds: string[];
  nodes: Entity[];
  edges: GraphEdge[];
}

export type ReportBlockType = "heading" | "markdown" | "chart" | "table" | "kpi" | "sources";

export interface ReportBlock {
  id: string;
  type: ReportBlockType;
  text?: string;
  level?: number;
  title?: string;
  chart?: Chart;
  kpi?: { label: string; value: unknown };
  columns?: TableColumn[];
  rows?: unknown[][];
  sql?: string;
  citations?: Citation[];
  citation?: string;
  trace_id?: string;
  question?: string;
  items?: { label: string; effective_date?: string | null }[];
}

export interface Report {
  id: string;
  title: string;
  description: string | null;
  status: "draft" | "generating" | "ready" | "error";
  prompt: string | null;
  blocks: ReportBlock[] | null;
  block_count: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  job: Job | null;
}

export interface KBHealth {
  generated_at: string;
  config_version: string;
  kb_version: number | string;
  totals: { documents: number; data_tables: number; entities: number };
  records_by_doc_type: Record<string, Record<string, number>>;
  active_documents_by_doc_type: Record<string, number>;
  last_updated_by_doc_type: Record<string, string | null>;
  blocked_files: { count: number; items: { document_id: string; filename: string; updated_at: string; reason: string | null }[] };
  failed_ingest_jobs: { job_id: string; document_id: string | null; status: string; error: string }[];
  open_conflicts: number;
  abstentions_last_days: { days: number; count: number; questions_total: number; rate: number | null; questions: { question: string; count: number; reasons: Record<string, number>; last_at: string }[] };
  embedding_coverage: { model: string; retrievable_chunks: number; embedded: number; missing: number };
  jobs: Record<string, number>;
  graph: Record<string, unknown>;
}

export interface ReviewItem {
  id: string;
  trace_id: string;
  session_id: string | null;
  user_id: string;
  scope: string | null;
  question: string;
  draft_answer: string;
  draft_citations: Citation[];
  decision_trace: DecisionTrace;
  reason: string;
  status: string;
  final_answer: string | null;
  overridden: boolean;
  reviewer_id: string | null;
  review_note: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export interface Conflict {
  id: string;
  kind: string;
  entity_key: string;
  record_ids: string[];
  details: Record<string, unknown>;
  status: string;
  resolution: string | null;
  resolved_by: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface Member {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  attributes: Record<string, unknown>;
  joined_at: string;
}

export interface Invite {
  invite_id: string;
  email: string;
  role: Role;
  attributes: Record<string, unknown>;
  created_at: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  role: string;
  rate_limit_per_minute: number | null;
  active?: boolean;
  created_at?: string;
  last_used_at?: string | null;
}

export interface TraceSummary {
  trace_id: string;
  created_at: string;
  user_id: string;
  scope: string | null;
  question: string;
  status: string;
  intent?: string | null;
  route?: string | null;
  cost_usd?: number | null;
  latency_ms?: number | null;
  langfuse_url?: string | null;
  [k: string]: unknown;
}


// ---------------------------------------------------------------- data models (modeling/spec.py)
export type Text2 = { en: string; vi: string };
export type RuleKind = "input" | "lookup" | "aggregate" | "formula" | "constant" | "unresolved";
export interface ConditionSpec { column: string; op: string; value?: unknown; as_of_offset_days?: number | null }
export interface RuleSpec {
  kind: RuleKind; relationship_id?: string | null; table?: string | null; column?: string | null; func?: string | null;
  filters?: ConditionSpec[]; expression?: string | null; value?: unknown; default?: unknown; round_digits?: number | null;
}
export interface ColumnCheckSpec { compared: number; matched: number; rate: number | null; expected_nulls: number; samples: { row: number; expected: unknown; actual: unknown }[]; basis?: "view" | "isolated"; waiting_on?: string[] }
export interface EvidenceView { kind: string; text: Text2; detail: Record<string, unknown>; note: string | null }
export interface CandidateSpec { rule: RuleSpec; check: ColumnCheckSpec | null; evidence: { kind: string; detail: Record<string, unknown>; note: string | null }[]; confidence: number }
export interface OutputColumnSpec {
  name: string; label: string; group: string | null; type: string; is_key: boolean; rule: RuleSpec; status: "proposed" | "confirmed" | "rejected";
  confidence: number; check: ColumnCheckSpec | null; alternatives: CandidateSpec[]; excel_formula: string | null; note: string | null; problem: string | null;
}
export interface SheetColumnSpec { name: string; label: string; group: string | null; type: string; letter: string | null; examples: string[]; distinct: number; nulls: number; formula_count: number; formula: string | null }
export interface PivotSpec { name: string; sheet: string; location: string; source_sheet: string | null; rows: string[]; values: { field: string; func: string; label: string }[]; filters: { field: string; selected: string[] | null; excluded: string[] }[] }
export interface TableSpecView {
  key: string; title: string; document_id: string | null; filename: string | null; sheet: string | null; role: "main" | "source" | "ignored";
  columns: SheetColumnSpec[]; row_count: number; header_row: number | null; data_range: string | null; formula_count: number; pivots: PivotSpec[]; notes: string[]; main_score: number;
}
export interface TransformSpec { op: string; args: unknown[] }
export interface KeyPairSpec { base_column: string; source_column: string; base_transforms: TransformSpec[]; source_transforms: TransformSpec[] }
export interface RelationshipSpec {
  id: string; source_table: string; target_table: string; keys: KeyPairSpec[]; cardinality: "one_to_one" | "many_to_one"; coverage: number | null;
  source_match: number | null; matched_keys: number | null; confidence: number; status: "proposed" | "confirmed" | "rejected";
}
export interface ModelSpecView {
  tables: TableSpecView[]; main_table: string | null; view_name: string; relationships: RelationshipSpec[]; columns: OutputColumnSpec[];
  parameters: { as_of_date: string | null; as_of_source: string | null }; analyzed_at: string | null;
}
export interface ModelCounts { tables: number; sources: number; relationships: number; columns: number; calculated: number; confirmed: number; attention: number; unresolved: number; matching: number }
export interface DataModel {
  id: string; name: string; description: string | null; status: string; document_ids: string[]; schema_name: string; view_name: string; error: string | null;
  revision: number; built_revision: number | null; stale: boolean; created_at: string; updated_at: string; built_at: string | null; created_by: string;
  job: Job | null; counts: ModelCounts | null; main_title: string | null;
  validation_summary: { score: number | null; calculated?: number; matched?: number; partial?: number; mismatch?: number; not_calculated?: number; inputs?: number; rows_aligned?: boolean; view_rows?: number; excel_rows?: number } | null;
}
export interface ValidationColumn extends Partial<ColumnCheckSpec> { kind: string }
export interface DataModelDetail extends DataModel {
  documents: { id: string; title: string; filename: string }[];
  spec: ModelSpecView;
  validation: { validated_at?: string; excel_rows?: number; view_rows?: number; rows_aligned?: boolean; score?: number | null; summary?: Record<string, number>; columns?: Record<string, ValidationColumn> };
  texts: {
    columns: Record<string, { rule: Text2; formula: string; evidence: EvidenceView[]; attention: boolean; alternatives: { rule: Text2; formula: string; evidence: EvidenceView[] }[] }>;
    relationships: Record<string, { text: Text2; keys: Text2; evidence: EvidenceView[]; used_by: string[] }>;
    notices: { code: string; level: string; en: string; vi: string }[];
  };
  build: { sql: string | null; problems: Record<string, string> | null; view_rows: number | null; built_at: string | null; data_table_id: string | null; revision: number | null; table_rows: Record<string, number> | null };
}
export interface RuleTest { check: ColumnCheckSpec | null; error?: string | null; text: Text2; formula: string }
export interface ModelConnection { dsn: string | null; schema: string; view: string; qualified: string; reader_role: string | null; built: boolean; tables: { key: string; title: string; role: string }[]; sample_sql: string }

// ---------------------------------------------------------------- insights
export interface Insights {
  days: number;
  knowledge: { documents: number; by_source_type: Record<string, number>; by_status: Record<string, number>; records: number; chunks: number; embedded_chunks: number; tables: number; table_rows: number; entities: number; relations: number; models: number; models_ready: number; media_minutes: number; superseded: number };
  answers: {
    questions: number; by_status: Record<string, number>; grounded_rate: number | null; abstain_rate: number | null; latency_p50_ms: number | null; latency_p95_ms: number | null;
    avg_cost_usd: number | null; avg_citations: number | null; tools: Record<string, number>; skills: Record<string, number>; multi_skill: number;
    recent: { trace_id: string; question: string; status: string; latency_ms: number | null; created_at: string; citations: number; confidence: number | null; skills: string[] }[];
  };
  trust: { pending_reviews: number; reviewed: number; open_conflicts: number; resolved_conflicts: number; feedback_up: number; feedback_down: number; suspicious_records: number; blocked_files: number; members_by_role: Record<string, number>; api_keys: number };
  safeguards: Record<string, boolean>;
}
