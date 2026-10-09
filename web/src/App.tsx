// Router: /login, /signup, /onboarding, /w/:wsId/* (shell theo workspace, gồm cả admin của superadmin), /admin (vào admin trong
// workspace gần nhất; chỉ hiện riêng khi superadmin chưa có workspace nào).
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { Loading } from "./components/ui";
import { auth } from "./lib/api";
import { AboutPage } from "./pages/About";
import { AdminPage } from "./pages/Admin";
import { AnalyticsPage } from "./pages/Analytics";
import { LoginPage, SignupPage } from "./pages/Auth";
import { ChatPage } from "./pages/Chat";
import { DocumentDetailPage } from "./pages/DocumentDetail";
import { DocumentsPage } from "./pages/Documents";
import { GraphPage } from "./pages/Graph";
import { HomePage } from "./pages/Home";
import { ModelStudioPage } from "./pages/ModelStudio";
import { ModelsPage } from "./pages/Models";
import { OnboardingPage } from "./pages/Onboarding";
import { ReportDetailPage } from "./pages/ReportDetail";
import { ReportsPage } from "./pages/Reports";
import { SettingsPage } from "./pages/Settings";
import { TablesPage } from "./pages/Tables";
import { TrustPage } from "./pages/Trust";
import { useAuth, WorkspaceProvider } from "./state/auth";

function RequireAuth({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function WorkspaceRoutes() {
  const { wsId = "" } = useParams();
  const { workspaces, user } = useAuth();
  const ws = workspaces.find((w) => w.id === wsId || w.slug === wsId);
  if (!ws) {
    if (user?.is_superadmin) return <Navigate to="/admin" replace />;
    return <Navigate to="/onboarding" replace />;
  }
  if (auth.workspace() !== ws.id) auth.setWorkspace(ws.id);
  return (
    <WorkspaceProvider ws={ws}>
      <AppShell>
        <Routes>
          <Route index element={<HomePage />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="chat/:sessionId" element={<ChatPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="documents/:documentId" element={<DocumentDetailPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="models/:modelId" element={<ModelStudioPage />} />
          <Route path="models/:modelId/:step" element={<ModelStudioPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="tables" element={<TablesPage />} />
          <Route path="tables/:tableId" element={<TablesPage />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="graph/:entityId" element={<GraphPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="reports/:reportId" element={<ReportDetailPage />} />
          <Route path="trust/*" element={<TrustPage />} />
          <Route path="ops/*" element={<Navigate to="../trust" replace />} />
          <Route path="settings/*" element={<SettingsPage />} />
          <Route path="about" element={<AboutPage />} />
          <Route path="admin/*" element={<AdminPage inShell />} />
          <Route path="*" element={<Navigate to="." replace />} />
        </Routes>
      </AppShell>
    </WorkspaceProvider>
  );
}

function Home() {
  const { workspaces, user } = useAuth();
  const saved = auth.workspace();
  const ws = workspaces.find((w) => w.id === saved) || workspaces[0];
  if (ws) return <Navigate to={`/w/${ws.id}`} replace />;
  if (user?.is_superadmin) return <Navigate to="/admin" replace />;
  return <Navigate to="/onboarding" replace />;
}

function AdminEntry() {
  const { workspaces } = useAuth();
  const { pathname } = useLocation();
  const saved = auth.workspace();
  const ws = workspaces.find((w) => w.id === saved) || workspaces[0];
  if (ws) return <Navigate to={`/w/${ws.id}${pathname}`} replace />;
  return <AdminPage />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/onboarding" element={<RequireAuth><OnboardingPage /></RequireAuth>} />
      <Route path="/admin/*" element={<RequireAuth><AdminEntry /></RequireAuth>} />
      <Route path="/w/:wsId/*" element={<RequireAuth><WorkspaceRoutes /></RequireAuth>} />
      <Route path="*" element={<RequireAuth><Home /></RequireAuth>} />
    </Routes>
  );
}
