/**
 * Bootstrap 主入口
 * 负责应用启动流程的编排
 *
 * 薄壳：完整 SSO / IAM / 访问控制 / Bot 编排经 @ext 注入
 * （开源默认 = 直接渲染主应用；内部 = src/internal/bootstrap.ts）。
 */
import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';
import { isBcnDomain } from '@/utils/platform';
import React from 'react';
import ErrorPrompt from './ErrorPrompt';
import LoginPrompt from './LoginPrompt';
import { mountComponent } from './startup/utils';

// ======================== 导出函数 ========================

export function render(oldRender: () => void) {
  // BCN 域名：在 Umi 路由初始化前直接修正 URL
  // 避免 / → /home → /bcn/chat/list 的双重跳转
  if (isBcnDomain() && !window.location.pathname.startsWith('/bcn')) {
    window.history.replaceState(null, '', '/bcn/chat/list');
  }

  // 通用提示 UI（开源 / 内部都需要：后端可能返回登录态/错误）
  mountComponent(React.createElement(LoginPrompt));
  mountComponent(React.createElement(ErrorPrompt));

  // 启动编排：开源默认直接 oldRender()；内部 extend 为完整 SSO + Bot 流程
  getExt(AppExt).bootstrap.init({ oldRender });
}

export async function getInitialState(): Promise<any> {
  // 初始身份：开源默认返回空壳；内部 extend 为 fetchIamToken + 读 __TERN__.user + 缓存兜底
  return getExt(AppExt).bootstrap.fetchInitialAuth();
}
