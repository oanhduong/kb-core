// Ngăn kéo duyệt 1 cột: logic hiện tại (câu nghiệp vụ + khớp Excel + dòng lệch), gợi ý khác, mô tả bằng lời để AI dịch,
// hoặc tự dựng quy tắc bằng form có hướng dẫn (thử ngay trên dữ liệu trước khi lưu).
import { useMutation } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, ChevronDown, Plus, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Drawer, Field, Spinner, useToast } from "@/components/ui";
import { useI18n } from "@/i18n";
import { api } from "@/lib/api";
import type { ConditionSpec, DataModelDetail, OutputColumnSpec, RuleKind, RuleSpec, RuleTest } from "@/lib/types";
import { Advanced } from "@/state/prefs";
import { useWorkspace } from "@/state/auth";
import { CheckLine, EvidenceChips, KIND_ICON, KindTag, SamplesTable, typeLabel } from "./shared";

const FUNCS = ["count", "count_distinct", "sum", "avg", "min", "max"];
const OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "not_null", "contains"];

export function ColumnEditor({ model, column, onClose, onSaved, canEdit }: {
  model: DataModelDetail; column: OutputColumnSpec | null; onClose: () => void; onSaved: (m: DataModelDetail) => void; canEdit: boolean;
}) {
  const { t, pick } = useI18n();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const [words, setWords] = useState("");
  const [proposal, setProposal] = useState<(RuleTest & { rule: RuleSpec; why: string | null }) | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => { setWords(""); setProposal(null); setFormOpen(false); setNote(column?.note || ""); }, [column?.name, column?.note]);
  const label = (n: string) => model.spec.columns.find((c) => c.name === n)?.label || n;

  const patch = useMutation({
    mutationFn: (b: { rule?: RuleSpec; status?: string; note?: string; alternative?: number }) => api.patchModelColumn(wsId, model.id, column!.name, b),
    onSuccess: (m) => { onSaved(m); toast.ok(t("models.editor.saved")); },
    onError: toast.err,
  });
  const describe = useMutation({
    mutationFn: () => api.describeModelColumn(wsId, model.id, column!.name, words.trim()),
    onSuccess: setProposal,
    onError: toast.err,
  });
  if (!column) return <Drawer open={false} onClose={onClose} title="">{null}</Drawer>;
  const tx = model.texts.columns[column.name];
  const busy = patch.isPending;

  return (
    <Drawer open={!!column} onClose={onClose} width="max-w-2xl" title={column.label}>
      <div className="mb-5 flex flex-wrap items-center gap-2 text-sm text-ink-600">
        {column.group && <span className="chip bg-ink-100/70 text-ink-700">{column.group}</span>}
        <span>{typeLabel(column.type, t)}</span>
        {column.status === "confirmed" && <span className="chip bg-verify-50 text-verify-700"><Check className="h-3 w-3" />{t("models.status.confirmed")}</span>}
        {column.status === "rejected" && <span className="chip bg-alert-50 text-alert-700">{t("models.status.rejected")}</span>}
      </div>

      {/* hiện tại */}
      <div className="panel mb-6 p-5">
        <div className="mb-2 flex items-center justify-between gap-3"><KindTag kind={column.rule.kind} /><CheckLine check={column.check} columnLabel={label} /></div>
        <p className="text-[15px] leading-7 text-ink-900">{pick(tx?.rule)}</p>
        {column.problem && <p className="mt-2 rounded-lg bg-alert-50 px-3 py-2 text-sm text-alert-700">{t("models.editor.problem")}: {column.problem}</p>}
        <div className="mt-3"><EvidenceChips items={tx?.evidence || []} limit={6} /></div>
        {column.check && (column.check.rate ?? 1) < 1 && column.check.samples.length > 0 && (
          <div className="mt-4"><div className="mb-1.5 text-[13px] font-medium text-ink-700">{t("models.editor.samples")}</div><SamplesTable check={column.check} type={column.type} /></div>
        )}
        <Advanced>
          <div className="mt-4 space-y-1 rounded-lg bg-ink-950 p-3 font-mono text-xs text-ink-100">
            <div>{tx?.formula}</div>
            {column.excel_formula && <div className="text-marker-200">Excel: {column.excel_formula}</div>}
            <div className="text-ink-400">{column.name} · {column.type} · confidence {Math.round(column.confidence * 100)}%</div>
          </div>
        </Advanced>
        {canEdit && (
          <div className="mt-5 flex flex-wrap gap-2">
            {column.status !== "confirmed" && column.rule.kind !== "unresolved" && (
              <button className="btn-primary" disabled={busy} onClick={() => patch.mutate({ status: "confirmed" })}><Check className="h-4 w-4" />{t("models.editor.approve")}</button>
            )}
            {column.rule.kind !== "input" && (
              <button className="btn-secondary" disabled={busy} onClick={() => patch.mutate({ rule: { kind: "input" } })} title={t("models.editor.markInputHint")}>{t("models.editor.markInput")}</button>
            )}
            {column.status !== "rejected" && column.rule.kind !== "input" && (
              <button className="btn-ghost text-alert-700" disabled={busy} onClick={() => patch.mutate({ status: "rejected" })}><X className="h-4 w-4" />{t("models.editor.reject")}</button>
            )}
            {busy && <Spinner />}
          </div>
        )}
      </div>

      {/* gợi ý khác */}
      {column.alternatives.length > 0 && (
        <section className="mb-6">
          <h4 className="mb-2 font-display text-base font-semibold">{t("models.editor.alternatives")}</h4>
          <ul className="space-y-2">
            {column.alternatives.map((a, i) => (
              <li key={i} className="card flex items-start gap-3 p-4">
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-center gap-3"><KindTag kind={a.rule.kind} /><CheckLine check={a.check} columnLabel={label} /></div>
                  <p className="text-sm leading-6 text-ink-800">{pick(tx?.alternatives[i]?.rule)}</p>
                  <div className="mt-1.5"><EvidenceChips items={tx?.alternatives[i]?.evidence || []} /></div>
                </div>
                {canEdit && <button className="btn-secondary btn-sm shrink-0" disabled={busy} onClick={() => patch.mutate({ alternative: i })}>{t("models.editor.useThis")}</button>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {canEdit && (
        <>
          {/* mô tả bằng lời */}
          <section className="mb-6">
            <h4 className="mb-1 font-display text-base font-semibold">{t("models.editor.words")}</h4>
            <p className="mb-2 text-sm text-ink-600">{t("models.editor.wordsHint")}</p>
            <textarea className="input min-h-[84px]" value={words} onChange={(e) => setWords(e.target.value)} placeholder={t("models.editor.wordsPh")} />
            <div className="mt-2 flex justify-end">
              <button className="btn-ink" disabled={words.trim().length < 3 || describe.isPending} onClick={() => describe.mutate()}>
                {describe.isPending ? <Spinner className="text-white" /> : <Sparkles className="h-4 w-4 text-marker-200" />}{t("models.editor.translate")}
              </button>
            </div>
            {proposal && (
              <div className="mt-3 rounded-xl border border-brand-200 bg-brand-50/50 p-4">
                <div className="mb-1 flex items-center gap-3"><KindTag kind={proposal.rule.kind} /><CheckLine check={proposal.check} columnLabel={label} /></div>
                <p className="text-sm leading-6 text-ink-900">{pick(proposal.text)}</p>
                {proposal.check && (proposal.check.rate ?? 1) < 1 && <div className="mt-3"><SamplesTable check={proposal.check} type={column.type} /></div>}
                <div className="mt-3 flex gap-2">
                  <button className="btn-primary btn-sm" disabled={busy} onClick={() => patch.mutate({ rule: proposal.rule })}>{t("models.editor.useRule")}</button>
                  <button className="btn-ghost btn-sm" onClick={() => setProposal(null)}>{t("action.cancel")}</button>
                </div>
              </div>
            )}
          </section>

          {/* tự dựng */}
          <section className="mb-6">
            <button className="flex w-full items-center justify-between rounded-xl px-1 py-2 text-left" onClick={() => setFormOpen((v) => !v)} aria-expanded={formOpen}>
              <span><span className="block font-display text-base font-semibold">{t("models.editor.manual")}</span><span className="block text-sm text-ink-600">{t("models.editor.manualHint")}</span></span>
              <ChevronDown className={clsx("h-5 w-5 text-ink-400 transition", formOpen && "rotate-180")} />
            </button>
            {formOpen && <RuleForm model={model} column={column} onSave={(rule) => patch.mutate({ rule })} saving={busy} />}
          </section>

          <section>
            <Field label={t("models.editor.note")} hint={t("models.editor.noteHint")}>
              <textarea className="input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} onBlur={() => note !== (column.note || "") && patch.mutate({ note })} />
            </Field>
          </section>
        </>
      )}
    </Drawer>
  );
}

function RuleForm({ model, column, onSave, saving }: { model: DataModelDetail; column: OutputColumnSpec; onSave: (r: RuleSpec) => void; saving: boolean }) {
  const { t, pick } = useI18n();
  const { wsId } = useWorkspace();
  const spec = model.spec;
  const rels = spec.relationships.filter((r) => r.status !== "rejected");
  const [rule, setRule] = useState<RuleSpec>(() => (column.rule.kind === "unresolved" ? { kind: "aggregate", relationship_id: rels[0]?.id, func: "count", filters: [], default: 0 } : { ...column.rule, filters: column.rule.filters || [] }));
  const [result, setResult] = useState<RuleTest | null>(null);
  const rel = rels.find((r) => r.id === rule.relationship_id);
  const src = spec.tables.find((x) => x.key === rel?.source_table);
  const main = spec.tables.find((x) => x.key === spec.main_table);
  const others = spec.columns.filter((c) => c.name !== column.name);
  const set = (p: Partial<RuleSpec>) => { setRule((r) => ({ ...r, ...p })); setResult(null); };
  const test = useMutation({ mutationFn: () => api.testModelColumn(wsId, model.id, column.name, clean(rule)), onSuccess: setResult, onError: (e) => setResult({ check: null, error: (e as Error).message, text: { en: "", vi: "" }, formula: "" }) });
  const kinds: RuleKind[] = ["lookup", "aggregate", "formula", "input", "constant"];
  const relLabel = (id: string) => {
    const r = rels.find((x) => x.id === id);
    const s = spec.tables.find((x) => x.key === r?.source_table);
    return `${s?.title || r?.source_table} — ${pick(model.texts.relationships[id]?.keys)}`;
  };
  const srcCols = src?.columns || [];
  const colType = (n: string) => srcCols.find((c) => c.name === n)?.type || "text";

  return (
    <div className="well mt-2 space-y-4 p-4">
      <div className="grid gap-2 sm:grid-cols-5">
        {kinds.map((k) => {
          const Icon = KIND_ICON[k];
          return (
            <button key={k} type="button" onClick={() => set({ kind: k, func: k === "aggregate" ? rule.func || "count" : k === "lookup" ? "first" : null })}
              className={clsx("flex flex-col items-start gap-1 rounded-xl border p-2.5 text-left text-xs transition", rule.kind === k ? "border-ink-900 bg-white shadow-card" : "border-transparent bg-white/60 hover:bg-white")}>
              <Icon className="h-4 w-4 text-ink-600" /><span className="font-semibold text-ink-900">{t(`models.kind.${k}`)}</span>
            </button>
          );
        })}
      </div>
      <p className="text-xs text-ink-600">{t(`models.kind.${rule.kind}.hint`)}</p>

      {(rule.kind === "lookup" || rule.kind === "aggregate") && (
        <>
          <Field label={t("models.form.connection")}>
            <select className="input" value={rule.relationship_id || ""} onChange={(e) => set({ relationship_id: e.target.value, column: null, filters: [] })}>
              {rels.map((r) => <option key={r.id} value={r.id}>{relLabel(r.id)}</option>)}
            </select>
          </Field>
          {rule.kind === "aggregate" && (
            <Field label={t("models.form.operation")}>
              <select className="input" value={rule.func || "count"} onChange={(e) => set({ func: e.target.value, default: ["count", "sum", "count_distinct"].includes(e.target.value) ? 0 : null })}>
                {FUNCS.map((f) => <option key={f} value={f}>{t(`models.func.${f}`)}</option>)}
              </select>
            </Field>
          )}
          {(rule.kind === "lookup" || rule.func !== "count") && (
            <Field label={t("models.form.column")}>
              <select className="input" value={rule.column || ""} onChange={(e) => set({ column: e.target.value })}>
                <option value="">—</option>
                {srcCols.map((c) => <option key={c.name} value={c.name}>{c.label} ({typeLabel(c.type, t)})</option>)}
              </select>
            </Field>
          )}
          {rule.kind === "aggregate" && (
            <div>
              <div className="label">{t("models.form.conditions")}</div>
              <div className="space-y-2">
                {(rule.filters || []).map((f, i) => (
                  <ConditionRow key={i} cond={f} columns={srcCols} type={colType(f.column)}
                    onChange={(c) => set({ filters: (rule.filters || []).map((x, j) => (j === i ? c : x)) })}
                    onRemove={() => set({ filters: (rule.filters || []).filter((_, j) => j !== i) })} />
                ))}
                <button type="button" className="btn-ghost btn-sm" onClick={() => set({ filters: [...(rule.filters || []), { column: srcCols[0]?.name || "", op: "eq", value: "" }] })}>
                  <Plus className="h-3.5 w-3.5" />{t("models.form.addCondition")}
                </button>
              </div>
            </div>
          )}
          <Field label={t("models.form.default")} hint={t("models.form.defaultHint")}>
            <input className="input" value={rule.default === null || rule.default === undefined ? "" : String(rule.default)}
              onChange={(e) => set({ default: e.target.value === "" ? null : Number.isNaN(Number(e.target.value)) ? e.target.value : Number(e.target.value) })} />
          </Field>
        </>
      )}

      {rule.kind === "formula" && (
        <div>
          <Field label={t("models.form.expression")} hint={t("models.form.expressionHint")}>
            <textarea className="input font-mono text-[13px]" rows={3} value={rule.expression || ""} onChange={(e) => set({ expression: e.target.value })} placeholder="DAYS(AS_OF(), [Last IMV])" />
          </Field>
          <div className="mt-2 flex max-h-28 flex-wrap gap-1 overflow-y-auto">
            {others.map((c) => <button key={c.name} type="button" className="chip bg-white text-ink-700 ring-1 ring-ink-100 hover:ring-ink-300" onClick={() => set({ expression: `${rule.expression || ""}[${c.label}]` })}>{c.label}</button>)}
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            {["DAYS(AS_OF(), [ ])", "DAYS([ ], [ ])", "ROUND( , 1)", "IF( , , )", "COALESCE( , 0)", "VALUE(DIGITS([ ]))"].map((f) => (
              <button key={f} type="button" className="chip bg-ink-900 font-mono text-white" onClick={() => set({ expression: `${rule.expression || ""}${f}` })}>{f}</button>
            ))}
          </div>
        </div>
      )}

      {rule.kind === "input" && (
        <Field label={t("models.form.inputColumn")}>
          <select className="input" value={rule.column || column.name} onChange={(e) => set({ column: e.target.value })}>
            {(main?.columns || []).map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
          </select>
        </Field>
      )}
      {rule.kind === "constant" && (
        <Field label={t("models.form.value")}><input className="input" value={rule.value === undefined || rule.value === null ? "" : String(rule.value)} onChange={(e) => set({ value: Number.isNaN(Number(e.target.value)) || e.target.value === "" ? e.target.value : Number(e.target.value) })} /></Field>
      )}

      {result && (
        <div className={clsx("rounded-xl p-3 text-sm", result.error ? "bg-alert-50 text-alert-700" : "bg-white")}>
          {result.error ? result.error : (
            <>
              <div className="mb-1"><CheckLine check={result.check} /></div>
              <p className="leading-6 text-ink-900">{pick(result.text)}</p>
              {result.check && (result.check.rate ?? 1) < 1 && <div className="mt-2"><SamplesTable check={result.check} type={column.type} /></div>}
            </>
          )}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-secondary" disabled={test.isPending} onClick={() => test.mutate()}>{test.isPending && <Spinner />}{t("models.form.test")}</button>
        <button type="button" className="btn-primary" disabled={saving} onClick={() => onSave(clean(rule))}>{t("models.form.save")}</button>
      </div>
    </div>
  );
}

function clean(r: RuleSpec): RuleSpec {
  const out: RuleSpec = { kind: r.kind };
  if (r.kind === "lookup" || r.kind === "aggregate") {
    Object.assign(out, { relationship_id: r.relationship_id, column: r.column || null, func: r.kind === "lookup" ? "first" : r.func || "count",
      filters: (r.filters || []).filter((f) => f.column), default: r.default ?? null, round_digits: r.round_digits ?? null });
  } else if (r.kind === "formula") {
    Object.assign(out, { expression: r.expression || "", round_digits: r.round_digits ?? null });
  } else if (r.kind === "input") {
    out.column = r.column || null;
  } else if (r.kind === "constant") {
    out.value = r.value;
  }
  return out;
}

function ConditionRow({ cond, columns, type, onChange, onRemove }: {
  cond: ConditionSpec; columns: { name: string; label: string; type: string; examples: string[] }[]; type: string; onChange: (c: ConditionSpec) => void; onRemove: () => void;
}) {
  const { t } = useI18n();
  const isDate = type === "date" || type === "timestamp";
  const rel = cond.as_of_offset_days !== null && cond.as_of_offset_days !== undefined;
  const examples = columns.find((c) => c.name === cond.column)?.examples || [];
  const listId = useMemo(() => `ex-${Math.random().toString(36).slice(2)}`, []);
  const multi = cond.op === "in" || cond.op === "not_in";
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl bg-white p-2">
      <select className="input w-auto min-w-[150px] flex-1" value={cond.column} onChange={(e) => onChange({ ...cond, column: e.target.value })}>
        {columns.map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
      </select>
      <select className="input w-auto" value={cond.op} onChange={(e) => onChange({ ...cond, op: e.target.value })}>
        {OPS.map((o) => <option key={o} value={o}>{t(`models.op.${o}`)}</option>)}
      </select>
      {!["is_null", "not_null"].includes(cond.op) && (
        rel ? (
          <span className="flex items-center gap-1 text-sm text-ink-700">{t("models.form.reportDatePlus")}
            <input type="number" className="input w-20" value={cond.as_of_offset_days ?? 0} onChange={(e) => onChange({ ...cond, as_of_offset_days: Number(e.target.value) })} />{t("models.form.days")}</span>
        ) : (
          <>
            <input className="input w-auto min-w-[120px] flex-1" list={listId} value={Array.isArray(cond.value) ? (cond.value as unknown[]).join(", ") : String(cond.value ?? "")}
              placeholder={multi ? t("models.form.valuesPh") : ""}
              onChange={(e) => onChange({ ...cond, value: multi ? e.target.value.split(",").map((s) => s.trim()).filter(Boolean) : e.target.value })} />
            <datalist id={listId}>{examples.map((x) => <option key={x} value={x} />)}</datalist>
          </>
        )
      )}
      {isDate && (
        <button type="button" className={clsx("chip", rel ? "bg-ink-900 text-white" : "bg-ink-100 text-ink-700")} onClick={() => onChange({ ...cond, as_of_offset_days: rel ? null : 0, value: rel ? "" : null })}>
          {t("models.form.relative")}
        </button>
      )}
      <button type="button" className="btn-ghost btn-sm" onClick={onRemove} aria-label={t("action.delete")}><Trash2 className="h-3.5 w-3.5" /></button>
    </div>
  );
}

