// Khung ứng dụng: rail điều hướng màu ink (workspace, mục làm việc, quản lý) + menu người dùng (ngôn ngữ, chi tiết kỹ thuật).
import clsx from "clsx";
import {
  BarChart3, Boxes, Check, ChevronsUpDown, FileStack, Home, Languages, LogOut, Menu, MessageCircle, Network, Plus, Settings,
  Shield, ShieldCheck, SlidersHorizontal, UserRound, Workflow, X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useI18n } from "@/i18n";
import { roleLabel } from "@/lib/format";
import { useAuth, useWorkspace } from "@/state/auth";
import { usePrefs } from "@/state/prefs";

const WORK = [
  { to: "", icon: Home, key: "nav.home", end: true },
  { to: "chat", icon: MessageCircle, key: "nav.ask" },
  { to: "documents", icon: FileStack, key: "nav.sources" },
  { to: "models", icon: Workflow, key: "nav.models" },
  { to: "analytics", icon: BarChart3, key: "nav.explore" },
  { to: "reports", icon: Boxes, key: "nav.reports" },
  { to: "graph", icon: Network, key: "nav.graph" },
];

export function Logo({ className, light }: { className?: string; light?: boolean }) {
  return (
    <span className={clsx("inline-flex items-center gap-2", className)}>
      <svg viewBox="0 0 28 28" className="h-7 w-7" aria-hidden>
        <rect x="1" y="1" width="26" height="26" rx="8" fill={light ? "#FFFFFF" : "#1E1B4B"} fillOpacity={light ? 0.08 : 1} />
        <path d="M7 18.5h14" stroke="#FDE68A" strokeWidth="5" strokeLinecap="round" />
        <path d="M8 9.5h12M8 13.5h8" stroke={light ? "#FFFFFF" : "#B9B6D9"} strokeWidth="2" strokeLinecap="round" />
      </svg>
      <span className={clsx("font-display text-[17px] font-semibold tracking-tight", light ? "text-white" : "text-ink-900")}>KB Explorer</span>
    </span>
  );
}

