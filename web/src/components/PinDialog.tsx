// Ghim 1 câu trả lời (trace) vào báo cáo có sẵn hoặc báo cáo mới.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, FilePlus2, FileText } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import { useWorkspace } from "@/state/auth";
import { Field, Modal, Spinner, useToast } from "./ui";

const NEW = "__new__";

export function PinDialog({ traceId, question, onClose }: { traceId: string | null; question?: string; onClose: () => void }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const toast = useToast();
  const qc = useQueryClient();
  const nav = useNavigate();
  const reports = useQuery({ queryKey: ["reports", wsId], queryFn: () => api.reports(wsId), enabled: !!traceId });
  const [target, setTarget] = useState<string>(NEW);
  const [newTitle, setNewTitle] = useState("");
  const [heading, setHeading] = useState("");
  const [withAnswer, setWithAnswer] = useState(true);
  const [withArtifacts, setWithArtifacts] = useState(true);
  useEffect(() => {
    if (!traceId) return;
    setTarget(NEW);
    setNewTitle("");
    setHeading(question ? question.slice(0, 120) : "");
    setWithAnswer(true);
    setWithArtifacts(true);
  }, [traceId, question]);
  const editable = (reports.data || []).filter((r) => r.status !== "generating");
  const mut = useMutation({
    mutationFn: async () => {
      let id = target;
      if (id === NEW) id = (await api.createReport(wsId, { title: newTitle.trim() || t("reports.pin.defaultTitle") })).id;
      await api.pinToReport(wsId, id, { trace_id: traceId!, heading: heading.trim() || undefined, include_answer: withAnswer, include_artifacts: withArtifacts });
      return id;
    },
    onSuccess: (id) => {
      void qc.invalidateQueries({ queryKey: ["reports", wsId] });
      void qc.invalidateQueries({ queryKey: ["report", wsId, id] });
      toast.ok(t("reports.pin.done"));
      onClose();
      nav(`/w/${wsId}/reports/${id}`);
    },
    onError: toast.err,
  });
  const nothing = !withAnswer && !withArtifacts;
  return (
    <Modal open={!!traceId} onClose={onClose} title={t("reports.pin.title")} width="max-w-xl"
      footer={<>
        <button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button>
        <button className="btn-primary" disabled={mut.isPending || nothing} onClick={() => mut.mutate()}>
          {mut.isPending && <Spinner className="text-white" />} {t("reports.pin.submit")}
        </button>
      </>}>
      <div className="space-y-5">
        <p className="text-sm leading-6 text-ink-600">{t("reports.pin.intro")}</p>
        <div>
          <span className="label">{t("reports.pin.target")}</span>
          <div className="max-h-56 space-y-1 overflow-y-auto rounded-xl border border-ink-100 p-1" role="radiogroup" aria-label={t("reports.pin.target")}>
            <Choice selected={target === NEW} onSelect={() => setTarget(NEW)} icon={<FilePlus2 className="h-4 w-4" />} title={t("reports.pin.new")} />
            {reports.isLoading && <div className="flex items-center gap-2 px-3 py-2 text-sm text-ink-600"><Spinner /> {t("loading")}</div>}
            {editable.map((r) => (
              <Choice key={r.id} selected={target === r.id} onSelect={() => setTarget(r.id)} icon={<FileText className="h-4 w-4" />}
                title={r.title} meta={t("reports.list.meta", { n: r.block_count, when: fmtRelative(r.updated_at) })} />
            ))}
          </div>
        </div>
        {target === NEW && (
          <Field label={t("reports.pin.newTitle")}>
            <input className="input" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder={t("reports.pin.defaultTitle")} />
          </Field>
        )}
        <Field label={t("reports.pin.heading")} hint={t("reports.pin.headingHint")}>
          <input className="input" value={heading} onChange={(e) => setHeading(e.target.value)} placeholder={t("reports.pin.headingPlaceholder")} />
        </Field>
        <fieldset>
          <legend className="label">{t("reports.pin.include")}</legend>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Check2 checked={withAnswer} onChange={setWithAnswer} label={t("reports.pin.withAnswer")} />
            <Check2 checked={withArtifacts} onChange={setWithArtifacts} label={t("reports.pin.withArtifacts")} />
          </div>
          {nothing && <p className="mt-2 text-xs text-alert-700">{t("reports.pin.nothing")}</p>}
        </fieldset>
      </div>
    </Modal>
  );
}

function Choice({ selected, onSelect, icon, title, meta }: { selected: boolean; onSelect: () => void; icon: ReactNode; title: string; meta?: string }) {
  return (
    <button type="button" role="radio" aria-checked={selected} onClick={onSelect}
      className={clsx("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition", selected ? "bg-brand-50 text-brand-800" : "text-ink-800 hover:bg-paper")}>
      <span className={clsx("shrink-0", selected ? "text-brand-600" : "text-ink-400")}>{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{title}</span>
        {meta && <span className="block truncate text-xs text-ink-600">{meta}</span>}
      </span>
      {selected && <Check className="h-4 w-4 shrink-0 text-brand-600" />}
    </button>
  );
}

function Check2({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className={clsx("flex flex-1 cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 text-sm transition",
      checked ? "border-brand-300 bg-brand-50/60 text-ink-900" : "border-ink-100 bg-white text-ink-700 hover:border-ink-300")}>
      <input type="checkbox" className="h-4 w-4 accent-brand-600" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}
