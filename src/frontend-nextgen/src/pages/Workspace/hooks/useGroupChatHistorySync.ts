import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ChatMessage } from '@tc-chat/core';
import { useState } from 'react';
import { useManifestHistoryLoader } from './useManifestHistoryLoader';
import type { SessionEnterOutcome } from './useSessionDisplayStatus';

/**
 * 群聊历史装载同步（Spec: docs/specs/workspace-session-connection-display.md）——从 useGroupChat
 * 拆出以控制文件体积（对齐 useProviderStateSubscriptions 先例），行为不变：持有向上翻页可见性
 * 状态并驱动 useManifestHistoryLoader 的连接 / manifest / 历史 hydration 串行流程。
 */
export function useGroupChatHistorySync({
  provider,
  sessionId,
  historyRefreshNonce,
  setMessages,
  onEnterOutcome,
}: {
  provider: GroupChatProvider | null;
  sessionId: string | null;
  historyRefreshNonce: number;
  setMessages: (messages: ChatMessage[]) => void;
  onEnterOutcome?: (outcome: SessionEnterOutcome, historyEmpty?: boolean) => void;
}) {
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingMoreHistory, setIsLoadingMoreHistory] = useState(false);

  useManifestHistoryLoader({
    provider,
    sessionId,
    historyRefreshNonce,
    setHasMoreHistory,
    setIsLoadingMoreHistory,
    setMessages,
    onEnterOutcome,
  });

  // setter 供 useGroupChat 的 loadMoreHistory / reloadHistory 更新分页可见性。
  return { hasMoreHistory, isLoadingMoreHistory, setHasMoreHistory, setIsLoadingMoreHistory };
}
