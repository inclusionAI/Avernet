/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * ServiceConnectionStore - 服务 Bot 专用连接状态管理
 *
 * 与 connectionStore 完全独立，避免服务 Bot 和个人 Bot 之间的连接冲突
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

/** API 层引擎名称（与 connectionStore 保持一致） */
export type ApiEngine =
  | 'moltis'
  | 'openclaw'
  | 'hermes'
  | 'aicoding'
  | 'claude_code';

export interface ServiceConnectionState {
  // 连接信息
  proxyTarget: string;
  proxyToken: string;
  connectionType: 'local' | 'remote';
  tokenRefreshFailed: boolean;
  /** 当前活跃引擎（用于 WebSocket 路径拼接和 UI 区分） */
  activeEngine: ApiEngine;

  // Actions
  setConnection: (
    target: string,
    token: string,
    type?: 'local' | 'remote',
    engine?: ApiEngine,
  ) => void;
  setActiveEngine: (engine: ApiEngine) => void;
  reset: () => void;
  setTokenRefreshFailed: (failed: boolean) => void;

  // Getters
  getProxyTarget: () => string;
  getProxyToken: () => string;
  isConnected: () => boolean;
}

const initialState = {
  proxyTarget: '',
  proxyToken: '',
  connectionType: 'remote' as const,
  tokenRefreshFailed: false,
  activeEngine: 'openclaw' as ApiEngine,
};

export const useServiceConnectionStore = create<ServiceConnectionState>()(
  devtools(
    (set, get) => ({
      // Initial state
      ...initialState,

      // Actions
      setConnection: (target, token, type = 'remote', engine) =>
        set(
          {
            proxyTarget: target,
            proxyToken: token,
            connectionType: type,
            tokenRefreshFailed: false,
            ...(engine ? { activeEngine: engine } : {}),
          },
          false,
          'setConnection',
        ),

      setActiveEngine: (engine) =>
        set({ activeEngine: engine }, false, 'setActiveEngine'),

      reset: () => set({ ...initialState }, false, 'reset'),

      setTokenRefreshFailed: (failed) =>
        set({ tokenRefreshFailed: failed }, false, 'setTokenRefreshFailed'),

      // Getters
      getProxyTarget: () => get().proxyTarget,
      getProxyToken: () => get().proxyToken,
      isConnected: () => !!get().proxyTarget && !!get().proxyToken,
    }),
    { name: 'ServiceConnectionStore' },
  ),
);

// 便捷导出
export const getServiceProxyTarget = () =>
  useServiceConnectionStore.getState().proxyTarget;
export const getServiceProxyToken = () =>
  useServiceConnectionStore.getState().proxyToken;
