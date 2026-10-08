// Danh sách báo cáo + tạo mới (mô tả -> AI soạn nền, hoặc báo cáo trống để ghim câu trả lời vào).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { FilePlus2, FileText, Plus, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Confirm, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Segmented, Spinner, StatusBadge, useToast } from "@/components/ui";
import { useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtRelative, truncate } from "@/lib/format";
import type { Report } from "@/lib/types";
import { useWorkspace } from "@/state/auth";

const EXAMPLES = ["reports.example.overview", "reports.example.policies", "reports.example.numbers"];

export function ReportsPage() {
  const t = useT();
  const { wsId, can } = useWorkspace();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [del, setDel] = useState<Report | null>(null);
  const reports = useQuery({
    queryKey: ["reports", wsId],
    queryFn: () => api.reports(wsId),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "generating") ? 4000 : false),
  });
  const delMut = useMutation({
    mutationFn: (id: string) => api.deleteReport(wsId, id),
    onSuccess: () => { setDel(null); toast.ok(t("reports.deleted")); void qc.invalidateQueries({ queryKey: ["reports", wsId] }); },
    onError: toast.err,
  });
  const editor = can("editor");
  return (
    <Page>
      <PageHeader title={t("reports.title")} subtitle={t("reports.subtitle")}
        actions={editor && <button className="btn-primary" onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> {t("reports.new")}</button>} />
      {reports.isLoading && <Loading />}
      {reports.error && <ErrorBox error={reports.error} retry={() => reports.refetch()} />}
      {reports.data && reports.data.length === 0 && (
        <div className="panel">
          <Empty icon={<FileText />} title={t("reports.empty.title")} hint={t(editor ? "reports.empty.hint" : "reports.empty.hintViewer")}
            action={editor && <button className="btn-primary" onClick={() => setOpen(true)}><Sparkles className="h-4 w-4" /> {t("reports.empty.action")}</button>} />
        </div>
      )}
      {reports.data && reports.data.length > 0 && (
        <ul className="panel divide-y divide-ink-100 overflow-hidden">
          {reports.data.map((r) => (
            <li key={r.id} className="group relative flex items-start gap-4 px-5 py-4 transition-colors hover:bg-paper focus-within:bg-paper sm:px-6">
              <span className={clsx("mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", r.prompt ? "bg-brand-50 text-brand-700" : "bg-ink-100/70 text-ink-700")} aria-hidden>
                {r.prompt ? <Sparkles className="h-[18px] w-[18px]" /> : <FileText className="h-[18px] w-[18px]" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <Link to={r.id} className="font-display text-[17px] font-semibold text-ink-900 after:absolute after:inset-0 after:content-[''] hover:text-brand-700">
                    {r.title}
                  </Link>
                  {r.status !== "ready" && <StatusBadge status={r.status} />}
                </div>
                <p className="mt-0.5 line-clamp-2 text-sm leading-6 text-ink-600">
                  {r.description || (r.prompt ? truncate(r.prompt, 180) : t("reports.list.manual"))}
                </p>
                <div className="mt-1 text-xs text-ink-400">
                  {r.status === "generating" && r.job?.progress.total
                    ? t("reports.progress.section", { n: Math.min(r.job.progress.done + 1, r.job.progress.total), total: r.job.progress.total })
                    : t("reports.list.meta", { n: r.block_count, when: fmtRelative(r.updated_at) })}
                </div>
              </div>
              {editor && (
                <button type="button" className="btn-ghost btn-sm relative z-10 opacity-0 transition focus:opacity-100 group-hover:opacity-100"
                  aria-label={t("reports.delete")} title={t("reports.delete")} onClick={() => setDel(r)}>
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <CreateModal open={open} onClose={() => setOpen(false)} onCreated={(id) => nav(`/w/${wsId}/reports/${id}`)} />
      <Confirm open={!!del} onClose={() => setDel(null)} onConfirm={() => del && delMut.mutate(del.id)} danger busy={delMut.isPending}
        title={t("reports.deleteTitle")} message={t("reports.deleteMessage", { title: del?.title || "" })} confirmLabel={t("action.delete")} />
    </Page>
  );
}

function CreateModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<"ai" | "blank">("ai");
  const [prompt, setPrompt] = useState("");
  const [title, setTitle] = useState("");
  useEffect(() => { if (open) { setPrompt(""); setTitle(""); } }, [open]);
  const mut = useMutation({
    mutationFn: () => api.createReport(wsId, mode === "ai" ? { prompt: prompt.trim(), title: title.trim() || undefined } : { title: title.trim() }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["reports", wsId] });
      toast.ok(t(mode === "ai" ? "reports.create.started" : "reports.create.created"));
      onClose();
      onCreated(r.id);
    },
    onError: toast.err,
  });
  const ready = mode === "ai" ? !!prompt.trim() : !!title.trim();
  return (
    <Modal open={open} onClose={onClose} title={t("reports.create.title")} width="max-w-2xl"
      footer={<>
        <button className="btn-secondary" onClick={onClose}>{t("action.cancel")}</button>
        <button className="btn-primary" disabled={mut.isPending || !ready} onClick={() => mut.mutate()}>
          {mut.isPending ? <Spinner className="text-white" /> : mode === "ai" ? <Sparkles className="h-4 w-4" /> : <FilePlus2 className="h-4 w-4" />}
          {t(mode === "ai" ? "reports.create.submitAi" : "reports.create.submitBlank")}
        </button>
      </>}>
      <Segmented value={mode} onChange={setMode} className="mb-5 w-full text-sm [&>button]:flex-1 [&>button]:py-1.5"
        options={[
          { key: "ai", label: <span className="inline-flex items-center gap-1.5"><Sparkles className="h-4 w-4" />{t("reports.create.modeAi")}</span> },
          { key: "blank", label: <span className="inline-flex items-center gap-1.5"><FilePlus2 className="h-4 w-4" />{t("reports.create.modeBlank")}</span> },
        ]} />
      {mode === "ai" ? (
        <div className="space-y-4">
          <Field label={t("reports.create.prompt")} hint={t("reports.create.promptHint")}>
            <textarea className="input text-[15px] leading-6" rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={t(EXAMPLES[0])} autoFocus />
          </Field>
          <div>
            <div className="mb-2 text-[13px] font-medium text-ink-700">{t("reports.create.examples")}</div>
            <ul className="space-y-1.5">
              {EXAMPLES.map((k) => (
                <li key={k}>
                  <button type="button" onClick={() => setPrompt(t(k))}
                    className="w-full rounded-xl border border-ink-100 px-3.5 py-2.5 text-left text-sm leading-6 text-ink-700 transition hover:border-brand-300 hover:bg-brand-50/40 hover:text-ink-900">
                    {t(k)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <Field label={t("reports.create.titleOptional")}>
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("reports.create.titleAuto")} />
          </Field>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm leading-6 text-ink-600">{t("reports.create.blankHint")}</p>
          <Field label={t("reports.create.titleLabel")}>
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("reports.create.titlePlaceholder")} autoFocus />
          </Field>
        </div>
      )}
    </Modal>
  );
}
