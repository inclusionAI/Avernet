import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { PanelHandle } from '@tc-chat/core';
import { useEffect, useRef } from 'react';

/**
 * 协作群副屏只跟随 sessionId 生命周期清理。
 *
 * 会话详情是异步补齐的：participants 从空到完整会重建 provider，但 sessionId 未变。
 * 若把 provider 变化也当成清理边界，详情回填会把 by_session 刚恢复的 TaskPanel 关闭。
 */
export function useGroupChatPanelCleanup(
  provider: GroupChatProvider | null,
  sessionId: string | null,
  panelRef: React.RefObject<PanelHandle | null>,
): void {
  const lastSessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!provider || !sessionId) return;
    if (lastSessionIdRef.current === sessionId) return;
    lastSessionIdRef.current = sessionId;
    panelRef.current?.closePanelForce();
  }, [panelRef, provider, sessionId]);
}
