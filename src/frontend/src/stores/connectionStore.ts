/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Connection Store - 公共连接信息（bot/brain 共用）
 *
 * bot 和 brain 初始化完成后都往此 store 写入连接信息，
 * 下游消费方（ChatPage、CardPreview 等）统一从此处读取。
 *
 * ─── Token 生命周期 ───────────────────────────────────────────
 *
 * proxyToken 每 2 分钟过期。`useConnectionRefresh`（src/hooks/useConnectionRefresh.ts）
 * 挂载于 MainLayout，每 100 秒自动调用 getBotConnection(binding_id) 刷新 token，
 * 并通过 setConnection 写回此 store。
 *
 * 刷新仅在 remote 模式下生效（本地/Electron 模式 token 不过期）。
 * 累计失败 10 次后停止轮询，设 connectionRefreshStopped = true，MainLayout 弹框提示用户刷新。
 *
 * ─── Bot 删除时的连接清理 ────────────────────────────────────
 *
 * 删除当前活跃 Bot 时，必须调用 reset() 清空此 store，否则页面会继续以
 * "已连接"状态运行，但 Bot 已不存在，所有代理请求均会失败。
 *
 * 此逻辑由 useBot.deleteBot（src/hooks/useBot.ts）负责：
 *   - 删除非活跃 Bot → 仅 removeBot，不影响连接
 *   - 删除活跃 Bot   → removeBot + connectionStore.reset() + 1.5s 后 reload()
 *                       Bootstrap 重新初始化，找其他可用 Bot 或创建默认 Bot
 */

import { ENGINE_TYPE } from '@/services/backend-api/BotController';
import { getProxypassAbsoluteUrl, getServers } from '@/utils/env';
import { getElectronEnv, isElectron } from '@/utils/platform';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

/** API 层引擎名称 */
export type ApiEngine =
  | 'moltis'
  | 'openclaw'
  | 'hermes'
  | 'aicoding'
  | 'claude_code'
  | 'teclaw';

interface ConnectionState {
  /** 代理目标地址 */
  proxyTarget: string;
  /** 代理 token */
  proxyToken: string;
  /** 连接类型 */
  connectionType: 'local' | 'remote' | 'desktop';
  /** 当前活跃引擎（用于 WebSocket 路径拼接） */
  activeEngine: ApiEngine;
  /**
   * token 刷新是否失败
   * true = useConnectionRefresh 连续失败达到上限，store 里的 token 可能已过期
   * false = 最近一次刷新成功，token 是新鲜的
   */
  tokenRefreshFailed: boolean;

  /**
   * token 刷新是否已永久停止
   * true = useConnectionRefresh 累计失败超过上限，已停止轮询，需要用户手动刷新页面
   */
  connectionRefreshStopped: boolean;

  /** Actions */
  setConnection: (
    target: string,
    token: string,
    type?: 'local' | 'remote' | 'desktop',
    engine?: ApiEngine,
  ) => void;
  setActiveEngine: (engine: ApiEngine) => void;
  setTokenRefreshFailed: (failed: boolean) => void;
  setConnectionRefreshStopped: (stopped: boolean) => void;
  /** 网络恢复信号：每次恢复时 +1，组件监听此值并清除 session 失败标记 */
  reconnectTrigger: number;
  triggerReconnect: () => void;
  reset: () => void;
}

export const useConnectionStore = create<ConnectionState>()(
  devtools(
    (set) => ({
      proxyTarget: '',
      proxyToken: '',
      connectionType: 'remote',
      activeEngine: ENGINE_TYPE.OPENCLAW,
      tokenRefreshFailed: false,
      connectionRefreshStopped: false,
      reconnectTrigger: 0,

      setConnection: (target, token, type, engine) =>
        set(
          {
            proxyTarget: target,
            proxyToken: token,
            connectionType: type || 'remote',
            ...(engine ? { activeEngine: engine } : {}),
          },
          false,
          'setConnection',
        ),

      setActiveEngine: (engine) =>
        set({ activeEngine: engine }, false, 'setActiveEngine'),

      setTokenRefreshFailed: (failed) =>
        set({ tokenRefreshFailed: failed }, false, 'setTokenRefreshFailed'),

      triggerReconnect: () =>
        set(
          (s) => ({ reconnectTrigger: s.reconnectTrigger + 1 }),
          false,
          'triggerReconnect',
        ),

      setConnectionRefreshStopped: (stopped) =>
        set(
          { connectionRefreshStopped: stopped },
          false,
          'setConnectionRefreshStopped',
        ),

      reset: () =>
        set(
          {
            proxyTarget: '',
            proxyToken: '',
            connectionType: 'remote',
            activeEngine: ENGINE_TYPE.OPENCLAW,
            tokenRefreshFailed: false,
            connectionRefreshStopped: false,
            reconnectTrigger: 0,
          },
          false,
          'reset',
        ),
    }),
    { name: 'ConnectionStore' },
  ),
);

