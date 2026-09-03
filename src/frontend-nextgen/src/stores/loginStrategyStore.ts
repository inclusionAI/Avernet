import type { LoginStrategy } from '@/capabilities/types';
import { create } from 'zustand';

/**
 * 登录策略运行态（纯状态）。`app.tsx` 在 `sealExtensions()` 后按 `getCapabilities().getLoginStrategy()`
 * 把 capability 解析结果写入本 store，供 `httpClient` / 副屏等 Service 层读取（Service 不直接 import
 * capability 注册表，避免 boot 顺序 / 测试 mock 负担）。
 *
 * 默认 `'ace-gateway'`（保守）：未装配时维持既有 ACE 硬跳转现状，现有 httpClient/observer 测试不受影响。
 * Open Core 构建 capability 返回 `'oauth-provider'` → 启动期写入后切为外部登录。
 */
interface LoginStrategyState {
  loginStrategy: LoginStrategy;
  setLoginStrategy: (strategy: LoginStrategy) => void;
}

export const useLoginStrategyStore = create<LoginStrategyState>((set) => ({
  loginStrategy: 'ace-gateway',
  setLoginStrategy: (loginStrategy) => set({ loginStrategy }),
}));
