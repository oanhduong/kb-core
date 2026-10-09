// Quản trị hệ thống (superadmin): người dùng, workspace, hàng đợi việc chạy nền, chi phí AI, cấu hình đang chạy.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Shield } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Badge, Empty, ErrorBox, KPI, Loading, PageHeader, Segmented, StatusBadge, useToast } from "@/components/ui";
import { useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtDate, fmtNumber, fmtRelative, fmtUsd, statusLabel, truncate } from "@/lib/format";
import { useAuth } from "@/state/auth";
import { Advanced } from "@/state/prefs";

// inShell: hiện trong khung workspace (có sidebar); không có workspace nào thì hiện riêng kèm nút quay lại.
export function AdminPage({ inShell }: { inShell?: boolean }) {
  const { user, workspaces } = useAuth();
  const t = useT();
  if (!user?.is_superadmin) return <Navigate to="/" replace />;
  const back = workspaces[0] ? `/w/${workspaces[0].id}` : "/onboarding";
  const tabs = [
    { to: "users", label: t("auth.admin.tab.users") },
    { to: "workspaces", label: t("auth.admin.tab.workspaces") },
    { to: "jobs", label: t("auth.admin.tab.jobs") },
    { to: "costs", label: t("auth.admin.tab.costs") },
    { to: "config", label: t("auth.admin.tab.config") },
  ];
  return (
    <div className={clsx(!inShell && "min-h-screen bg-paper")}>
      <Page wide>
        {!inShell && <Link to={back} className="mb-4 inline-flex items-center gap-1.5 rounded-lg text-sm text-ink-600 hover:text-ink-900"><ArrowLeft className="h-4 w-4" /> {t("auth.admin.back")}</Link>}
        <PageHeader
          title={<span className="flex items-center gap-2.5"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-ink-900 text-white"><Shield className="h-[18px] w-[18px]" /></span>{t("auth.admin.title")}</span>}
          subtitle={t("auth.admin.subtitle")} />
        <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-ink-100" aria-label={t("auth.admin.title")}>
          {tabs.map((tb) => (
            <NavLink key={tb.to} to={tb.to}
              className={({ isActive }) => clsx("-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition", isActive ? "border-ink-900 text-ink-900" : "border-transparent text-ink-600 hover:text-ink-900")}>
              {tb.label}
            </NavLink>
          ))}
        </nav>
        <Routes>
          <Route index element={<Navigate to="users" replace />} />
          <Route path="users" element={<Users />} />
          <Route path="workspaces" element={<Workspaces />} />
          <Route path="jobs" element={<Jobs />} />
          <Route path="costs" element={<Costs />} />
          <Route path="config" element={<Config />} />
        </Routes>
      </Page>
    </div>
  );
}

function Switch({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
      className={clsx("flex h-5 w-9 shrink-0 items-center rounded-full p-0.5 transition disabled:cursor-not-allowed disabled:opacity-45", checked ? "bg-brand-600" : "bg-ink-100")}>
      <span className={clsx("h-4 w-4 rounded-full bg-white shadow transition", checked && "translate-x-4")} />
    </button>
  );
}

function Table({ head, children, minWidth = 640 }: { head: React.ReactNode; children: React.ReactNode; minWidth?: number }) {
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full" style={{ minWidth }}>
        <thead><tr className="[&>th:first-child]:rounded-tl-2xl [&>th:last-child]:rounded-tr-2xl">{head}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Users() {
  const { user: me } = useAuth();
  const t = useT();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-users"], queryFn: api.adminUsers });
  const upd = useMutation({ mutationFn: ({ id, b }: { id: string; b: { is_active?: boolean; is_superadmin?: boolean } }) => api.adminUpdateUser(id, b), onSuccess: () => { toast.ok(t("action.saved")); void qc.invalidateQueries({ queryKey: ["admin-users"] }); }, onError: toast.err });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || t("error.title")} retry={() => q.refetch()} />;
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-600">{t("auth.admin.users.count", { n: fmtNumber(q.data.length) })}</p>
      <Table head={<>
        <th className="th">{t("auth.admin.users.col.person")}</th>
        <th className="th">{t("auth.admin.users.col.access")}</th>
        <th className="th">{t("auth.admin.users.col.admin")}</th>
        <th className="th">{t("auth.admin.users.col.lastLogin")}</th>
        <th className="th">{t("auth.admin.users.col.created")}</th>
      </>}>
        {q.data.map((u) => {
          const self = u.id === me?.id;
          return (
            <tr key={u.id} className="hover:bg-paper/60">
              <td className="td">
                <div className="font-medium text-ink-900">{u.name}{self && <span className="ml-1.5 text-xs font-normal text-ink-600">{t("settings.members.you")}</span>}</div>
                <div className="text-xs text-ink-600">{u.email}</div>
              </td>
              <td className="td">
                <div className="flex items-center gap-2.5">
                  <Switch checked={u.is_active} disabled={self || upd.isPending} label={t("auth.admin.users.col.access")} onChange={(v) => upd.mutate({ id: u.id, b: { is_active: v } })} />
                  <span className={clsx("text-sm", u.is_active ? "text-ink-800" : "text-alert-700")}>{u.is_active ? t("auth.admin.users.active") : t("auth.admin.users.blocked")}</span>
                </div>
              </td>
              <td className="td">
                <div className="flex items-center gap-2.5">
                  <Switch checked={u.is_superadmin} disabled={self || upd.isPending} label={t("auth.admin.users.col.admin")} onChange={(v) => upd.mutate({ id: u.id, b: { is_superadmin: v } })} />
                  {u.is_superadmin && <Badge tone="brand">{t("auth.admin.users.adminBadge")}</Badge>}
                </div>
              </td>
              <td className="td whitespace-nowrap text-xs text-ink-600">{u.last_login_at ? fmtRelative(u.last_login_at) : t("auth.admin.users.never")}</td>
              <td className="td whitespace-nowrap text-xs text-ink-600">{fmtDate(u.created_at)}</td>
            </tr>
          );
        })}
      </Table>
    </div>
  );
}

