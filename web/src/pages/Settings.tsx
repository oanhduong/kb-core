// Cài đặt workspace: thông tin chung, thành viên & lời mời (chủ sở hữu), kết nối hệ thống khác / API key (chủ sở hữu),
// tài khoản cá nhân (hồ sơ, ngôn ngữ, chi tiết kỹ thuật, mật khẩu). Mã định danh chỉ hiện ở chế độ kỹ thuật.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, Copy, Eye, Languages, Link2, Pencil, PlugZap, ShieldCheck, SlidersHorizontal, Trash2, UserPlus } from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Badge, Confirm, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Segmented, Spinner, useToast } from "@/components/ui";
import { useI18n, useT } from "@/i18n";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtDate, fmtNumber, fmtRelative, roleLabel } from "@/lib/format";
import type { Role } from "@/lib/types";
import { PackChooser } from "@/pages/Onboarding";
import { useAuth, useWorkspace } from "@/state/auth";
import { Advanced, usePrefs } from "@/state/prefs";

const ROLES: Role[] = ["viewer", "editor", "owner"];

export function SettingsPage() {
  const { can } = useWorkspace();
  const t = useT();
  const tabs = [
    { to: "general", label: t("settings.tab.general") },
    ...(can("owner") ? [{ to: "members", label: t("settings.tab.members") }, { to: "api-keys", label: t("settings.tab.connections") }] : []),
    { to: "account", label: t("settings.tab.account") },
  ];
  return (
    <Page>
      <PageHeader title={t("settings.title")} subtitle={t("settings.subtitle")} />
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-ink-100" aria-label={t("settings.title")}>
        {tabs.map((tb) => (
          <NavLink key={tb.to} to={tb.to}
            className={({ isActive }) => clsx("-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition", isActive ? "border-ink-900 text-ink-900" : "border-transparent text-ink-600 hover:text-ink-900")}>
            {tb.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to="general" replace />} />
        <Route path="general" element={<General />} />
        <Route path="members" element={can("owner") ? <Members /> : <Navigate to="../general" replace />} />
        <Route path="api-keys" element={can("owner") ? <ApiKeys /> : <Navigate to="../general" replace />} />
        <Route path="account" element={<Account />} />
      </Routes>
    </Page>
  );
}

function Section({ title, hint, children, danger, className }: { title: string; hint?: ReactNode; children: ReactNode; danger?: boolean; className?: string }) {
  return (
    <section className={clsx("panel p-6", danger && "border-alert-500/25", className)}>
      <h2 className={clsx("font-display text-lg font-semibold", danger ? "text-alert-700" : "text-ink-900")}>{title}</h2>
      {hint && <p className="mt-1 text-sm leading-6 text-ink-600">{hint}</p>}
      <div className="mt-5">{children}</div>
    </section>
  );
}

function TechRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 text-xs">
      <span className="text-ink-600">{label}</span>
      <code className="truncate rounded bg-ink-100/60 px-1.5 py-0.5 font-mono text-ink-800">{value}</code>
    </div>
  );
}

// ---------------------------------------------------------------- chung
function General() {
  const { ws, can } = useWorkspace();
  const { refresh } = useAuth();
  const t = useT();
  const toast = useToast();
  const nav = useNavigate();
  const packs = useQuery({ queryKey: ["domain-packs"], queryFn: api.domainPacks });
  const [form, setForm] = useState({ name: ws.name, description: ws.description || "", domain_pack: ws.domain_pack });
  const [archive, setArchive] = useState(false);
  const isOwner = can("owner");
  const save = useMutation({ mutationFn: () => api.updateWorkspace(ws.id, { name: form.name.trim(), description: form.description.trim() || null, domain_pack: form.domain_pack } as never), onSuccess: async () => { toast.ok(t("action.saved")); await refresh(); }, onError: toast.err });
  const arch = useMutation({ mutationFn: () => api.archiveWorkspace(ws.id), onSuccess: async () => { toast.ok(t("settings.archive.done")); await refresh(); nav("/"); }, onError: toast.err });
  const dirty = form.name.trim() !== ws.name || (form.description.trim() || "") !== (ws.description || "") || form.domain_pack !== ws.domain_pack;
  return (
    <div className="max-w-3xl space-y-6">
      <Section title={t("settings.general.title")} hint={isOwner ? t("settings.general.hint") : t("settings.general.readonly")}>
        <div className="space-y-4">
          <Field label={t("settings.general.name")}><input className="input" value={form.name} disabled={!isOwner} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label={t("settings.general.description")} hint={t("settings.general.descriptionHint")}><textarea className="input" rows={2} value={form.description} disabled={!isOwner} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <div>
            <span className="label">{t("settings.general.pack")}</span>
            <p className="mb-2.5 text-xs leading-5 text-ink-600">{t("settings.general.packHint")}</p>
            {packs.isLoading ? <Loading /> : packs.error ? <ErrorBox error={packs.error} retry={() => packs.refetch()} /> : (
              <PackChooser packs={packs.data || []} value={form.domain_pack} onChange={(k) => setForm({ ...form, domain_pack: k })} disabled={!isOwner} />
            )}
          </div>
          <p className="text-xs text-ink-600">{t("settings.general.created", { date: fmtDate(ws.created_at) })}</p>
          <Advanced>
            <div className="well divide-y divide-ink-100 px-3 py-1.5">
              <TechRow label={t("settings.tech.workspaceId")} value={ws.id} />
              <TechRow label={t("settings.tech.slug")} value={ws.slug} />
              <TechRow label={t("settings.tech.pack")} value={ws.domain_pack} />
            </div>
          </Advanced>
        </div>
        {isOwner && (
          <div className="mt-6 flex justify-end">
            <button className="btn-primary" disabled={save.isPending || !dirty || !form.name.trim()} onClick={() => save.mutate()}>{save.isPending && <Spinner className="text-white" />} {t("settings.general.save")}</button>
          </div>
        )}
      </Section>
      {isOwner && (
        <Section danger title={t("settings.archive.title")} hint={t("settings.archive.hint")}>
          <button className="btn-danger" onClick={() => setArchive(true)}>{t("settings.archive.button")}</button>
          <Confirm open={archive} onClose={() => setArchive(false)} onConfirm={() => arch.mutate()} danger title={t("settings.archive.title")}
            message={t("settings.archive.confirm", { name: ws.name })} confirmLabel={t("settings.archive.button")} busy={arch.isPending} />
        </Section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- thành viên
const ROLE_ICON: Record<Role, typeof Eye> = { viewer: Eye, editor: Pencil, owner: ShieldCheck };

function RoleGuide() {
  const t = useT();
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {ROLES.map((r) => {
        const Icon = ROLE_ICON[r];
        return (
          <div key={r} className="well p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-900"><Icon className="h-4 w-4 text-ink-600" />{roleLabel(r)}</div>
            <p className="mt-1.5 text-xs leading-5 text-ink-700">{t(`settings.role.${r}.desc`)}</p>
          </div>
        );
      })}
    </div>
  );
}

function Members() {
  const { ws } = useWorkspace();
  const { user } = useAuth();
  const t = useT();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["members", ws.id], queryFn: () => api.members(ws.id) });
  const [invite, setInvite] = useState(false);
  const [form, setForm] = useState<{ email: string; role: Role; attr: string }>({ email: "", role: "viewer", attr: "" });
  const [remove, setRemove] = useState<string | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["members", ws.id] });
  const attr = ws.member_attributes[0];
  const attrKey = attr?.key;
  const inv = useMutation({ mutationFn: () => api.invite(ws.id, { email: form.email.trim(), role: form.role, attributes: attrKey && form.attr ? { [attrKey]: form.attr } : undefined }), onSuccess: () => { toast.ok(t("settings.members.invited")); setInvite(false); setForm({ email: "", role: "viewer", attr: "" }); refresh(); }, onError: toast.err });
  const upd = useMutation({ mutationFn: ({ uid, role }: { uid: string; role: Role }) => api.updateMember(ws.id, uid, { role }), onSuccess: () => { toast.ok(t("settings.members.roleChanged")); refresh(); }, onError: toast.err });
  const rm = useMutation({ mutationFn: (uid: string) => api.removeMember(ws.id, uid), onSuccess: () => { setRemove(null); refresh(); }, onError: toast.err });
  const cancel = useMutation({ mutationFn: (id: string) => api.cancelInvite(ws.id, id), onSuccess: refresh, onError: toast.err });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || t("error.title")} retry={() => q.refetch()} />;
  const removing = q.data.members.find((m) => m.user_id === remove);
  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold text-ink-900">{t("settings.members.title")}</h2>
          <p className="mt-1 text-sm text-ink-600">{t("settings.members.count", { n: q.data.members.length })}</p>
        </div>
        <button className="btn-primary" onClick={() => setInvite(true)}><UserPlus className="h-4 w-4" /> {t("settings.members.add")}</button>
      </div>
      <div className="panel overflow-x-auto">
        <table className="w-full min-w-[560px]">
          <thead><tr>
            <th className="th rounded-tl-2xl">{t("settings.members.col.person")}</th>
            <th className="th">{t("settings.members.col.role")}</th>
            {attr && <th className="th">{attr.label}</th>}
            <th className="th">{t("settings.members.col.joined")}</th>
            <th className="th rounded-tr-2xl"><span className="sr-only">{t("settings.members.col.actions")}</span></th>
          </tr></thead>
          <tbody>
            {q.data.members.map((m) => {
              const isOwnerRow = m.user_id === ws.owner_id;
              const isMe = m.user_id === user?.id;
              return (
                <tr key={m.user_id} className="hover:bg-paper/60">
                  <td className="td">
                    <div className="flex items-center gap-3">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink-100 text-xs font-bold text-ink-800">{(m.name || m.email).slice(0, 1).toUpperCase()}</span>
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-ink-900">{m.name}{isMe && <span className="ml-1.5 text-xs font-normal text-ink-600">{t("settings.members.you")}</span>}</span>
                        <span className="block truncate text-xs text-ink-600">{m.email}</span>
                      </span>
                    </div>
                  </td>
                  <td className="td">
                    {isOwnerRow ? <Badge tone="brand">{t("settings.members.workspaceOwner")}</Badge> : (
                      <select className="input w-40 py-1.5" aria-label={t("settings.members.col.role")} value={m.role} disabled={isMe || upd.isPending} onChange={(e) => upd.mutate({ uid: m.user_id, role: e.target.value as Role })}>
                        {ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
                      </select>
                    )}
                  </td>
                  {attrKey && <td className="td text-sm">{String(m.attributes?.[attrKey] ?? "—")}</td>}
                  <td className="td whitespace-nowrap text-xs text-ink-600">{fmtDate(m.joined_at)}</td>
                  <td className="td text-right">
                    {!isOwnerRow && !isMe && <button className="btn-ghost btn-sm" onClick={() => setRemove(m.user_id)} aria-label={t("settings.members.remove", { name: m.name || m.email })} title={t("settings.members.removeTitle")}><Trash2 className="h-3.5 w-3.5" /></button>}
                  </td>
                </tr>
              );
            })}
            {q.data.pending_invites.map((i) => (
              <tr key={i.invite_id}>
                <td className="td">
                  <div className="flex items-center gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-dashed border-ink-300 text-xs text-ink-400">?</span>
                    <span className="min-w-0">
                      <span className="block truncate text-ink-800">{i.email}</span>
                      <span className="block text-xs text-ink-600">{t("settings.members.pending")}</span>
                    </span>
                  </div>
                </td>
                <td className="td">{roleLabel(i.role)}</td>
                {attrKey && <td className="td text-sm">{String(i.attributes?.[attrKey] ?? "—")}</td>}
                <td className="td whitespace-nowrap text-xs text-ink-600">{t("settings.members.invitedOn", { date: fmtDate(i.created_at) })}</td>
                <td className="td text-right"><button className="btn-ghost btn-sm" disabled={cancel.isPending} onClick={() => cancel.mutate(i.invite_id)}>{t("settings.members.cancelInvite")}</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <h3 className="mb-3 font-display text-base font-semibold text-ink-900">{t("settings.roles.title")}</h3>
        <RoleGuide />
      </div>
      <Modal open={invite} onClose={() => setInvite(false)} title={t("settings.members.add")}
        footer={<><button className="btn-secondary" onClick={() => setInvite(false)}>{t("action.cancel")}</button><button className="btn-primary" disabled={!form.email.trim() || inv.isPending} onClick={() => inv.mutate()}>{inv.isPending && <Spinner className="text-white" />} {t("settings.members.addConfirm")}</button></>}>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (form.email.trim()) inv.mutate(); }}>
          <Field label={t("settings.members.email")} hint={t("settings.members.emailHint")}><input className="input" type="email" autoFocus value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <div>
            <span className="label">{t("settings.members.col.role")}</span>
            <div className="space-y-2">
              {ROLES.map((r) => (
                <label key={r} className={clsx("flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition", form.role === r ? "border-brand-400 bg-brand-50/60" : "border-ink-100 hover:border-ink-300")}>
                  <input type="radio" name="role" className="mt-1 accent-brand-600" checked={form.role === r} onChange={() => setForm({ ...form, role: r })} />
                  <span><span className="block text-sm font-medium text-ink-900">{roleLabel(r)}</span><span className="block text-xs leading-5 text-ink-600">{t(`settings.role.${r}.desc`)}</span></span>
                </label>
              ))}
            </div>
          </div>
          {attr && <Field label={attr.label} hint={attr.description}><input className="input" value={form.attr} onChange={(e) => setForm({ ...form, attr: e.target.value })} /></Field>}
        </form>
      </Modal>
      <Confirm open={!!remove} onClose={() => setRemove(null)} onConfirm={() => remove && rm.mutate(remove)} danger title={t("settings.members.removeTitle")}
        message={t("settings.members.removeMessage", { name: removing?.name || removing?.email || "" })} confirmLabel={t("settings.members.removeConfirm")} busy={rm.isPending} />
    </div>
  );
}

