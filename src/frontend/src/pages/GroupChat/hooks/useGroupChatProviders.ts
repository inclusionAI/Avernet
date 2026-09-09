/**
 * useGroupChatProviders - 管理群聊的 Provider
 *
 * 按需建立 WebSocket 连接：切换到某个会话时才建立连接
 * 单个群聊的消息管理使用 useGroupChat hook
 * 仅支持会话维度：groupId:sessionId 作为缓存 key
 */

import { selectBcnWebsocketUrl } from '@/stores/connectionStore';
import { useUserStore } from '@/stores/userStore';
import { useCallback, useEffect, useState } from 'react';

// eslint-disable-next-line @typescript-eslint/ban-ts-comment
import { GroupChatProvider } from '@aix-chat/adapters';

export interface ProviderInfo {
  provider: GroupChatProvider;
  groupId: string;
  sessionId: string;
  viewActorId: string;
  isConnected: boolean;
  /** 标记为已销毁，防止断开后重连 */
  isDestroyed: boolean;
}

// 全局 provider 缓存 (key: groupId:sessionId:viewActorId)
const providerCache = new Map<string, ProviderInfo>();
// 正在连接中的 key 集合，防止重复连接
const pendingConnections = new Set<string>();
const listeners = new Set<() => void>();
const scopeReconnectTokens = new Map<string, symbol>();
const scopeReconnectDelaysMs = [100, 250, 500, 1000, 2000, 3000];

function notifyListeners() {
  listeners.forEach((listener) => listener());
}

/**
 * 生成缓存 key
 * @param groupId 群组 ID
 * @param sessionId 会话 ID
 * @returns 缓存 key
 */
function getCacheKey(
  groupId: string,
  sessionId: string,
  viewActorId: string,
): string {
  return `${groupId}:${sessionId}:${viewActorId}`;
}

/**
 * 获取 Provider
 * @param groupId 群组 ID
 * @param sessionId 会话 ID
 */
export function getProvider(
  groupId: string,
  sessionId: string,
  viewActorId: string,
): ProviderInfo | undefined {
  const key = getCacheKey(groupId, sessionId, viewActorId);
  return providerCache.get(key);
}

export function getAllProviders(): Map<string, ProviderInfo> {
  return new Map(providerCache);
}

export interface UseGroupChatProvidersResult {
  /** 连接指定会话（按需连接） */
  connectGroup: (
    groupId: string,
    sessionId: string,
    viewActorId: string,
  ) => void;
  /** 断开指定会话的连接 */
  disconnectGroup: (
    groupId: string,
    sessionId: string,
    viewActorId: string,
  ) => void;
  getProvider: (
    groupId: string,
    sessionId: string,
    viewActorId: string,
  ) => ProviderInfo | undefined;
  /** 断开所有连接 */
  disconnectAll: () => void;
}

/**
 * useGroupChatProviders Hook
 * 管理群聊的 Provider 连接（按需连接模式）
 */