function Workspaces() {
  const t = useT();
  const q = useQuery({ queryKey: ["admin-workspaces"], queryFn: api.adminWorkspaces });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || t("error.title")} retry={() => q.refetch()} />;
  const packName = (key: string, fallback: string) => { const k = `auth.pack.${key}.name`; const v = t(k); return v === k ? fallback : v; };
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-600">{t("auth.admin.ws.count", { n: fmtNumber(q.data.length) })}</p>
      <Table head={<>
        <th className="th">{t("auth.admin.ws.col.name")}</th>
        <th className="th">{t("auth.admin.ws.col.kind")}</th>
        <th className="th">{t("auth.admin.ws.col.members")}</th>
        <th className="th">{t("auth.admin.ws.col.created")}</th>
        <th className="th">{t("auth.admin.ws.col.status")}</th>
        <th className="th"><span className="sr-only">{t("settings.members.col.actions")}</span></th>
      </>}>
        {q.data.map((w) => (
          <tr key={w.id} className="hover:bg-paper/60">
            <td className="td">
              <div className="font-medium text-ink-900">{w.name}</div>
              <Advanced><div className="font-mono text-[11px] text-ink-400">{w.slug} / {w.id}</div><div className="font-mono text-[11px] text-ink-400">{t("auth.admin.ws.owner")}: {w.owner_id}</div></Advanced>
            </td>
            <td className="td">{packName(w.domain_pack, w.domain_pack_name)}</td>
            <td className="td tabular-nums">{w.member_count !== undefined ? fmtNumber(w.member_count) : "—"}</td>
            <td className="td whitespace-nowrap text-xs text-ink-600">{fmtDate(w.created_at)}</td>
            <td className="td">{w.archived_at ? <Badge>{t("auth.admin.ws.archived")}</Badge> : <Badge tone="emerald">{t("auth.admin.ws.active")}</Badge>}</td>
            <td className="td text-right">{!w.archived_at && <Link className="btn-secondary btn-sm" to={`/w/${w.id}`}>{t("action.open")}</Link>}</td>
          </tr>
        ))}
      </Table>
    </div>
  );
}

const JOB_FILTERS = ["", "queued", "processing", "done", "blocked", "error"] as const;
type JobFilter = (typeof JOB_FILTERS)[number];

