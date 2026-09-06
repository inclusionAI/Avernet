import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Evolve from "./pages/Evolve.js";
import "./singlebox.css";

const root = document.getElementById("root");
if (!root) throw new Error("Singlebox root element is missing");

const queryClient = new QueryClient();
createRoot(root).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/evolve/*" element={<Evolve />} />
          <Route path="*" element={<Navigate to="/evolve" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
