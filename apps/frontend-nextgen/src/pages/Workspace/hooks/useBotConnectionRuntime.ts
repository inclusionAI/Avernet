import type { BotChatProvider, BotConnectionRuntime } from '@/services/workspace/botChatProvider';
import { useCallback, useSyncExternalStore } from 'react';

const UNRESOLVED_CONNECTION_RUNTIME: BotConnectionRuntime = { resolved: false, engine: null };
const noopUnsubscribe = () => undefined;

/** 将 provider 的 /connection 运行时快照安全接入 React，并在切换会话时同步复位。 */
export function useBotConnectionRuntime(provider: BotChatProvider | null): BotConnectionRuntime {
  const subscribe = useCallback(
    (listener: () => void) => provider?.subscribeToConnectionRuntime(listener) ?? noopUnsubscribe,
    [provider],
  );
  const getSnapshot = useCallback(
    () => provider?.connectionRuntime ?? UNRESOLVED_CONNECTION_RUNTIME,
    [provider],
  );
  return useSyncExternalStore(subscribe, getSnapshot, () => UNRESOLVED_CONNECTION_RUNTIME);
}
