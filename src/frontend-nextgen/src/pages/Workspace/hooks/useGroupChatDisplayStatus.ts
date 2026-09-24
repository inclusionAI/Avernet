import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { ChatMessage } from '@tc-chat/core';
import { useCallback, useEffect, useState } from 'react';
import { filterVisibleGroupMessages } from './groupChatHistoryUtils';
import { useGroupChatAutoReconnect } from './useGroupChatAutoReconnect';
import { EMPTY_CONFIRM_MS } from './useMessageAreaSkeleton';
import { useSessionDisplayStatus, type SessionEnterOutcome } from './useSessionDisplayStatus';

export type { SessionEnterOutcome };

/**
 * 群聊显示状态合成（Spec: docs/specs/workspace-session-connection-display.md）——从 useGroupChat
 * 拆出以控制文件体积（对齐 useProviderStateSubscriptions 先例），自治管理进入流程信号与意外
 * 断线自动重连接管（useGroupChatAutoReconnect）。
 *
 * 就绪绑定「渲染层可见消息」实际出现（预览反馈：顶栏「已连接」不得早于历史消息可见）：以
 * filterVisibleGroupMessages 过滤后的消息数为判据——与消息区渲染共用同一过滤，两层必须同源。
 * 「历史为空」（loader 上报 historyEmpty）不再立即信任：loader 返回空可能是装载缝隙（实际历史
 * 稍后经 WS / 重载补齐），须持续 EMPTY_CONFIRM_MS 仍无可见消息才确认真·空——期间历史补到则
 * 就绪与内容同帧出现。判定在渲染层同步派生，对任何层级延迟免疫。
 *
 * onEnterOutcome 供 useGroupChatHistorySync（loader）上报 pending/ready/failed + 空历史标志。
 */
export function useGroupChatDisplayStatus({
  sessionId,
  provider,
  rawStatus,
  messages,
}: {
  sessionId: string | null;
  provider: GroupChatProvider | null;
  rawStatus: ProviderConnectionStatus;
  messages: ChatMessage[];
}) {
  const [loaderOutcome, setLoaderOutcome] = useState<SessionEnterOutcome>('pending');
  const [historyEmpty, setHistoryEmpty] = useState(false);

  const onEnterOutcome = useCallback((outcome: SessionEnterOutcome, empty?: boolean) => {
    setLoaderOutcome(outcome);
    setHistoryEmpty(outcome === 'ready' ? !!empty : false);
  }, []);

  const isAutoReconnecting = useGroupChatAutoReconnect(provider, rawStatus);
  const visibleCount = filterVisibleGroupMessages(messages).length;
  // 真空确认：loader 上报空历史后持续宽限仍无可见消息，才信任为真·空会话。
  const [emptyConfirmed, setEmptyConfirmed] = useState(false);
  const shouldConfirmEmpty = loaderOutcome === 'ready' && historyEmpty && visibleCount === 0;

  useEffect(() => {
    if (!shouldConfirmEmpty) {
      if (emptyConfirmed) setEmptyConfirmed(false);
      return;
    }
    const timer = setTimeout(() => setEmptyConfirmed(true), EMPTY_CONFIRM_MS);
    return () => clearTimeout(timer);
  }, [shouldConfirmEmpty, emptyConfirmed]);

  const enterOutcome: SessionEnterOutcome =
    loaderOutcome === 'failed'
      ? 'failed'
      : loaderOutcome === 'ready' && (visibleCount > 0 || (historyEmpty && emptyConfirmed))
      ? 'ready'
      : 'pending';
  const { status } = useSessionDisplayStatus({
    sessionKey: sessionId,
    rawStatus,
    enterOutcome,
    autoReconnecting: isAutoReconnecting,
  });
  return { status, onEnterOutcome };
}
