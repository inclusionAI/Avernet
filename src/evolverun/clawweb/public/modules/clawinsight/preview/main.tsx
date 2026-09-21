import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom';
import InsightCenter from '../web/pages/InsightCenter';
import { HostHeader } from './HostHeader';
import './style.css';
createRoot(document.getElementById('root')!).render(<BrowserRouter>
  <div className="flex min-h-screen flex-col bg-gray-50">
    <HostHeader />
    <main className="flex-1"><Routes>
      <Route path="/" element={<Navigate to="/insight?module=monitoring" replace />} />
      <Route path="/insight" element={<InsightCenter />} />
      <Route path="*" element={<div className="p-8 text-sm text-gray-600">此本地预览仅提供监控页面。<Link className="text-blue-600" to="/insight?module=monitoring">返回 Agent 监控自愈</Link></div>} />
    </Routes></main>
  </div>
  <details className="preview-tools"><summary>本地预览</summary>
    <p>合成测试数据，不连接线上。</p>
    <a href="/preview/login/member">切换普通用户</a><a href="/preview/login/admin">切换监控管理员</a>
  </details>
</BrowserRouter>);
