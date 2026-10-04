// Quản trị hệ thống (superadmin): người dùng, workspace, hàng đợi job toàn hệ thống, chi phí, config đang chạy.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Shield } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Badge, ErrorBox, KPI, Loading, PageHeader, StatusBadge, useToast } from "@/components/ui";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtDate, fmtRelative, fmtUsd, truncate } from "@/lib/format";
import { useAuth } from "@/state/auth";

export function AdminPage() {
  const { user, workspaces } = useAuth();
  if (!user?.is_superadmin) return <Navigate to="/" replace />;
  const back = workspaces[0] ? `/w/${workspaces[0].id}` : "/onboarding";
  const tabs = [{ to: "users", label: "Người dùng" }, { to: "workspaces", label: "Workspaces" }, { to: "jobs", label: "Hàng đợi job" }, { to: "costs", label: "Chi phí" }, { to: "config", label: "Config" }];
  return (
    <div className="min-h-screen">
      <Page>
        <Link to={back} className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" /> Về workspace</Link>
        <PageHeader title={<span className="flex items-center gap-2"><Shield className="h-5 w-5 text-violet-600" /> Quản trị hệ thống</span>} subtitle="Chỉ superadmin. Thay đổi ở đây ảnh hưởng toàn bộ người dùng." />
        <div className="mb-4 flex gap-1 border-b border-slate-200">{tabs.map((t) => <NavLink key={t.to} to={t.to} className={({ isActive }) => clsx("-mb-px border-b-2 px-3 py-2 text-sm font-medium", isActive ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800")}>{t.label}</NavLink>)}</div>
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

function Users() {
  const { user: me } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-users"], queryFn: api.adminUsers });
  const upd = useMutation({ mutationFn: ({ id, b }: { id: string; b: { is_active?: boolean; is_superadmin?: boolean } }) => api.adminUpdateUser(id, b), onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin-users"] }), onError: toast.err });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || "—"} />;
  return (
    <div className="card overflow-hidden"><table className="w-full"><thead><tr><th className="th">Người dùng</th><th className="th">Trạng thái</th><th className="th">Superadmin</th><th className="th">Đăng nhập cuối</th><th className="th">Tạo</th></tr></thead>
      <tbody>{q.data.map((u) => (
        <tr key={u.id} className="hover:bg-slate-50">
          <td className="td"><div className="font-medium">{u.name}</div><div className="text-xs text-slate-500">{u.email}</div></td>
          <td className="td"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={u.is_active} disabled={u.id === me?.id} onChange={(e) => upd.mutate({ id: u.id, b: { is_active: e.target.checked } })} /> {u.is_active ? "Hoạt động" : "Bị khoá"}</label></td>
          <td className="td"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={u.is_superadmin} disabled={u.id === me?.id} onChange={(e) => upd.mutate({ id: u.id, b: { is_superadmin: e.target.checked } })} /> {u.is_superadmin && <Badge tone="violet">superadmin</Badge>}</label></td>
          <td className="td text-xs">{fmtDate(u.last_login_at, true)}</td><td className="td text-xs">{fmtDate(u.created_at)}</td>
        </tr>
      ))}</tbody></table></div>
  );
}

function Workspaces() {
  const q = useQuery({ queryKey: ["admin-workspaces"], queryFn: api.adminWorkspaces });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || "—"} />;
  return (
    <div className="card overflow-hidden"><table className="w-full"><thead><tr><th className="th">Workspace</th><th className="th">Pack</th><th className="th">Chủ sở hữu</th><th className="th">Tạo</th><th className="th">Trạng thái</th><th className="th" /></tr></thead>
      <tbody>{q.data.map((w) => (
        <tr key={w.id} className="hover:bg-slate-50">
          <td className="td"><div className="font-medium">{w.name}</div><div className="text-xs text-slate-500">{w.slug} · {w.id}</div></td>
          <td className="td">{w.domain_pack_name}</td><td className="td font-mono text-xs">{w.owner_id}</td><td className="td text-xs">{fmtDate(w.created_at)}</td>
          <td className="td">{w.archived_at ? <Badge>Đã lưu trữ</Badge> : <Badge tone="emerald">Hoạt động</Badge>}</td>
          <td className="td text-right">{!w.archived_at && <Link className="btn-secondary btn-sm" to={`/w/${w.id}`}>Mở</Link>}</td>
        </tr>
      ))}</tbody></table></div>
  );
}

function Jobs() {
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["admin-jobs", status], queryFn: () => api.adminJobs({ status: status || undefined, limit: 200 }), refetchInterval: 5000 });
  return (
    <div>
      <div className="mb-3 flex gap-2">{["", "queued", "processing", "done", "blocked", "error"].map((s) => <button key={s} className={clsx("badge cursor-pointer", status === s ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600")} onClick={() => setStatus(s)}>{s || "Tất cả"}</button>)}</div>
      {q.isLoading && <Loading />}
      {q.data && (
        <div className="card overflow-x-auto"><table className="w-full"><thead><tr><th className="th">Loại</th><th className="th">Trạng thái</th><th className="th">Workspace</th><th className="th">Thử</th><th className="th">Tạo</th><th className="th">Kết quả / lỗi</th></tr></thead>
          <tbody>{q.data.map((j) => <tr key={j.job_id} className="hover:bg-slate-50"><td className="td font-mono text-xs">{j.type}</td><td className="td"><StatusBadge status={j.status} /></td><td className="td font-mono text-xs">{j.workspace_id?.slice(0, 8) || "—"}</td><td className="td text-xs">{j.attempts}/{j.max_attempts}</td><td className="td text-xs">{fmtRelative(j.created_at)}</td><td className="td max-w-md text-xs">{j.error ? <span className="text-red-600">{truncate(j.error, 160)}</span> : truncate(JSON.stringify(j.result || {}), 120)}</td></tr>)}</tbody></table></div>
      )}
    </div>
  );
}

function Costs() {
  const q = useQuery({ queryKey: ["admin-costs"], queryFn: () => api.adminCosts(30) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || "—"} />;
  const rows = (q.data.by_role as { role?: string; model?: string; calls?: number; cost_usd?: number; input_tokens?: number; output_tokens?: number }[]) || [];
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2"><KPI label="Hôm nay" value={fmtUsd(q.data.today_usd)} tone={q.data.today_usd > q.data.daily_budget_usd ? "red" : undefined} /><KPI label="Ngân sách ngày" value={fmtUsd(q.data.daily_budget_usd)} hint="vượt -> cảnh báo webhook" /></div>
      {rows.length > 0 && (
        <div className="card overflow-hidden"><table className="w-full"><thead><tr><th className="th">Vai trò LLM</th><th className="th">Model</th><th className="th text-right">Lượt gọi</th><th className="th text-right">Token vào</th><th className="th text-right">Token ra</th><th className="th text-right">USD (30 ngày)</th></tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i} className="hover:bg-slate-50"><td className="td">{r.role}</td><td className="td font-mono text-xs">{r.model}</td><td className="td text-right">{r.calls}</td><td className="td text-right">{r.input_tokens}</td><td className="td text-right">{r.output_tokens}</td><td className="td text-right">{fmtUsd(r.cost_usd)}</td></tr>)}</tbody></table></div>
      )}
    </div>
  );
}

function Config() {
  const q = useQuery({ queryKey: ["config"], queryFn: api.config });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || "—"} />;
  const c = q.data as Record<string, unknown>;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 text-sm"><Badge tone="brand">version {String(c.config_version)}</Badge>{typeof c.config_hash === "string" && <Badge>hash {c.config_hash.slice(0, 12)}</Badge>}<Badge>{String(c.source_path || "")}</Badge></div>
      <p className="text-xs text-slate-500">Config quyết định kết quả (model, ngưỡng, prompt) nằm trong <code>config.yaml</code> có version; rollback bằng <code>make rollback-config VERSION=…</code> (xem docs/OPERATIONS.md).</p>
      <pre className="card max-h-[70vh] overflow-auto p-4 text-xs">{JSON.stringify(c, null, 2)}</pre>
    </div>
  );
}