function useOutside(ref: React.RefObject<HTMLElement>, onOut: () => void) {
  useEffect(() => {
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onOut(); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [ref, onOut]);
}

function UserMenu({ onNavigate }: { onNavigate: () => void }) {
  const { user, logout } = useAuth();
  const { t, lang, setLang } = useI18n();
  const { advanced, setAdvanced } = usePrefs();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, () => setOpen(false));
  return (
    <div className="relative" ref={ref}>
      {open && (
        <div className="absolute bottom-full left-0 right-0 z-40 mb-2 animate-rise rounded-2xl bg-white p-2 text-ink-900 shadow-lift">
          <div className="px-3 pb-2 pt-1">
            <div className="truncate text-sm font-semibold">{user?.name || user?.email}</div>
            <div className="truncate text-xs text-ink-600">{user?.email}</div>
          </div>
          <div className="flex items-center justify-between gap-2 rounded-xl px-3 py-2 text-sm">
            <span className="flex items-center gap-2"><Languages className="h-4 w-4 text-ink-600" />{t("menu.language")}</span>
            <div className="seg">
              <button aria-pressed={lang === "vi"} onClick={() => setLang("vi")}>VI</button>
              <button aria-pressed={lang === "en"} onClick={() => setLang("en")}>EN</button>
            </div>
          </div>
          <button className="flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left text-sm hover:bg-paper" onClick={() => setAdvanced(!advanced)} role="switch" aria-checked={advanced}>
            <SlidersHorizontal className="mt-0.5 h-4 w-4 text-ink-600" />
            <span className="flex-1"><span className="block">{t("menu.advanced")}</span><span className="block text-xs text-ink-600">{t("menu.advancedHint")}</span></span>
            <span className={clsx("mt-0.5 flex h-5 w-9 items-center rounded-full p-0.5 transition", advanced ? "bg-brand-600" : "bg-ink-100")}>
              <span className={clsx("h-4 w-4 rounded-full bg-white shadow transition", advanced && "translate-x-4")} />
            </span>
          </button>
          <NavLink to="settings/account" onClick={() => { setOpen(false); onNavigate(); }} className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm hover:bg-paper"><UserRound className="h-4 w-4 text-ink-600" />{t("menu.account")}</NavLink>
          <button className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-alert-700 hover:bg-alert-50" onClick={() => { logout(); nav("/login"); }}><LogOut className="h-4 w-4" />{t("menu.signOut")}</button>
        </div>
      )}
      <button className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-white/[.06]" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-marker-200 text-xs font-bold text-ink-900">{(user?.name || user?.email || "?").slice(0, 1).toUpperCase()}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-white">{user?.name || user?.email}</span>
          <span className="block text-[11px] text-ink-300">{lang.toUpperCase()}{advanced ? ` · ${t("menu.advanced")}` : ""}</span>
        </span>
        <ChevronsUpDown className="h-4 w-4 text-ink-400" />
      </button>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { ws, role, can } = useWorkspace();
  const { user, workspaces } = useAuth();
  const { t } = useI18n();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [wsMenu, setWsMenu] = useState(false);
  const wsRef = useRef<HTMLDivElement>(null);
  useOutside(wsRef, () => setWsMenu(false));
  const item = (to: string, Icon: typeof Home, label: string, end?: boolean) => (
    <NavLink key={to} to={to} end={end} className={({ isActive }) => clsx("nav-item", isActive && "nav-item-active")} onClick={() => setOpen(false)}>
      {({ isActive }) => (<><Icon className={clsx("h-[18px] w-[18px]", isActive ? "text-marker-200" : "text-ink-400")} /> {label}</>)}
    </NavLink>
  );

  const sidebar = (
    <aside className="flex h-full w-[248px] flex-col bg-ink-900 text-white">
      <div className="px-4 pb-3 pt-5"><Logo light /></div>
      <div className="relative px-3" ref={wsRef}>
        <button className="flex w-full items-center gap-2.5 rounded-xl bg-white/[.06] px-3 py-2.5 text-left hover:bg-white/10" onClick={() => setWsMenu((v) => !v)} aria-label={t("nav.switchWorkspace")}>
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-500 font-display text-sm font-bold">{ws.name.slice(0, 1).toUpperCase()}</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold">{ws.name}</span>
            <span className="block truncate text-[11px] text-ink-300">{roleLabel(role)}</span>
          </span>
          <ChevronsUpDown className="h-4 w-4 text-ink-400" />
        </button>
        {wsMenu && (
          <div className="absolute left-3 right-3 z-30 mt-2 animate-rise overflow-hidden rounded-2xl bg-white p-1.5 text-ink-900 shadow-lift">
            <div className="max-h-64 overflow-y-auto">
              {workspaces.map((w) => (
                <button key={w.id} className="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-sm hover:bg-paper" onClick={() => { setWsMenu(false); nav(`/w/${w.id}`); }}>
                  <span className="flex h-6 w-6 items-center justify-center rounded-md bg-ink-100 text-xs font-bold">{w.name.slice(0, 1).toUpperCase()}</span>
                  <span className="flex-1 truncate">{w.name}</span>
                  {w.id === ws.id && <Check className="h-4 w-4 text-brand-600" />}
                </button>
              ))}
            </div>
            <button className="mt-1 flex w-full items-center gap-2 rounded-xl border-t border-ink-100 px-2.5 py-2 text-sm text-ink-700 hover:bg-paper" onClick={() => { setWsMenu(false); nav("/onboarding?new=1"); }}>
              <Plus className="h-4 w-4" /> {t("nav.newWorkspace")}
            </button>
          </div>
        )}
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-5">
        {WORK.map((n) => item(n.to, n.icon, t(n.key), n.end))}
        <div className="px-3 pb-1.5 pt-6 text-[12px] font-medium text-ink-400">{t("nav.section.manage")}</div>
        {can("editor") && item("trust", ShieldCheck, t("nav.trust"))}
        {item("settings", Settings, t("nav.settings"))}
        {user?.is_superadmin && item("/admin", Shield, t("nav.admin"))}
      </nav>
      <div className="border-t border-white/[.08] p-3"><UserMenu onNavigate={() => setOpen(false)} /></div>
    </aside>
  );

  return (
    <div className="flex h-screen overflow-hidden">
      <div className="hidden lg:block">{sidebar}</div>
      {open && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="absolute inset-0 bg-ink-950/50" onClick={() => setOpen(false)} />
          <div className="relative z-10">{sidebar}</div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-2 border-b border-ink-100 bg-white px-3 lg:hidden">
          <button className="btn-ghost btn-sm" onClick={() => setOpen((v) => !v)} aria-label="menu">{open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}</button>
          <Logo />
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}

export function Page({ children, wide, className }: { children: ReactNode; wide?: boolean; className?: string }) {
  return <div className={clsx("mx-auto w-full px-4 py-7 sm:px-8", wide ? "max-w-[1480px]" : "max-w-6xl", className)}>{children}</div>;
}
