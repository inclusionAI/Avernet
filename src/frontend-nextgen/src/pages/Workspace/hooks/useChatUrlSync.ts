import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useEffect } from 'react';

/** 单聊会话选中 → 写回 URL（?tab=chat&bot=<botId>&session=<sessionId>），便于分享/刷新回填。 */
export function useChatUrlSync(isChatView: boolean, botId: string | undefined, sessionId: string | undefined) {
  useEffect(() => {
    if (!isChatView) return;
    // 身份切换后 useBotSessionMap 会清缓存，selectedSession 短暂为 null，
    // 但 store 仍持有 selectedBotSessionId。此时不覆盖 URL（由 flush effect 写入），
    // 等缓存回填后再同步。
    if (!sessionId && useWorkspaceStore.getState().selectedBotSessionId) return;
    const params = new URLSearchParams();
    params.set('tab', 'chat');
    if (sessionId && botId) {
      params.set('bot', botId);
      params.set('session', sessionId);
    }
    const next = params.toString();
    const current = window.location.search.replace(/^\?/, '');
    if (next !== current) {
      history.replace(`${window.location.pathname}${next ? `?${next}` : ''}${window.location.hash}`);
    }
  }, [isChatView, botId, sessionId]);
}
