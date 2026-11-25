import { releaseTaskPanelNavigationForOtherSession } from '@/services/tasks/taskConversationNavigation';
import type { PanelHandle } from '@tc-chat/core';
import { useEffect, useRef } from 'react';

/**
 * 协作群副屏只跟随 sessionId 生命周期清理。
 *
 * 会话详情是异步补齐的：participants 从空到完整会重建 provider，但 sessionId 未变。
 * 清理必须与 single-bot 的 useBotChat 一样只看会话边界，不能等 provider 就位；
 * 否则面板可能在目标会话的 by_session 恢复后才被迟到的 closePanelForce 关闭。
 */
export function useGroupChatPanelCleanup(
  sessionId: string | null,
  panelRef: React.RefObject<PanelHandle | null>,
): void {
  const lastSessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      // 群列表重拉会短暂清空选中会话；重入同一 session 也必须先清理旧焦点，
      // 否则 bootstrap 会将残留 activeTask 误判为已展示，无从恢复面板。
      lastSessionIdRef.current = null;
      return;
    }
    if (lastSessionIdRef.current === sessionId) return;
    lastSessionIdRef.current = sessionId;
    releaseTaskPanelNavigationForOtherSession(sessionId);
    panelRef.current?.closePanelForce();
  }, [panelRef, sessionId]);
}
