import type { GroupChatProvider, GroupChatState } from '@/services/workspace/groupChatProvider';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { useEffect } from 'react';

/**
 * 订阅 Provider 阶段状态 + WebSocket 连接状态（从 useGroupChat 拆出以控制文件体积，行为不变）。
 */
export function useProviderStateSubscriptions(
  provider: GroupChatProvider | null,
  setSupportState: (state: GroupChatState) => void,
  setConnectionStatus: (status: ProviderConnectionStatus) => void,
): void {
  useEffect(() => {
    if (!provider) return;
    const offState = provider.subscribeToSupportState(setSupportState);
    const offStatus = provider.subscribeToConnectionStatus((event: { status: ProviderConnectionStatus }) =>
      setConnectionStatus(event.status),
    );
    return () => {
      offState();
      offStatus();
    };
  }, [provider, setSupportState, setConnectionStatus]);
}
