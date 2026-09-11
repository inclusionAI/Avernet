import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./style.css";

const WorkflowWorkspace = lazy(() => import("@avernet/workflow/web/pages/WorkflowWorkspace"));
const InsightCenter = lazy(() => import("@avernet/clawinsight/web/pages/InsightCenter/index"));
const ClawevolveApp = lazy(() => import("@avernet/clawevolve/web").then(({ ClawevolveApp }) => ({ default: ClawevolveApp })));

const queryClient = new QueryClient();

function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="clawweb-header">
        <div className="clawweb-header-inner">
          <NavLink to="/" className="clawweb-brand" aria-label="AgentEvolve 首页">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <defs><linearGradient id="clawweb-logo-gradient" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stopColor="#6366f1" /><stop offset="100%" stopColor="#06b6d4" /></linearGradient></defs>
              <path d="M12 2 2 7l10 5 10-5-10-5Z" fill="url(#clawweb-logo-gradient)" opacity=".15" stroke="url(#clawweb-logo-gradient)" />
              <path d="m2 17 10 5 10-5M2 12l10 5 10-5" stroke="url(#clawweb-logo-gradient)" />
              <circle cx="12" cy="2" r="1.5" fill="#6366f1" /><path d="M12 7v5l3 2" stroke="#6366f1" strokeWidth="2" />
            </svg>
            <span>AgentEvolve</span>
          </NavLink>
          <nav aria-label="AgentEvolve 主导航" className="clawweb-primary-nav">
            <NavLink to="/workflows/workspace">任务护航</NavLink>
            <NavLink to="/insight">效果中心</NavLink>
            <NavLink to="/evolve">Claw进化</NavLink>
          </nav>
        </div>
      </header>
      <Suspense fallback={<div className="p-6">加载中...</div>}>
        <Routes>
          <Route path="/" element={<Navigate to="/evolve" replace />} />
          <Route path="/workflows/workspace" element={<WorkflowWorkspace />} />
          <Route path="/insight" element={<InsightCenter />} />
          <Route path="/evolve/*" element={<ClawevolveApp />} />
        </Routes>
      </Suspense>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter><App /></BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
