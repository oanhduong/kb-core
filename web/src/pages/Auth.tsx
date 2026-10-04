import { Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { ErrorBox, Field, Spinner } from "@/components/ui";
import { useAuth } from "@/state/auth";

function Shell({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-brand-50 via-slate-50 to-slate-100 p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2 text-brand-700">
          <Sparkles className="h-6 w-6" />
          <span className="text-lg font-semibold tracking-tight">KB Explorer</span>
        </div>
        <div className="card p-6">
          <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
          <p className="mb-5 mt-1 text-sm text-slate-500">{subtitle}</p>
          {children}
        </div>
      </div>
    </div>
  );
}

export function LoginPage() {
  const { user, login, loading } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!loading && user) return <Navigate to="/" replace />;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await login(email.trim(), password);
      nav("/");
    } catch (ex) {
      setErr(ex);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Đăng nhập" subtitle="Khai phá dữ liệu từ tài liệu bạn đã tải lên.">
      <form className="space-y-3" onSubmit={submit}>
        <Field label="Email"><input className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label="Mật khẩu"><input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        {err ? <ErrorBox error={err} /> : null}
        <button className="btn-primary w-full" disabled={busy}>{busy && <Spinner className="text-white" />} Đăng nhập</button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-500">Chưa có tài khoản? <Link className="text-brand-700 hover:underline" to="/signup">Đăng ký</Link></p>
    </Shell>
  );
}

export function SignupPage() {
  const { user, signup, loading } = useAuth();
  const nav = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "", workspace_name: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!loading && user) return <Navigate to="/" replace />;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await signup({ ...form, email: form.email.trim(), workspace_name: form.workspace_name.trim() || undefined });
      nav("/");
    } catch (ex) {
      setErr(ex);
    } finally {
      setBusy(false);
    }
  };
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <Shell title="Tạo tài khoản" subtitle="Mỗi tài khoản có thể tạo nhiều workspace và chia sẻ cho đồng nghiệp.">
      <form className="space-y-3" onSubmit={submit}>
        <Field label="Tên hiển thị"><input className="input" required value={form.name} onChange={set("name")} /></Field>
        <Field label="Email"><input className="input" type="email" autoComplete="email" required value={form.email} onChange={set("email")} /></Field>
        <Field label="Mật khẩu" hint="Tối thiểu 8 ký tự"><input className="input" type="password" autoComplete="new-password" minLength={8} required value={form.password} onChange={set("password")} /></Field>
        <Field label="Workspace đầu tiên (tuỳ chọn)"><input className="input" placeholder="Ví dụ: Phòng kinh doanh" value={form.workspace_name} onChange={set("workspace_name")} /></Field>
        {err ? <ErrorBox error={err} /> : null}
        <button className="btn-primary w-full" disabled={busy}>{busy && <Spinner className="text-white" />} Đăng ký</button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-500">Đã có tài khoản? <Link className="text-brand-700 hover:underline" to="/login">Đăng nhập</Link></p>
    </Shell>
  );
}
