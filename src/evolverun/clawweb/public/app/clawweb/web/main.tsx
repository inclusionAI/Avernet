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
      <nav className="flex items-center gap-4 border-b bg-white px-6 py-3">
        <strong className="mr-4 text-lg">AgentEvolve</strong>
        <NavLink to="/workflows/workspace">任务护航</NavLink>
        <NavLink to="/insight">效果中心</NavLink>
        <NavLink to="/evolve">Claw进化</NavLink>
      </nav>
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