function Jobs() {
  const t = useT();
  const [status, setStatus] = useState<JobFilter>("");
  const q = useQuery({ queryKey: ["admin-jobs", status], queryFn: () => api.adminJobs({ status: status || undefined, limit: 200 }), refetchInterval: 5000 });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-ink-600">{t("auth.admin.jobs.hint")}</p>
        <Segmented value={status} onChange={setStatus} options={JOB_FILTERS.map((s) => ({ key: s, label: s ? statusLabel(s) : t("auth.admin.jobs.all") }))} />
      </div>
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} retry={() => q.refetch()} />}
      {q.data && q.data.length === 0 && <div className="panel"><Empty title={t("auth.admin.jobs.empty")} /></div>}
      {q.data && q.data.length > 0 && (
        <Table minWidth={860} head={<>
          <th className="th">{t("auth.admin.jobs.col.type")}</th>
          <th className="th">{t("auth.admin.jobs.col.status")}</th>
          <th className="th">{t("auth.admin.jobs.col.workspace")}</th>
          <th className="th">{t("auth.admin.jobs.col.attempts")}</th>
          <th className="th">{t("auth.admin.jobs.col.created")}</th>
          <th className="th">{t("auth.admin.jobs.col.result")}</th>
        </>}>
          {q.data.map((j) => (
            <tr key={j.job_id} className="align-top hover:bg-paper/60">
              <td className="td font-mono text-xs">{j.type}</td>
              <td className="td"><StatusBadge status={j.status} /></td>
              <td className="td font-mono text-xs text-ink-600">{j.workspace_id?.slice(0, 8) || "—"}</td>
              <td className="td text-xs tabular-nums">{j.attempts}/{j.max_attempts}</td>
              <td className="td whitespace-nowrap text-xs text-ink-600">{fmtRelative(j.created_at)}</td>
              <td className="td max-w-md break-words text-xs">{j.error ? <span className="text-alert-700">{truncate(j.error, 160)}</span> : <span className="font-mono text-ink-600">{truncate(JSON.stringify(j.result || {}), 120)}</span>}</td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}

function Costs() {
  const t = useT();
  const q = useQuery({ queryKey: ["admin-costs"], queryFn: () => api.adminCosts(30) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || t("error.title")} retry={() => q.refetch()} />;
  const rows = (q.data.by_role as { role?: string; model?: string; calls?: number; cost_usd?: number; input_tokens?: number; output_tokens?: number }[]) || [];
  const over = q.data.today_usd > q.data.daily_budget_usd;
  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:max-w-2xl">
        <KPI label={t("auth.admin.costs.today")} value={fmtUsd(q.data.today_usd)} tone={over ? "red" : undefined} hint={over ? t("auth.admin.costs.over") : undefined} />
        <KPI label={t("auth.admin.costs.budget")} value={fmtUsd(q.data.daily_budget_usd)} hint={t("auth.admin.costs.budgetHint")} />
      </div>
      {rows.length === 0 ? <div className="panel"><Empty title={t("auth.admin.costs.empty")} /></div> : (
        <div>
          <h2 className="mb-3 font-display text-lg font-semibold text-ink-900">{t("auth.admin.costs.byRole")}</h2>
          <Table head={<>
            <th className="th">{t("auth.admin.costs.col.role")}</th>
            <th className="th">{t("auth.admin.costs.col.model")}</th>
            <th className="th text-right">{t("auth.admin.costs.col.calls")}</th>
            <th className="th text-right">{t("auth.admin.costs.col.in")}</th>
            <th className="th text-right">{t("auth.admin.costs.col.out")}</th>
            <th className="th text-right">{t("auth.admin.costs.col.usd")}</th>
          </>}>
            {rows.map((r, i) => (
              <tr key={i} className="hover:bg-paper/60">
                <td className="td">{r.role}</td>
                <td className="td font-mono text-xs">{r.model}</td>
                <td className="td text-right tabular-nums">{fmtNumber(r.calls ?? 0)}</td>
                <td className="td text-right tabular-nums">{fmtNumber(r.input_tokens ?? 0)}</td>
                <td className="td text-right tabular-nums">{fmtNumber(r.output_tokens ?? 0)}</td>
                <td className="td text-right font-medium tabular-nums">{fmtUsd(r.cost_usd)}</td>
              </tr>
            ))}
          </Table>
        </div>
      )}
    </div>
  );
}

function Config() {
  const t = useT();
  const q = useQuery({ queryKey: ["config"], queryFn: api.config });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || t("error.title")} retry={() => q.refetch()} />;
  const c = q.data as Record<string, unknown>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge tone="brand">{t("auth.admin.config.version", { v: String(c.config_version) })}</Badge>
        {typeof c.config_hash === "string" && <Badge>{t("auth.admin.config.hash", { h: c.config_hash.slice(0, 12) })}</Badge>}
        {typeof c.source_path === "string" && c.source_path && <Badge>{c.source_path}</Badge>}
      </div>
      <p className="max-w-3xl text-sm leading-6 text-ink-600">
        {t("auth.admin.config.hint1")} <code className="rounded bg-ink-100/60 px-1 font-mono text-xs">config.yaml</code>{t("auth.admin.config.hint2")} <code className="rounded bg-ink-100/60 px-1 font-mono text-xs">make rollback-config VERSION=…</code> {t("auth.admin.config.hint3")}
      </p>
      <pre className="panel max-h-[70vh] overflow-auto p-5 font-mono text-xs leading-5 text-ink-800">{JSON.stringify(c, null, 2)}</pre>
    </div>
  );
}
