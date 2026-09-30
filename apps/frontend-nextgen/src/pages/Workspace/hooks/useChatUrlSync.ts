import { serializeWorkspaceRoute } from '@/domain/workspaceRoute';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useEffect } from 'react';

/** 单聊 Store → URL 投影；current= 当前身份，bot= 目标 Bot，session= 可选会话。 */
export function useChatUrlSync(
  enabled: boolean,
  currentIdentityId: string | null,
  botId: string | undefined,
  sessionId: string | undefined,
) {
  useEffect(() => {
    if (!enabled) return;
    // 身份切换后会话缓存会短暂清空；Store 仍有 selectedBotSessionId 时等待缓存回填，避免丢 session。
    if (!sessionId && useWorkspaceStore.getState().selectedBotSessionId) return;
    const next = serializeWorkspaceRoute({
      view: 'chat',
      currentIdentityId,
      targetBotId: botId,
      sessionId,
    });
    const current = window.location.search.replace(/^\?/, '');
    if (next !== current) {
      history.replace(`${window.location.pathname}?${next}${window.location.hash}`);
    }
  }, [botId, currentIdentityId, enabled, sessionId]);
}
