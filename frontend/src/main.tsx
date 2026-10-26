import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { Loading } from "./components/common";
import { AuthProvider, useAuth } from "./lib/auth";
import { OpsPage, UsersPage } from "./pages/AdminPages";
import { RunPage } from "./pages/RunPage";
import { ScenarioPage } from "./pages/ScenarioPage";
import { LoginPage, SetupPage, WelcomePage } from "./pages/Welcome";
import { WorkspacePage } from "./pages/WorkspacePage";
import "./styles.css";

function Protected({ children, capability }: { children: ReactNode; capability?: string }) {
  const { me, loading, can } = useAuth();
  if (loading) return <Loading />;
  if (!me) return <Navigate to="/login" replace />;
  if (capability && !can(capability)) return <Navigate to="/app" replace />;
  return <Layout>{children}</Layout>;
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<WelcomePage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route
        path="/app"
        element={
          <Protected>
            <WorkspacePage />
          </Protected>
        }
      />
      <Route
        path="/app/scenarios/:id"
        element={
          <Protected>
            <ScenarioPage />
          </Protected>
        }
      />
      <Route
        path="/app/runs/:id"
        element={
          <Protected>
            <RunPage />
          </Protected>
        }
      />
      <Route
        path="/app/users"
        element={
          <Protected capability="users.manage">
            <UsersPage />
          </Protected>
        }
      />
      <Route
        path="/app/ops"
        element={
          <Protected capability="ops.view">
            <OpsPage />
          </Protected>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <AuthProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AuthProvider>
    </StrictMode>,
  );
}
