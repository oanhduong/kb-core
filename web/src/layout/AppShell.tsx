// Khung ứng dụng: sidebar điều hướng + chọn workspace + menu người dùng; nội dung cuộn riêng.
import clsx from "clsx";
import { BarChart3, Bot, ChevronsUpDown, FileText, Home, LogOut, Menu, MessageSquare, Network, Plus, Settings, Shield, Table2, Wrench, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth, useWorkspace } from "@/state/auth";
import { ROLE_LABEL } from "@/lib/format";

const NAV = [
  { to: "", icon: Home, label: "Tổng quan", end: true },
  { to: "chat", icon: MessageSquare, label: "Hỏi đáp & khai phá" },
  { to: "documents", icon: FileText, label: "Tài liệu" },
  { to: "tables", icon: Table2, label: "Bảng số liệu" },
  { to: "analytics", icon: BarChart3, label: "Phân tích số liệu" },
  { to: "graph", icon: Network, label: "Đồ thị tri thức" },
  { to: "reports", icon: Bot, label: "Báo cáo" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { ws, role, can } = useWorkspace();
  const { user, workspaces, logout } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [wsMenu, setWsMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setWsMenu(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const sidebar = (
    <aside className="flex h-full w-64 flex-col border-r border-slate-200 bg-white">
      <div className="relative px-3 pt-4" ref={menuRef}>
        <button className="flex w-full items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-left hover:bg-slate-50" onClick={() => setWsMenu((v) => !v)}>
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand-600 text-sm font-bold text-white">{ws.name.slice(0, 1).toUpperCase()}</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-slate-900">{ws.name}</span>
            <span className="block truncate text-xs text-slate-500">{ws.domain_pack_name} · {ROLE_LABEL[role]}</span>
          </span>
          <ChevronsUpDown className="h-4 w-4 text-slate-400" />
        </button>
        {wsMenu && (
          <div className="absolute left-3 right-3 z-30 mt-1 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
            <div className="max-h-64 overflow-y-auto py-1">
              {workspaces.map((w) => (
                <button key={w.id} className={clsx("flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50", w.id === ws.id && "bg-brand-50 text-brand-700")}
                  onClick={() => { setWsMenu(false); nav(`/w/${w.id}`); }}>
                  <span className="flex h-6 w-6 items-center justify-center rounded bg-slate-200 text-xs font-bold text-slate-700">{w.name.slice(0, 1).toUpperCase()}</span>
                  <span className="truncate">{w.name}</span>
                </button>
              ))}
            </div>
            <button className="flex w-full items-center gap-2 border-t border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50" onClick={() => { setWsMenu(false); nav("/onboarding?new=1"); }}>
              <Plus className="h-4 w-4" /> Tạo workspace mới
            </button>
          </div>
        )}
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => clsx("nav-item", isActive && "nav-item-active")} onClick={() => setOpen(false)}>
            <n.icon className="h-4 w-4" /> {n.label}
          </NavLink>
        ))}
        {can("editor") && (
          <>
            <div className="px-3 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Vận hành</div>
            <NavLink to="ops" className={({ isActive }) => clsx("nav-item", isActive && "nav-item-active")} onClick={() => setOpen(false)}><Wrench className="h-4 w-4" /> Giám sát & duyệt</NavLink>
          </>
        )}
        <NavLink to="settings" className={({ isActive }) => clsx("nav-item", isActive && "nav-item-active")} onClick={() => setOpen(false)}><Settings className="h-4 w-4" /> Cài đặt workspace</NavLink>
        {user?.is_superadmin && (
          <NavLink to="/admin" className={({ isActive }) => clsx("nav-item", isActive && "nav-item-active")}><Shield className="h-4 w-4" /> Quản trị hệ thống</NavLink>
        )}
      </nav>
      <div className="border-t border-slate-200 p-3">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700">{(user?.name || user?.email || "?").slice(0, 1).toUpperCase()}</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-slate-800">{user?.name || user?.email}</span>
            <span className="block truncate text-xs text-slate-500">{user?.email}</span>
          </span>
          <button className="btn-ghost btn-sm" title="Đăng xuất" onClick={() => { logout(); nav("/login"); }}><LogOut className="h-4 w-4" /></button>
        </div>
      </div>
    </aside>
  );

  return (
    <div className="flex h-screen overflow-hidden">
      <div className="hidden lg:block">{sidebar}</div>
      {open && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setOpen(false)} />
          <div className="relative z-10">{sidebar}</div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center gap-2 border-b border-slate-200 bg-white px-3 lg:hidden">
          <button className="btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>{open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}</button>
          <span className="text-sm font-semibold">{ws.name}</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}

export function Page({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return <div className={clsx("mx-auto w-full px-4 py-6 sm:px-6", wide ? "max-w-[1600px]" : "max-w-6xl")}>{children}</div>;
}