// ---------------------------------------------------------------- kết nối hệ thống khác (API key)
function ApiKeys() {
  const { ws } = useWorkspace();
  const t = useT();
  const { advanced } = usePrefs();
  const toast = useToast();
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["api-keys", ws.id], queryFn: () => api.apiKeys(ws.id) });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", role: "viewer", rate: "" });
  const [created, setCreated] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revoke, setRevoke] = useState<string | null>(null);
  const mk = useMutation({ mutationFn: () => api.createApiKey(ws.id, { name: form.name.trim(), role: form.role, rate_limit_per_minute: form.rate ? Number(form.rate) : undefined }), onSuccess: (r) => { setCreated(r.api_key); setCopied(false); setOpen(false); setForm({ name: "", role: "viewer", rate: "" }); void qc.invalidateQueries({ queryKey: ["api-keys", ws.id] }); }, onError: toast.err });
  const rv = useMutation({ mutationFn: (id: string) => api.revokeApiKey(ws.id, id), onSuccess: () => { setRevoke(null); toast.ok(t("settings.keys.revoked")); void qc.invalidateQueries({ queryKey: ["api-keys", ws.id] }); }, onError: toast.err });
  const copy = async () => { await navigator.clipboard.writeText(created || ""); setCopied(true); toast.ok(t("action.copied")); };
  const revoking = keys.data?.find((k) => k.id === revoke);
  return (
    <div className="max-w-4xl space-y-6">
      <section className="panel flex flex-col gap-5 p-6 sm:flex-row sm:items-start">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-700"><PlugZap className="h-5 w-5" /></span>
        <div className="flex-1">
          <h2 className="font-display text-lg font-semibold text-ink-900">{t("settings.keys.title")}</h2>
          <p className="mt-1 text-sm leading-6 text-ink-700">{t("settings.keys.explain")}</p>
          <ul className="mt-3 space-y-1.5 text-sm text-ink-700">
            <li className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-verify-600" />{t("settings.keys.point1")}</li>
            <li className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-verify-600" />{t("settings.keys.point2")}</li>
            <li className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-verify-600" />{t("settings.keys.point3")}</li>
          </ul>
          <Advanced>
            <div className="well mt-4 px-3 py-2 text-xs leading-5 text-ink-700">
              {t("settings.keys.tech")} <code className="font-mono">/w/{ws.id}/…</code>, <code className="font-mono">X-API-Key</code>
            </div>
          </Advanced>
        </div>
        <button className="btn-primary self-start" onClick={() => setOpen(true)}><Link2 className="h-4 w-4" /> {t("settings.keys.create")}</button>
      </section>

      {keys.isLoading && <Loading />}
      {keys.error && <ErrorBox error={keys.error} retry={() => keys.refetch()} />}
      {keys.data && keys.data.length === 0 && (
        <div className="panel"><Empty icon={<PlugZap />} title={t("settings.keys.empty")} hint={t("settings.keys.emptyHint")} /></div>
      )}
      {keys.data && keys.data.length > 0 && (
        <div className="panel overflow-x-auto">
          <table className="w-full min-w-[560px]">
            <thead><tr>
              <th className="th rounded-tl-2xl">{t("settings.keys.col.name")}</th>
              <th className="th">{t("settings.keys.col.access")}</th>
              {advanced && <th className="th">{t("settings.keys.col.prefix")}</th>}
              {advanced && <th className="th">{t("settings.keys.col.rate")}</th>}
              <th className="th">{t("settings.keys.col.lastUsed")}</th>
              <th className="th rounded-tr-2xl"><span className="sr-only">{t("settings.members.col.actions")}</span></th>
            </tr></thead>
            <tbody>
              {keys.data.map((k) => {
                const off = k.active === false;
                return (
                  <tr key={k.id} className={clsx("hover:bg-paper/60", off && "text-ink-400")}>
                    <td className="td">
                      <span className={clsx("font-medium", off ? "text-ink-400" : "text-ink-900")}>{k.name}</span>
                      {off && <Badge className="ml-2">{t("settings.keys.revokedBadge")}</Badge>}
                    </td>
                    <td className="td">{roleLabel(k.role) || k.role}</td>
                    {advanced && <td className="td font-mono text-xs">{k.prefix}…</td>}
                    {advanced && <td className="td text-xs">{k.rate_limit_per_minute ? fmtNumber(k.rate_limit_per_minute) : t("settings.keys.rateDefault")}</td>}
                    <td className="td whitespace-nowrap text-xs text-ink-600">{k.last_used_at ? fmtRelative(k.last_used_at) : t("settings.keys.neverUsed")}</td>
                    <td className="td text-right">{!off && <button className="btn-ghost btn-sm text-alert-700 hover:bg-alert-50 hover:text-alert-700" onClick={() => setRevoke(k.id)}>{t("settings.keys.revoke")}</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title={t("settings.keys.create")}
        footer={<><button className="btn-secondary" onClick={() => setOpen(false)}>{t("action.cancel")}</button><button className="btn-primary" disabled={!form.name.trim() || mk.isPending} onClick={() => mk.mutate()}>{mk.isPending && <Spinner className="text-white" />} {t("settings.keys.createConfirm")}</button></>}>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (form.name.trim()) mk.mutate(); }}>
          <Field label={t("settings.keys.name")} hint={t("settings.keys.nameHint")}><input className="input" autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={t("settings.keys.namePlaceholder")} /></Field>
          <Field label={t("settings.keys.access")} hint={t(`settings.role.${form.role}.desc`)}>
            <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}</select>
          </Field>
          <Advanced>
            <Field label={t("settings.keys.rate")} hint={t("settings.keys.rateHint")}><input className="input" type="number" min={1} value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} /></Field>
          </Advanced>
        </form>
      </Modal>
      <Modal open={!!created} onClose={() => setCreated(null)} title={t("settings.keys.newTitle")} footer={<button className="btn-primary" onClick={() => setCreated(null)}>{t("settings.keys.stored")}</button>}>
        <p className="mb-3 text-sm leading-6 text-ink-700">{t("settings.keys.newHint")}</p>
        <div className="flex items-center gap-2 rounded-xl bg-ink-950 p-3 font-mono text-xs text-ink-100">
          <span className="flex-1 select-all break-all">{created}</span>
          <button className="btn-ghost btn-sm text-ink-300 hover:bg-white/10 hover:text-white" onClick={() => void copy()} aria-label={t("action.copy")}>{copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}</button>
        </div>
      </Modal>
      <Confirm open={!!revoke} onClose={() => setRevoke(null)} onConfirm={() => revoke && rv.mutate(revoke)} danger title={t("settings.keys.revokeTitle")}
        message={t("settings.keys.revokeMessage", { name: revoking?.name || "" })} confirmLabel={t("settings.keys.revoke")} busy={rv.isPending} />
    </div>
  );
}

// ---------------------------------------------------------------- tài khoản
function Account() {
  const { user, refresh } = useAuth();
  const { t, lang, setLang } = useI18n();
  const { advanced, setAdvanced } = usePrefs();
  const toast = useToast();
  const [name, setName] = useState(user?.name || "");
  const [pw, setPw] = useState({ old: "", new1: "", new2: "" });
  const saveName = useMutation({ mutationFn: () => api.updateMe({ name: name.trim() }), onSuccess: async () => { toast.ok(t("action.saved")); await refresh(); }, onError: toast.err });
  const savePw = useMutation({ mutationFn: () => api.updateMe({ old_password: pw.old, new_password: pw.new1 }), onSuccess: () => { toast.ok(t("settings.password.done")); setPw({ old: "", new1: "", new2: "" }); }, onError: toast.err });
  const mismatch = pw.new2.length > 0 && pw.new1 !== pw.new2;
  return (
    <div className="max-w-2xl space-y-6">
      <Section title={t("settings.profile.title")}>
        <Field label={t("settings.profile.name")}><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-ink-700">
          <span className="text-ink-600">{t("settings.profile.email")}</span> {user?.email}
          {user?.is_superadmin && <Badge tone="brand">{t("settings.profile.superadmin")}</Badge>}
        </div>
        <div className="mt-5 flex justify-end"><button className="btn-primary" disabled={saveName.isPending || !name.trim() || name.trim() === user?.name} onClick={() => saveName.mutate()}>{saveName.isPending && <Spinner className="text-white" />} {t("action.save")}</button></div>
      </Section>

      <Section title={t("settings.prefs.title")} hint={t("settings.prefs.hint")}>
        <div className="divide-y divide-ink-100">
          <div className="flex flex-wrap items-center justify-between gap-3 pb-4">
            <div className="flex items-start gap-3">
              <Languages className="mt-0.5 h-[18px] w-[18px] text-ink-600" />
              <div><div className="text-sm font-medium text-ink-900">{t("settings.prefs.language")}</div><div className="text-xs text-ink-600">{t("settings.prefs.languageHint")}</div></div>
            </div>
            <Segmented value={lang} onChange={setLang} options={[{ key: "vi", label: "Tiếng Việt" }, { key: "en", label: "English" }]} />
          </div>
          <div className="flex items-start justify-between gap-3 pt-4">
            <div className="flex items-start gap-3">
              <SlidersHorizontal className="mt-0.5 h-[18px] w-[18px] text-ink-600" />
              <div><div className="text-sm font-medium text-ink-900">{t("menu.advanced")}</div><div className="text-xs leading-5 text-ink-600">{t("settings.prefs.advancedHint")}</div></div>
            </div>
            <button type="button" role="switch" aria-checked={advanced} aria-label={t("menu.advanced")} onClick={() => setAdvanced(!advanced)}
              className={clsx("mt-0.5 flex h-6 w-11 shrink-0 items-center rounded-full p-0.5 transition", advanced ? "bg-brand-600" : "bg-ink-100")}>
              <span className={clsx("h-5 w-5 rounded-full bg-white shadow transition", advanced && "translate-x-5")} />
            </button>
          </div>
        </div>
      </Section>

      <Section title={t("settings.password.title")}>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (pw.old && pw.new1.length >= 8 && pw.new1 === pw.new2) savePw.mutate(); }}>
          <Field label={t("settings.password.current")}><input className="input" type="password" autoComplete="current-password" value={pw.old} onChange={(e) => setPw({ ...pw, old: e.target.value })} /></Field>
          <Field label={t("settings.password.new")} hint={t("settings.password.rule")}><input className="input" type="password" autoComplete="new-password" minLength={8} value={pw.new1} onChange={(e) => setPw({ ...pw, new1: e.target.value })} /></Field>
          <Field label={t("settings.password.repeat")} hint={mismatch ? <span className="text-alert-700">{t("settings.password.mismatch")}</span> : undefined}>
            <input className="input" type="password" autoComplete="new-password" value={pw.new2} onChange={(e) => setPw({ ...pw, new2: e.target.value })} />
          </Field>
          <div className="flex justify-end"><button className="btn-primary" disabled={savePw.isPending || !pw.old || pw.new1.length < 8 || pw.new1 !== pw.new2}>{savePw.isPending && <Spinner className="text-white" />} {t("settings.password.submit")}</button></div>
        </form>
      </Section>
    </div>
  );
}
