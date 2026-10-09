// Liên kết giữa các bảng (mọi file, mọi sheet) do hệ thống dò bằng dữ liệu: gom theo KHOÁ CHUNG (vd "Site #", "Site No",
// "Site Number" ở 8 sheet = 1 khoá), mỗi cặp bảng có tỉ lệ khớp; biên tập viên xác nhận hoặc bỏ từng cặp, dò lại vẫn giữ
// quyết định. Liên kết được dùng khi trả lời câu hỏi số liệu (nối bảng đúng khoá) và để dựng mục trên bản đồ tri thức.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, ChevronDown, KeyRound, Link2, RefreshCw, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge, Empty, ErrorBox, Loading, Spinner, StatusBadge, useToast } from "@/components/ui";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import { fmtNumber, fmtPct } from "@/lib/format";
import type { TableLink } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { Advanced } from "@/state/prefs";

interface KeyGroup {
  id: string;
  label: string;
  members: { key: string; title: string; label: string }[];
  links: TableLink[];
  approved: number;
}

/** Gom các cặp liên kết thành khoá chung: cột nối với nhau (trực tiếp hoặc qua cột khác) cùng 1 nhóm. */
function groupLinks(items: TableLink[]): KeyGroup[] {
  const parent = new Map<string, string>();
  const find = (x: string): string => {
    if (!parent.has(x)) parent.set(x, x);
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    parent.set(x, r);
    return r;
  };
  const key = (s: TableLink["left"]) => `${s.table_id}:${s.column}`;
  const live = items.filter((l) => l.status !== "rejected");
  for (const l of live) parent.set(find(key(l.left)), find(key(l.right)));
  const groups = new Map<string, KeyGroup>();
  for (const l of items) {
    const root = find(key(l.left));
    const g = groups.get(root) || { id: root, label: "", members: [], links: [], approved: 0 };
    g.links.push(l);
    if (l.status === "approved") g.approved += 1;
    for (const s of [l.left, l.right]) {
      if (!g.members.some((m) => m.key === key(s))) g.members.push({ key: key(s), title: s.title || s.table || "", label: s.label });
    }
    groups.set(root, g);
  }
  return [...groups.values()].map((g) => {
    const counts = new Map<string, number>();
    for (const m of g.members) counts.set(m.label, (counts.get(m.label) || 0) + 1);
    return { ...g, label: [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "" };
  }).sort((a, b) => b.members.length - a.members.length);
}

export function TableLinksPane() {
  const t = useT();
  const { wsId, can } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const links = useQuery({
    queryKey: ["table-links", wsId], queryFn: () => api.tableLinks(wsId),
    refetchInterval: (q) => (q.state.data?.job && ["queued", "processing"].includes(q.state.data.job.status) ? 3000 : false),
  });
  const discover = useMutation({
    mutationFn: () => api.discoverTableLinks(wsId),
    onSuccess: () => { toast.ok(t("explore.links.discovering")); void qc.invalidateQueries({ queryKey: ["table-links", wsId] }); },
    onError: toast.err,
  });
  const groups = useMemo(() => groupLinks(links.data?.items || []), [links.data]);
  const running = links.data?.job && ["queued", "processing"].includes(links.data.job.status);

  if (links.isLoading) return <Loading />;
  if (links.error) return <ErrorBox error={links.error} retry={() => links.refetch()} />;
  return (
    <div className="space-y-4">
      <div className="panel flex flex-wrap items-start gap-4 p-5">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-700"><Link2 className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-lg font-semibold text-ink-900">{t("explore.links.title")}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-ink-700">{t("explore.links.explain")}</p>
          {running && <p className="mt-2 inline-flex items-center gap-2 text-sm text-brand-700"><Spinner /> {t("explore.links.running")}</p>}
        </div>
        {can("editor") && (
          <button className="btn-secondary" disabled={discover.isPending || !!running} onClick={() => discover.mutate()}>
            {discover.isPending ? <Spinner /> : <RefreshCw className="h-4 w-4" />} {t("explore.links.discover")}
          </button>
        )}
      </div>
      {groups.length === 0 ? (
        <div className="panel"><Empty icon={<Link2 />} title={t("explore.links.none.title")} hint={t("explore.links.none.hint")} /></div>
      ) : groups.map((g) => <GroupCard key={g.id} g={g} canEdit={can("editor")} />)}
    </div>
  );
}

function GroupCard({ g, canEdit }: { g: KeyGroup; canEdit: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const tables = new Set(g.members.map((m) => m.title)).size;
  const live = g.links.filter((l) => l.status !== "rejected").length;
  return (
    <section className="panel overflow-hidden">
      <button type="button" className="flex w-full items-start gap-3 p-5 text-left" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <KeyRound className="mt-1 h-[18px] w-[18px] shrink-0 text-ink-400" />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-display text-base font-semibold text-ink-900">{g.label}</span>
            <Badge>{t("explore.links.tables", { n: fmtNumber(tables) })}</Badge>
            {g.approved > 0 ? <Badge tone="emerald">{t("explore.links.approvedN", { n: g.approved })}</Badge> : <Badge tone="amber">{t("explore.links.proposed")}</Badge>}
            {live < g.links.length && <Badge tone="red">{t("explore.links.rejectedN", { n: g.links.length - live })}</Badge>}
          </span>
          <span className="mt-2 flex flex-wrap gap-1.5">
            {g.members.slice(0, 12).map((m) => (
              <span key={m.key} className="rounded-lg bg-paper px-2 py-1 text-xs text-ink-700 ring-1 ring-ink-100">
                <span className="text-ink-600">{m.title}</span> · <span className="font-medium text-ink-900">{m.label}</span>
              </span>
            ))}
            {g.members.length > 12 && <span className="px-1 py-1 text-xs text-ink-600">+{g.members.length - 12}</span>}
          </span>
        </span>
        <ChevronDown className={clsx("mt-1 h-4 w-4 shrink-0 text-ink-400 transition", open && "rotate-180")} />
      </button>
      {open && (
        <ul className="divide-y divide-ink-100 border-t border-ink-100">
          {g.links.map((l) => <LinkRow key={l.id} l={l} canEdit={canEdit} />)}
        </ul>
      )}
    </section>
  );
}

function LinkRow({ l, canEdit }: { l: TableLink; canEdit: boolean }) {
  const t = useT();
  const { wsId } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const review = useMutation({
    mutationFn: (status: "approved" | "rejected" | "proposed") => api.reviewTableLink(wsId, l.id, status),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["table-links", wsId] }),
    onError: toast.err,
  });
  const side = (s: TableLink["left"]) => (
    <span className="min-w-0">
      <span className="block truncate text-xs text-ink-600">{s.title}</span>
      <span className="block truncate text-sm font-medium text-ink-900">{s.label}</span>
      {s.examples.length > 0 && <span className="block truncate text-xs text-ink-400">{s.examples.slice(0, 3).join(", ")}</span>}
      <Advanced>{s.transforms.length > 0 && <span className="block font-mono text-[11px] text-ink-400">{s.transforms.join(" + ")}</span>}</Advanced>
    </span>
  );
  return (
    <li className={clsx("grid gap-3 px-5 py-3.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-center", l.status === "rejected" && "opacity-60")}>
      {side(l.left)}
      {side(l.right)}
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        <span className="text-xs text-ink-700" title={t("explore.links.matchHint")}>
          {t("explore.links.match", { n: fmtNumber(l.matched_keys), pct: fmtPct(l.left_coverage) })}
        </span>
        <Badge>{t(`explore.links.card.${l.cardinality}`)}</Badge>
        <Advanced><span className="text-xs tabular-nums text-ink-400">{l.confidence.toFixed(2)}</span></Advanced>
        {l.status !== "proposed" && <StatusBadge status={l.status} />}
        {canEdit && (
          <span className="flex gap-1">
            {l.status !== "approved" && (
              <button className="btn-secondary btn-sm px-2" disabled={review.isPending} onClick={() => review.mutate("approved")} title={t("explore.links.approve")} aria-label={t("explore.links.approve")}>
                <Check className="h-3.5 w-3.5" />
              </button>
            )}
            {l.status !== "rejected" && (
              <button className="btn-secondary btn-sm px-2" disabled={review.isPending} onClick={() => review.mutate("rejected")} title={t("explore.links.reject")} aria-label={t("explore.links.reject")}>
                <X className="h-3.5 w-3.5" />
              </button>
            )}
            {l.status !== "proposed" && (
              <button className="btn-ghost btn-sm" disabled={review.isPending} onClick={() => review.mutate("proposed")}>{t("explore.links.undo")}</button>
            )}
          </span>
        )}
      </div>
    </li>
  );
}
