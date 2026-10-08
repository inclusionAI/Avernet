import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { useEffect, useRef, useState } from 'react';

const DEFAULT_RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000] as const;

export interface GroupChatAutoReconnectOptions {
  /** Each value is the delay before the corresponding reconnect attempt. */
  delays?: readonly number[];
}

/**
 * 在群聊已经建立连接后，处理意外断开后的前端重连。
 *
 * 首次连接失败不在这里接管，仍由初始化流程负责；只有连接曾经成功、随后进入
 * disconnected/error，才按有限次指数退避调用 Provider.reconnect。这样连接中和重连
 * 过程中不会展示手动按钮，全部尝试失败后则保留 Provider 的 error/disconnected 状态。
 */
export function useGroupChatAutoReconnect(
  provider: GroupChatProvider | null,
  connectionStatus: ProviderConnectionStatus,
  options: GroupChatAutoReconnectOptions = {},
): boolean {
  const delays = options.delays ?? DEFAULT_RECONNECT_DELAYS;
  const hasConnectedRef = useRef(false);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectingRef = useRef(false);
  const [isAutoReconnecting, setIsAutoReconnecting] = useState(false);

  useEffect(() => {
    hasConnectedRef.current = false;
    attemptRef.current = 0;
    reconnectingRef.current = false;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    setIsAutoReconnecting(false);
  }, [provider]);

  useEffect(() => {
    if (!provider) return;

    const schedule = () => {
      if (
        !hasConnectedRef.current ||
        reconnectingRef.current ||
        timerRef.current ||
        attemptRef.current >= delays.length
      ) {
        if (attemptRef.current >= delays.length) setIsAutoReconnecting(false);
        return;
      }
      setIsAutoReconnecting(true);
      const delay = delays[attemptRef.current];
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        if (!hasConnectedRef.current || reconnectingRef.current) return;
        reconnectingRef.current = true;
        attemptRef.current += 1;
        void provider
          .reconnect()
          .catch(() => {
            reconnectingRef.current = false;
            schedule();
          })
          .finally(() => {
            reconnectingRef.current = false;
          });
      }, delay);
    };

    if (connectionStatus === 'connected') {
      hasConnectedRef.current = true;
      attemptRef.current = 0;
      setIsAutoReconnecting(false);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      return;
    }

    if (hasConnectedRef.current && (connectionStatus === 'disconnected' || connectionStatus === 'error')) schedule();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
    };
  }, [connectionStatus, delays, provider]);

  return isAutoReconnecting;
}

export { DEFAULT_RECONNECT_DELAYS };
