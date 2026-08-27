/**
 * ensureReactGlobal —— 为方案②/第三轨的 external-React UMD 组件兜底全局 React。
 *
 * 背景：bcs/assets/panel 等 UMD 资产构建时 external 了 react/react-dom，运行时从 window.React
 * 取；新 SDK 的 loadCDNComponent 不挂全局 React。故 teamclaw app 初始化挂一次全局，
 * 保证 external-React 的 UMD 可加载。不依赖 SDK 改动；SDK 后续补 ensureReactGlobal 工具为优化项。
 *
 * 安全：仅当 window.React 未挂时才赋值，避免覆盖宿主既有全局。
 */
import React from 'react';
import ReactDOM from 'react-dom';

export function ensureReactGlobal(): void {
  if (typeof window === 'undefined') return;
  const w = window as unknown as { React?: typeof React; ReactDOM?: typeof ReactDOM };
  if (!w.React) w.React = React;
  if (!w.ReactDOM) w.ReactDOM = ReactDOM;
}
