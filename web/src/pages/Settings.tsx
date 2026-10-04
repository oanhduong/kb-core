// Cài đặt workspace: thông tin chung (owner), thành viên & lời mời (owner), API key (owner), tài khoản cá nhân.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Copy, Key, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Badge, Confirm, ErrorBox, Field, Loading, Modal, PageHeader, Spinner, useToast } from "@/components/ui";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtDate, ROLE_LABEL } from "@/lib/format";
import type { Role } from "@/lib/types";
import { useAuth, useWorkspace } from "@/state/auth";

export function SettingsPage() {
  const { can } = useWorkspace();
  const tabs = [{ to: "general", label: "Chung" }, ...(can("owner") ? [{ to: "members", label: "Thành viên" }, { to: "api-keys", label: "API key" }] : []), { to: "account", label: "Tài khoản của tôi" }];
  return (
    <Page>
      <PageHeader title="Cài đặt" />
      <div className="mb-4 flex gap-1 border-b border-slate-200">{tabs.map((t) => <NavLink key={t.to} to={t.to} className={({ isActive }) => clsx("-mb-px border-b-2 px-3 py-2 text-sm font-medium", isActive ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800")}>{t.label}</NavLink>)}</div>
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

function General() {
  const { ws, can } = useWorkspace();
  const { refresh } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const packs = useQuery({ queryKey: ["domain-packs"], queryFn: api.domainPacks });
  const [form, setForm] = useState({ name: ws.name, description: ws.description || "", domain_pack: ws.domain_pack });
  const [archive, setArchive] = useState(false);
  const save = useMutation({ mutationFn: () => api.updateWorkspace(ws.id, { name: form.name.trim(), description: form.description.trim() || null, domain_pack: form.domain_pack } as never), onSuccess: async () => { toast.ok("Đã lưu"); await refresh(); }, onError: toast.err });
  const arch = useMutation({ mutationFn: () => api.archiveWorkspace(ws.id), onSuccess: async () => { toast.ok("Đã lưu trữ workspace"); await refresh(); nav("/"); }, onError: toast.err });
  return (
    <div className="max-w-2xl space-y-6">
      <section className="card p-5">
        <h2 className="mb-4 text-base font-semibold text-slate-900">Thông tin workspace</h2>
        <div className="space-y-3">
          <Field label="Tên"><input className="input" value={form.name} disabled={!can("owner")} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Mô tả"><textarea className="input" rows={2} value={form.description} disabled={!can("owner")} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <Field label="Domain pack" hint="Quyết định cách phân loại tài liệu, câu hỏi gợi ý, chính sách duyệt câu trả lời (ví dụ: liên quan tiền) và đồ thị quy tắc (retail).">
            <select className="input" value={form.domain_pack} disabled={!can("owner")} onChange={(e) => setForm({ ...form, domain_pack: e.target.value })}>{packs.data?.map((p) => <option key={p.key} value={p.key}>{p.name} — {p.description}</option>)}</select>
          </Field>
          <div className="text-xs text-slate-500">ID: <code>{ws.id}</code> · slug: <code>{ws.slug}</code> · tạo {fmtDate(ws.created_at)}</div>
        </div>
        {can("owner") && <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending && <Spinner className="text-white" />} Lưu</button></div>}
      </section>
      {can("owner") && (
        <section className="card border-red-200 p-5">
          <h2 className="mb-1 text-base font-semibold text-red-700">Lưu trữ workspace</h2>
          <p className="mb-3 text-sm text-slate-600">Workspace sẽ ẩn với mọi thành viên; dữ liệu được giữ lại để có thể khôi phục bởi quản trị hệ thống.</p>
          <button className="btn-danger" onClick={() => setArchive(true)}>Lưu trữ workspace</button>
          <Confirm open={archive} onClose={() => setArchive(false)} onConfirm={() => arch.mutate()} danger title="Lưu trữ workspace" message={`Lưu trữ "${ws.name}"?`} busy={arch.isPending} />
        </section>
      )}
    </div>
  );
}

function Members() {
  const { ws } = useWorkspace();
  const { user } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["members", ws.id], queryFn: () => api.members(ws.id) });
  const [invite, setInvite] = useState(false);
  const [form, setForm] = useState<{ email: string; role: Role; attr: string }>({ email: "", role: "viewer", attr: "" });
  const [remove, setRemove] = useState<string | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["members", ws.id] });
  const attrKey = ws.member_attributes[0]?.key;
  const inv = useMutation({ mutationFn: () => api.invite(ws.id, { email: form.email.trim(), role: form.role, attributes: attrKey && form.attr ? { [attrKey]: form.attr } : undefined }), onSuccess: () => { toast.ok("Đã thêm/mời thành viên"); setInvite(false); setForm({ email: "", role: "viewer", attr: "" }); refresh(); }, onError: toast.err });
  const upd = useMutation({ mutationFn: ({ uid, role }: { uid: string; role: Role }) => api.updateMember(ws.id, uid, { role }), onSuccess: refresh, onError: toast.err });
  const rm = useMutation({ mutationFn: (uid: string) => api.removeMember(ws.id, uid), onSuccess: () => { setRemove(null); refresh(); }, onError: toast.err });
  const cancel = useMutation({ mutationFn: (id: string) => api.cancelInvite(ws.id, id), onSuccess: refresh, onError: toast.err });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error || "—"} />;
  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex justify-end"><button className="btn-primary" onClick={() => setInvite(true)}><UserPlus className="h-4 w-4" /> Thêm thành viên</button></div>
      <div className="card overflow-hidden">
        <table className="w-full"><thead><tr><th className="th">Thành viên</th><th className="th">Vai trò</th>{attrKey && <th className="th">{ws.member_attributes[0].label}</th>}<th className="th">Tham gia</th><th className="th" /></tr></thead>
          <tbody>
            {q.data.members.map((m) => (
              <tr key={m.user_id} className="hover:bg-slate-50">
                <td className="td"><div className="font-medium text-slate-900">{m.name}</div><div className="text-xs text-slate-500">{m.email}</div></td>
                <td className="td">{m.user_id === ws.owner_id ? <Badge tone="brand">Chủ sở hữu</Badge> : <select className="input w-36 py-1" value={m.role} disabled={m.user_id === user?.id} onChange={(e) => upd.mutate({ uid: m.user_id, role: e.target.value as Role })}>{(["viewer", "editor", "owner"] as Role[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select>}</td>
                {attrKey && <td className="td text-xs">{String(m.attributes?.[attrKey] ?? "—")}</td>}
                <td className="td text-xs">{fmtDate(m.joined_at)}</td>
                <td className="td text-right">{m.user_id !== ws.owner_id && m.user_id !== user?.id && <button className="btn-ghost btn-sm" onClick={() => setRemove(m.user_id)}><Trash2 className="h-3.5 w-3.5" /></button>}</td>
              </tr>
            ))}
            {q.data.pending_invites.map((i) => (
              <tr key={i.invite_id} className="bg-amber-50/40">
                <td className="td"><div className="text-slate-700">{i.email}</div><div className="text-xs text-amber-700">Chờ người này đăng ký</div></td>
                <td className="td">{ROLE_LABEL[i.role]}</td>{attrKey && <td className="td text-xs">{String(i.attributes?.[attrKey] ?? "—")}</td>}<td className="td text-xs">{fmtDate(i.created_at)}</td>
                <td className="td text-right"><button className="btn-ghost btn-sm" onClick={() => cancel.mutate(i.invite_id)}>Huỷ mời</button></td>
              </tr>
            ))}
          </tbody></table>
      </div>
      <p className="text-xs text-slate-500">Người xem: hỏi đáp, xem tài liệu/bảng/đồ thị/báo cáo. Biên tập: thêm tải tài liệu, sửa, duyệt câu trả lời, tạo báo cáo. Chủ sở hữu: thêm quản lý thành viên, API key, cài đặt.</p>
      <Modal open={invite} onClose={() => setInvite(false)} title="Thêm thành viên" footer={<><button className="btn-secondary" onClick={() => setInvite(false)}>Huỷ</button><button className="btn-primary" disabled={!form.email.trim() || inv.isPending} onClick={() => inv.mutate()}>Thêm</button></>}>
        <div className="space-y-3">
          <Field label="Email" hint="Nếu người này chưa có tài khoản, họ sẽ tự động vào workspace khi đăng ký bằng email này."><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Vai trò"><select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>{(["viewer", "editor", "owner"] as Role[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select></Field>
          {attrKey && <Field label={ws.member_attributes[0].label} hint={ws.member_attributes[0].description}><input className="input" value={form.attr} onChange={(e) => setForm({ ...form, attr: e.target.value })} /></Field>}
        </div>
      </Modal>
      <Confirm open={!!remove} onClose={() => setRemove(null)} onConfirm={() => remove && rm.mutate(remove)} danger title="Gỡ thành viên" message="Người này sẽ không còn truy cập được workspace." busy={rm.isPending} />
    </div>
  );
}

function ApiKeys() {
  const { ws } = useWorkspace();
  const toast = useToast();
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["api-keys", ws.id], queryFn: () => api.apiKeys(ws.id) });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", role: "viewer", rate: "" });
  const [created, setCreated] = useState<string | null>(null);
  const [revoke, setRevoke] = useState<string | null>(null);
  const mk = useMutation({ mutationFn: () => api.createApiKey(ws.id, { name: form.name.trim(), role: form.role, rate_limit_per_minute: form.rate ? Number(form.rate) : undefined }), onSuccess: (r) => { setCreated(r.api_key); setOpen(false); setForm({ name: "", role: "viewer", rate: "" }); void qc.invalidateQueries({ queryKey: ["api-keys", ws.id] }); }, onError: toast.err });
  const rv = useMutation({ mutationFn: (id: string) => api.revokeApiKey(ws.id, id), onSuccess: () => { setRevoke(null); void qc.invalidateQueries({ queryKey: ["api-keys", ws.id] }); }, onError: toast.err });
  return (
    <div className="max-w-3xl space-y-4">
      <p className="text-sm text-slate-600">API key dùng cho tích hợp (hệ thống khác gọi <code>/w/{ws.id}/…</code> với header <code>X-API-Key</code>). Key chỉ hiển thị một lần khi tạo.</p>
      <div className="flex justify-end"><button className="btn-primary" onClick={() => setOpen(true)}><Key className="h-4 w-4" /> Tạo API key</button></div>
      {keys.isLoading && <Loading />}
      {keys.data && (
        <div className="card overflow-hidden"><table className="w-full"><thead><tr><th className="th">Tên</th><th className="th">Prefix</th><th className="th">Vai trò</th><th className="th">Giới hạn/phút</th><th className="th">Dùng lần cuối</th><th className="th" /></tr></thead>
          <tbody>{keys.data.length === 0 && <tr><td className="td text-center text-slate-500" colSpan={6}>Chưa có API key</td></tr>}
            {keys.data.map((k) => <tr key={k.id} className={clsx("hover:bg-slate-50", k.active === false && "opacity-50")}><td className="td font-medium">{k.name}</td><td className="td font-mono text-xs">{k.prefix}…</td><td className="td">{ROLE_LABEL[k.role] || k.role}</td><td className="td text-xs">{k.rate_limit_per_minute ?? "mặc định"}</td><td className="td text-xs">{fmtDate(k.last_used_at, true)}</td><td className="td text-right">{k.active !== false && <button className="btn-ghost btn-sm" onClick={() => setRevoke(k.id)}>Thu hồi</button>}</td></tr>)}
          </tbody></table></div>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="Tạo API key" footer={<><button className="btn-secondary" onClick={() => setOpen(false)}>Huỷ</button><button className="btn-primary" disabled={!form.name.trim() || mk.isPending} onClick={() => mk.mutate()}>Tạo</button></>}>
        <div className="space-y-3">
          <Field label="Tên (hệ thống dùng key)"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="pos-gateway, zapier…" /></Field>
          <Field label="Vai trò"><select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{(["viewer", "editor", "owner"] as Role[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select></Field>
          <Field label="Giới hạn request/phút (tuỳ chọn)"><input className="input" type="number" min={1} value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} /></Field>
        </div>
      </Modal>
      <Modal open={!!created} onClose={() => setCreated(null)} title="API key mới" footer={<button className="btn-primary" onClick={() => setCreated(null)}>Đã lưu lại</button>}>
        <p className="mb-2 text-sm text-slate-600">Sao chép và lưu ngay — key không hiển thị lại.</p>
        <div className="flex items-center gap-2 rounded-lg bg-slate-900 p-3 font-mono text-xs text-slate-100"><span className="flex-1 break-all">{created}</span><button className="btn-ghost btn-sm text-slate-300" onClick={() => { void navigator.clipboard.writeText(created || ""); toast.ok("Đã chép"); }}><Copy className="h-3.5 w-3.5" /></button></div>
      </Modal>
      <Confirm open={!!revoke} onClose={() => setRevoke(null)} onConfirm={() => revoke && rv.mutate(revoke)} danger title="Thu hồi API key" message="Mọi tích hợp đang dùng key này sẽ bị từ chối ngay." busy={rv.isPending} />
    </div>
  );
}

function Account() {
  const { user, refresh } = useAuth();
  const toast = useToast();
  const [name, setName] = useState(user?.name || "");
  const [pw, setPw] = useState({ old: "", new1: "", new2: "" });
  const saveName = useMutation({ mutationFn: () => api.updateMe({ name: name.trim() }), onSuccess: async () => { toast.ok("Đã lưu"); await refresh(); }, onError: toast.err });
  const savePw = useMutation({ mutationFn: () => api.updateMe({ old_password: pw.old, new_password: pw.new1 }), onSuccess: () => { toast.ok("Đã đổi mật khẩu"); setPw({ old: "", new1: "", new2: "" }); }, onError: toast.err });
  return (
    <div className="max-w-xl space-y-6">
      <section className="card p-5"><h2 className="mb-3 text-base font-semibold">Hồ sơ</h2>
        <Field label="Tên hiển thị"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <div className="mt-2 text-xs text-slate-500">Email: {user?.email}{user?.is_superadmin && <Badge tone="violet" className="ml-2">Quản trị hệ thống</Badge>}</div>
        <div className="mt-3 flex justify-end"><button className="btn-primary" disabled={saveName.isPending || !name.trim()} onClick={() => saveName.mutate()}>Lưu</button></div></section>
      <section className="card p-5"><h2 className="mb-3 text-base font-semibold">Đổi mật khẩu</h2>
        <div className="space-y-3">
          <Field label="Mật khẩu hiện tại"><input className="input" type="password" value={pw.old} onChange={(e) => setPw({ ...pw, old: e.target.value })} /></Field>
          <Field label="Mật khẩu mới"><input className="input" type="password" minLength={8} value={pw.new1} onChange={(e) => setPw({ ...pw, new1: e.target.value })} /></Field>
          <Field label="Nhập lại mật khẩu mới"><input className="input" type="password" value={pw.new2} onChange={(e) => setPw({ ...pw, new2: e.target.value })} /></Field>
        </div>
        <div className="mt-3 flex justify-end"><button className="btn-primary" disabled={savePw.isPending || !pw.old || pw.new1.length < 8 || pw.new1 !== pw.new2} onClick={() => savePw.mutate()}>Đổi mật khẩu</button></div></section>
    </div>
  );
}
