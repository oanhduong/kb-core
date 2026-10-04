// Trạng thái đăng nhập + workspace hiện tại (localStorage token, /auth/me để lấy user + workspaces).
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, auth, setUnauthorizedHandler } from "@/lib/api";
import type { Role, User, Workspace } from "@/lib/types";

interface AuthState {
  user: User | null;
  workspaces: Workspace[];
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (b: { email: string; password: string; name: string; workspace_name?: string }) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState<boolean>(!!auth.token());

  const refresh = useCallback(async () => {
    if (!auth.token()) {
      setUser(null);
      setWorkspaces([]);
      setLoading(false);
      return;
    }
    try {
      const me = await api.me();
      setUser(me.user);
      setWorkspaces(me.workspaces);
    } catch {
      auth.setToken(null);
      setUser(null);
      setWorkspaces([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      auth.setToken(null);
      setUser(null);
      setWorkspaces([]);
    });
    void refresh();
  }, [refresh]);

  const value = useMemo<AuthState>(() => ({
    user, workspaces, loading, refresh,
    async login(email, password) {
      const r = await api.login({ email, password });
      auth.setToken(r.access_token);
      await refresh();
    },
    async signup(b) {
      const r = await api.signup(b);
      auth.setToken(r.access_token);
      if (r.workspace) auth.setWorkspace(r.workspace.id);
      await refresh();
    },
    logout() {
      auth.setToken(null);
      auth.setWorkspace(null);
      setUser(null);
      setWorkspaces([]);
    },
  }), [user, workspaces, loading, refresh]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth ngoài AuthProvider");
  return v;
}

// ---------------------------------------------------------------- workspace hiện tại
interface WsState {
  ws: Workspace;
  wsId: string;
  role: Role;
  can: (min: Role) => boolean;
}
const WsCtx = createContext<WsState | null>(null);
const RANK: Record<Role, number> = { viewer: 0, editor: 1, owner: 2 };

export function WorkspaceProvider({ ws, children }: { ws: Workspace; children: ReactNode }) {
  const { user } = useAuth();
  const role: Role = user?.is_superadmin ? "owner" : ws.role || "viewer";
  const value = useMemo<WsState>(() => ({ ws, wsId: ws.id, role, can: (min) => RANK[role] >= RANK[min] }), [ws, role]);
  return <WsCtx.Provider value={value}>{children}</WsCtx.Provider>;
}

export function useWorkspace(): WsState {
  const v = useContext(WsCtx);
  if (!v) throw new Error("useWorkspace ngoài WorkspaceProvider");
  return v;
}