export function useGroupChatProviders(): UseGroupChatProvidersResult {
  const userId = useUserStore((state) => state.userId);

  // 强制组件更新
  const [, forceUpdate] = useState(0);

  // 订阅 provider 变化
  useEffect(() => {
    const listener = () => forceUpdate((n) => n + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  // 连接指定会话
  const connectGroup = useCallback(
    (groupId: string, sessionId: string, viewActorId: string) => {
      const cacheKey = getCacheKey(groupId, sessionId, viewActorId);
      scopeReconnectTokens.delete(cacheKey);

      let scheduleScopeReconnect: (token: symbol, attempt: number) => void;

      const createProvider = (scopeReconnect?: {
        token: symbol;
        attempt: number;
      }) => {
        if (
          scopeReconnect &&
          scopeReconnectTokens.get(cacheKey) !== scopeReconnect.token
        ) {
          return;
        }

        // 已有连接或正在连接中，跳过
        if (providerCache.has(cacheKey) || pendingConnections.has(cacheKey)) {
          console.log(
            '[useGroupChatProviders] Already connected or connecting:',
            cacheKey,
          );
          return;
        }

        const wsUrl = selectBcnWebsocketUrl();
        if (!wsUrl) {
          console.warn('[useGroupChatProviders] WebSocket URL not available');
          return;
        }

        console.log('[useGroupChatProviders] Connecting to:', cacheKey);

        const config: any = {
          url: wsUrl,
          currentUserId: userId || 'anonymous',
          immediateConnect: true,
          enableThinkingTag: true,
          reconnectAttempts: 0, // 禁用自动重连，展示断连状态让用户手动刷新
          heartbeatInterval: 30000,
          heartbeatTimeout: 90000,
          connectionTimeout: 10000,
          fallbackMessage: '请求失败，请稍后重试',
        };

        try {
          const provider = new GroupChatProvider(config);
          const providerInfo: ProviderInfo = {
            provider,
            groupId,
            sessionId,
            viewActorId,
            isConnected: false,
            isDestroyed: false,
          };

          // Patch: SDK 的 GroupChatProvider 在 BCS 帧中未传递 session_id，
          // 导致以 session 为维度建立 WS 连接后，发送消息仍走 'main' session。
          // 此处拦截 transport.send，在 BCS 帧中注入 session_id。
          const transport = (provider as any).transport;
          if (transport) {
            const originalSend = transport.send.bind(transport);
            transport.send = (frame: any) => {
              if (frame?.params) {
                // 在 connect 帧中注入 session_id
                if (frame.method === 'connect') {
                  frame.params.session_id = sessionId;
                  frame.params.view_actor_id = viewActorId;
                }
                // 在 chat.send 帧中注入 session_id，并将 sessionKey 从硬编码 'main' 替换为 sessionId
                if (frame.method === 'chat.send') {
                  frame.params.session_id = sessionId;
                  frame.params.sessionKey = sessionId;
                }
              }
              return originalSend(frame);
            };

            // Scope 变更帧由服务端紧接着物理关闭旧连接。用有界退避
            // 穿过服务端的 scope-change barrier，再以同一 View Actor 重连；
            // 新 scope 仍只由服务端从 Session Participant 解析。
            const originalOnTransportMessage = transport.onMessage;
            transport.onMessage = (frame: any) => {
              const isScopeChange =
                frame?.type === 'event' &&
                frame?.event === 'close' &&
                frame?.payload?.reason === 'view_scope_changed';
              if (isScopeChange) {
                providerInfo.isConnected = false;
                providerInfo.isDestroyed = true;
                pendingConnections.delete(cacheKey);
                if (providerCache.get(cacheKey) === providerInfo) {
                  providerCache.delete(cacheKey);
                }
                notifyListeners();
                const token = Symbol(cacheKey);
                scopeReconnectTokens.set(cacheKey, token);
                scheduleScopeReconnect(token, 0);
              }
              originalOnTransportMessage?.(frame);
            };
          }

          // 订阅连接状态，同步更新缓存中的 isConnected
          provider.subscribeToConnectionStatus((event) => {
            const cached = providerCache.get(cacheKey);
            if (!cached || cached.isDestroyed) return;
            const wasConnected = cached.isConnected;
            cached.isConnected = event.status === 'connected';
            if (wasConnected !== cached.isConnected) {
              notifyListeners();
            }
          });

          // 标记为正在连接
          pendingConnections.add(cacheKey);
          // 添加到缓存
          providerCache.set(cacheKey, providerInfo);
          notifyListeners();

          // 传入 sessionId 建立会话级连接
          const connectParams: any = { groupId, sessionId, viewActorId };

          provider
            .connect(connectParams)
            .then(() => {
              if (providerInfo.isDestroyed) {
                console.log(
                  '[useGroupChatProviders] Provider destroyed, disconnecting:',
                  cacheKey,
                );
                provider.disconnect();
                return;
              }
              console.log(
                '[useGroupChatProviders] ✅ Connected for:',
                cacheKey,
              );
              providerInfo.isConnected = true;
              pendingConnections.delete(cacheKey);
              if (scopeReconnect) {
                scopeReconnectTokens.delete(cacheKey);
              }
              notifyListeners();
            })
            .catch((err: Error) => {
              if (providerInfo.isDestroyed) {
                console.log(
                  '[useGroupChatProviders] Provider destroyed, ignoring connect error:',
                  cacheKey,
                );
                return;
              }
              console.error(
                '[useGroupChatProviders] ❌ Failed to connect for:',
                cacheKey,
                err.message,
              );
              pendingConnections.delete(cacheKey);
              providerCache.delete(cacheKey);
              notifyListeners();
              if (scopeReconnect) {
                scheduleScopeReconnect(
                  scopeReconnect.token,
                  scopeReconnect.attempt + 1,
                );
              }
            });
        } catch (error) {
          console.error(
            '[useGroupChatProviders] Failed to create provider for:',
            cacheKey,
            error,
          );
          if (scopeReconnect) {
            scheduleScopeReconnect(
              scopeReconnect.token,
              scopeReconnect.attempt + 1,
            );
          }
        }
      };

      scheduleScopeReconnect = (token: symbol, attempt: number) => {
        if (scopeReconnectTokens.get(cacheKey) !== token) return;
        const delay = scopeReconnectDelaysMs[attempt];
        if (delay === undefined) {
          scopeReconnectTokens.delete(cacheKey);
          console.error(
            '[useGroupChatProviders] Scope-change reconnect exhausted:',
            cacheKey,
          );
          return;
        }
        window.setTimeout(() => {
          if (scopeReconnectTokens.get(cacheKey) !== token) return;
          createProvider({ token, attempt });
        }, delay);
      };

      createProvider();
    },
    [userId],
  );

  // 断开指定会话的连接
  const disconnectGroup = useCallback(
    (groupId: string, sessionId: string, viewActorId: string) => {
      const cacheKey = getCacheKey(groupId, sessionId, viewActorId);
      scopeReconnectTokens.delete(cacheKey);
      const info = providerCache.get(cacheKey);
      if (!info) return;

      console.log('[useGroupChatProviders] Disconnecting:', cacheKey);
      info.isDestroyed = true;

      try {
        info.provider.disconnect();
      } catch (err) {
        console.error(
          '[useGroupChatProviders] Error disconnecting provider:',
          cacheKey,
          err,
        );
      }

      providerCache.delete(cacheKey);
      pendingConnections.delete(cacheKey);
      notifyListeners();
    },
    [],
  );

  // 断开所有连接
  const disconnectAll = useCallback(() => {
    // 先标记所有 provider 为已销毁
    providerCache.forEach((info) => {
      info.isDestroyed = true;
    });

    // 然后断开所有连接
    providerCache.forEach((info) => {
      try {
        info.provider.disconnect();
      } catch (err) {
        console.error(
          '[useGroupChatProviders] Error disconnecting provider:',
          `${info.groupId}:${info.sessionId}`,
          err,
        );
      }
    });

    providerCache.clear();
    pendingConnections.clear();
    scopeReconnectTokens.clear();
    notifyListeners();
  }, []);

  return {
    connectGroup,
    disconnectGroup,
    getProvider,
    disconnectAll,
  };
}

// 导出 listeners 供 useGroupChat 订阅
export { listeners };