const g = () => useConnectionStore.getState();

// ─── Selectors ──────────────────────────────────────────────

/**
 * 获取 WebSocket 绝对 URL（SDK 用）
 *
 * 路径按引擎区分：
 * - aicoding：``/api/ws``（引擎无关的统一入口，后续多引擎合并的目标形态）
 * - moltis / openclaw：``/api/${engine}/ws``（遗留 per-engine 路由）
 */
export function selectWebsocketUrl(
  state: Pick<
    ConnectionState,
    'proxyTarget' | 'activeEngine' | 'connectionType'
  >,
): string {
  const engine = state.activeEngine || ENGINE_TYPE.OPENCLAW;
  const wsPath = engine === 'aicoding' ? '/api/ws' : `/api/${engine}/ws`;

  if (isElectron()) {
    const electronEnv = getElectronEnv();
    const apiHost = electronEnv?.apiHost || 'localhost:20003';
    return `ws://${apiHost}${wsPath}`;
  }
  if (state.proxyTarget) {
    if (
      state.connectionType === 'local' ||
      state.connectionType === 'desktop'
    ) {
      return `ws://${state.proxyTarget}${wsPath}`;
    }
    const relativePath = `/proxypass/${state.proxyTarget}${wsPath}`;
    // getProxypassAbsoluteUrl 返回 https:// 协议，WebSocket 需要 wss://
    return getProxypassAbsoluteUrl(relativePath)
      .replace(/^https:/, 'wss:')
      .replace(/^http:/, 'ws:');
  }
  return `ws://localhost:20003${wsPath}`;
}

/**
 * 群聊 WS 的 BCS 基址（仅 dev 注入，见 config/config.local.ts 的 define）。
 * 值为含 BCS_PORT 的 BCS 地址（如 http://127.0.0.1:31000）。为真时聊天 WS 直连
 * ws://127.0.0.1:<BCS_PORT>/ws，跟随自定义端口，而非 bundle 里的静态 BCN 端口（默认 21000）。
 * 生产构建未注入该常量，typeof 守卫使本段被 tree-shake，走 getServers().BCN。
 */
declare const DEV_BCN_BASE: string | undefined;

/**
 * 获取 BCN WebSocket URL（群聊用）
 * 根据 getServers() 配置动态构造 WebSocket 地址
 */
export function selectBcnWebsocketUrl(): string {
  // dev：直连含 BCS_PORT 的本地 BCS（WS 不受 CORS 限制，可跨端口直连）。
  if (typeof DEV_BCN_BASE !== 'undefined' && DEV_BCN_BASE) {
    return `${DEV_BCN_BASE.replace(/^http/, 'ws')}/ws`;
  }

  const servers = getServers();
  const bcnServer = servers.BCN;

  if (!bcnServer) {
    // 降级：BCN 未配置（真实环境 SERVERS.BCN 恒有值，此分支几乎不触发）
    console.warn('[connectionStore] BCN server not configured, using default');
    return 'ws://localhost:21000/ws';
  }

  // 将 http/https 转换为 ws/wss
  const wsUrl = bcnServer.replace(/^http/, 'ws');
  return `${wsUrl}/ws`;
}

// ─── 非 React 代码用的便捷方法 ──────────────────────────────

export const setConnection = (
  target: string,
  token: string,
  type?: 'local' | 'remote',
  engine?: ApiEngine,
) => g().setConnection(target, token, type, engine);
export const getProxyTarget = () => g().proxyTarget;
export const getProxyToken = () => g().proxyToken;
export const getConnectionType = () => g().connectionType;
export const isLocalMode = () =>
  g().connectionType === 'local' || g().connectionType === 'desktop';
export const getActiveEngine = () => g().activeEngine;
export const buildWebsocketUrl = () => selectWebsocketUrl(g());
export const buildBcnWebsocketUrl = () => selectBcnWebsocketUrl();
