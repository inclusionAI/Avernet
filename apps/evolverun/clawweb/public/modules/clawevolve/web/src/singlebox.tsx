import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Evolve from "./pages/Evolve.js";
import "./singlebox.css";

const root = document.getElementById("root");
if (!root) throw new Error("Singlebox root element is missing");

function SingleboxPage() {
  const info = useQuery({ queryKey: ["singlebox-info"], queryFn: async () => {
    const response = await fetch("/api/singlebox/info");
    if (!response.ok) throw new Error("Failed to read local configuration");
    return response.json() as Promise<{ model: string }>;
  } });
  if (info.error) return <p>无法读取 Singlebox 配置，请检查本地服务。</p>;
  if (!info.data) return <p>正在读取本地配置…</p>;
  return <Evolve version="openversion" singleboxModel={info.data.model} />;
}

const queryClient = new QueryClient();
createRoot(root).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <nav className="border-b border-gray-200 bg-white shadow-sm">
          <div className="mx-auto flex max-w-screen-2xl items-center px-4 sm:px-6 lg:px-8">
            <a href="/evolve" className="flex shrink-0 items-center gap-2 border-b-2 border-transparent py-3">
              <svg className="h-8 w-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <defs>
                  <linearGradient id="singleboxLogoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#6366f1" />
                    <stop offset="100%" stopColor="#06b6d4" />
                  </linearGradient>
                </defs>
                <path d="M12 2L2 7l10 5 10-5-10-5z" fill="url(#singleboxLogoGrad)" opacity="0.15" stroke="url(#singleboxLogoGrad)" />
                <path d="M2 17l10 5 10-5" stroke="url(#singleboxLogoGrad)" />
                <path d="M2 12l10 5 10-5" stroke="url(#singleboxLogoGrad)" />
                <circle cx="12" cy="2" r="1.5" fill="#6366f1" />
                <path d="M12 7v5l3 2" stroke="#6366f1" strokeWidth="2" />
              </svg>
              <span className="bg-gradient-to-r from-indigo-600 to-cyan-500 bg-clip-text text-lg font-bold text-transparent">AgentEvolve</span>
              <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">(开源版)</span>
            </a>
            <div className="flex min-w-0 flex-1 items-center">
              <a href="/evolve" className="border-b-2 border-blue-600 px-2 py-3 text-sm font-medium text-blue-600">Claw进化</a>
            </div>
          </div>
        </nav>
        <Routes>
          <Route path="/evolve/*" element={<SingleboxPage />} />
          <Route path="*" element={<Navigate to="/evolve" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
