import type { GroupChatState } from '@/services/workspace/groupChatProvider';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { ChatMessage } from '@tc-chat/core';
import { useEffect } from 'react';

interface GroupBootstrapProcessingOptions {
  groupId: string | null;
  sessionId: string | null;
  messages: ChatMessage[];
  supportPhase: GroupChatState['phase'];
}

/** 建群启动提示在首条 Driver/Manager 消息或初始化错误后让位。 */
export function useGroupBootstrapProcessing({
  groupId,
  sessionId,
  messages,
  supportPhase,
}: GroupBootstrapProcessingOptions): boolean {
  const pending = useWorkspaceStore((state) => state.pendingGroupBootstrap);
  const clearPending = useWorkspaceStore((state) => state.clearPendingGroupBootstrap);
  const processing = Boolean(
    pending && pending.groupId === groupId && pending.sessionId === sessionId && pending.run.state === 'running',
  );

  useEffect(() => {
    if (!processing || !pending) return;
    const runId = pending.run.runId;
    const hasBootstrapMessage = messages.some((message) => {
      const extra = message.extra as Record<string, unknown> | undefined;
      return (
        extra?.runId === runId ||
        extra?.run_id === runId ||
        extra?.conversationRoundId === runId ||
        message.id === `bcs-run:${runId}:${pending.run.botUuid}`
      );
    });
    if (hasBootstrapMessage || supportPhase === 'error') clearPending(runId);
  }, [clearPending, messages, pending, processing, supportPhase]);

  return processing;
}
