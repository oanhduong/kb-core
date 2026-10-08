// Đăng nhập / đăng ký. Bố cục chia đôi: trái là bảng ink với logo + một câu khẳng định (mọi câu trả lời truy được về nguồn),
// phải là biểu mẫu. AuthShell + LangSwitch dùng lại cho màn tạo workspace (Onboarding).
import clsx from "clsx";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { ErrorBox, Field, Spinner } from "@/components/ui";
import { useI18n, useT } from "@/i18n";
import { Logo } from "@/layout/AppShell";
import { useAuth } from "@/state/auth";

export function LangSwitch({ className }: { className?: string }) {
  const { t, lang, setLang } = useI18n();
  return (
    <div role="group" aria-label={t("menu.language")} className={clsx("inline-flex rounded-[10px] border border-white/15 p-0.5 text-xs", className)}>
      {(["vi", "en"] as const).map((l) => (
        <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)} lang={l}
          className={clsx("rounded-lg px-2.5 py-1 font-semibold transition", lang === l ? "bg-white text-ink-900" : "text-ink-300 hover:text-white")}>
          {l === "vi" ? "VI" : "EN"}
        </button>
      ))}
    </div>
  );
}

/** Ví dụ thật nhỏ về một câu trả lời có trích dẫn — phần tô dạ quang là bằng chứng trong file nguồn. */
function EvidenceSample() {
  const t = useT();
  return (
    <figure className="max-w-md rounded-2xl bg-white/[.06] p-5 ring-1 ring-white/10" aria-label={t("auth.demo.label")}>
      <div className="text-[13px] text-ink-300">{t("auth.demo.question")}</div>
      <p className="mt-2 text-[15px] leading-7 text-white">
        {t("auth.demo.answer")}
        <span className="cite-chip">1</span>
      </p>
      <figcaption className="mt-4 rounded-xl bg-white p-3.5 text-[13px] leading-6 text-ink-800">
        <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-ink-600">
          <span className="cite-chip mx-0">1</span>{t("auth.demo.source")}
        </div>
        {t("auth.demo.quoteBefore")} <mark className="marker text-ink-900">{t("auth.demo.quoteMark")}</mark>{t("auth.demo.quoteAfter")}
      </figcaption>
    </figure>
  );
}

export function AuthShell({ title, subtitle, children, wide, top }: { title: string; subtitle?: ReactNode; children: ReactNode; wide?: boolean; top?: ReactNode }) {
  const t = useT();
  return (
    <div className="min-h-screen bg-paper lg:grid lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <aside className="relative flex flex-col overflow-hidden bg-ink-900 px-5 py-6 text-white sm:px-10 lg:min-h-screen lg:px-14 lg:py-12">
        <div className="flex items-center justify-between gap-3">
          <Logo light />
          <LangSwitch />
        </div>
        <div className="mt-8 lg:mt-auto">
          <h2 className="max-w-xl font-display text-[30px] font-semibold leading-[1.05] tracking-tight sm:text-[40px] lg:text-[54px]">
            {t("auth.statement.lead")} <span className="text-ink-300">{t("auth.statement.tail")}</span>
          </h2>
          <p className="mt-4 max-w-md text-[15px] leading-7 text-ink-300 lg:mt-6">{t("auth.statement.sub")}</p>
          <div className="mt-6 hidden max-w-md items-center gap-3 text-sm sm:flex lg:mt-8">
            <span className="text-ink-300">{t("auth.flow.files")}</span>
            <span className="h-px flex-1 bg-gradient-to-r from-white/10 to-white/40" aria-hidden />
            <span className="font-medium text-white">{t("auth.flow.answers")}</span>
            <span className="font-medium text-white">{t("auth.flow.numbers")}</span>
            <span className="font-medium text-white">{t("auth.flow.reports")}</span>
          </div>
        </div>
        <div className="mt-10 hidden lg:mb-auto lg:mt-12 lg:block"><EvidenceSample /></div>
        <p className="mt-8 hidden text-xs text-ink-400 lg:block">{t("auth.footer")}</p>
      </aside>
      <main className="flex items-center justify-center px-4 py-10 sm:px-10 lg:py-12">
        <div className={clsx("w-full animate-rise", wide ? "max-w-lg" : "max-w-sm")}>
          {top}
          <h1 className="font-display text-[28px] font-semibold leading-tight text-ink-900">{title}</h1>
          {subtitle && <p className="mt-2 text-[15px] leading-6 text-ink-600">{subtitle}</p>}
          <div className="mt-7">{children}</div>
        </div>
      </main>
    </div>
  );
}

export function LoginPage() {
  const { user, login, loading } = useAuth();
  const t = useT();
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
    <AuthShell title={t("auth.login.title")} subtitle={t("auth.login.subtitle")}>
      <form className="space-y-4" onSubmit={submit}>
        <Field label={t("auth.field.email")}><input className="input py-2.5" type="email" autoComplete="email" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label={t("auth.field.password")}><input className="input py-2.5" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        {err ? <ErrorBox error={err} /> : null}
        <button className="btn-primary w-full py-2.5" disabled={busy}>{busy && <Spinner className="text-white" />} {t("auth.login.submit")}</button>
      </form>
      <p className="mt-6 text-sm text-ink-600">{t("auth.login.noAccount")} <Link className="link" to="/signup">{t("auth.login.toSignup")}</Link></p>
    </AuthShell>
  );
}

export function SignupPage() {
  const { user, signup, loading } = useAuth();
  const t = useT();
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
    <AuthShell title={t("auth.signup.title")} subtitle={t("auth.signup.subtitle")}>
      <form className="space-y-4" onSubmit={submit}>
        <Field label={t("auth.field.name")}><input className="input py-2.5" autoComplete="name" autoFocus required value={form.name} onChange={set("name")} /></Field>
        <Field label={t("auth.field.email")}><input className="input py-2.5" type="email" autoComplete="email" required value={form.email} onChange={set("email")} /></Field>
        <Field label={t("auth.field.password")} hint={t("auth.field.passwordHint")}><input className="input py-2.5" type="password" autoComplete="new-password" minLength={8} required value={form.password} onChange={set("password")} /></Field>
        <Field label={t("auth.field.workspace")} hint={t("auth.field.workspaceHint")}><input className="input py-2.5" placeholder={t("auth.field.workspacePlaceholder")} value={form.workspace_name} onChange={set("workspace_name")} /></Field>
        {err ? <ErrorBox error={err} /> : null}
        <button className="btn-primary w-full py-2.5" disabled={busy}>{busy && <Spinner className="text-white" />} {t("auth.signup.submit")}</button>
      </form>
      <p className="mt-6 text-sm text-ink-600">{t("auth.signup.haveAccount")} <Link className="link" to="/login">{t("auth.signup.toLogin")}</Link></p>
    </AuthShell>
  );
}
