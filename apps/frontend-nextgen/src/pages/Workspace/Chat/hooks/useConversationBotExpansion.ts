import type { ConversationBotView } from '@/domain/conversation';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback } from 'react';

/** 展开 Bot 行只控制会话列表显隐，不抢占主舞台选中（2026-11-02 反馈：展开/切台解耦，
 *  正在阅读的会话不再被展开动作切走；主舞台仅在用户点具体会话时切换）。
 *  非 chatable / 用户未加载时维持原有不可展开门控，与 ConversationBotItem 行为一致。 */
export function useConversationBotExpansion(userId: string | null) {
  return useCallback(
    (view: ConversationBotView) => {
      if (!userId || !view.bot.chatable) return;
      useConversationStore.getState().setExpandedBot(view.bot.botId, true);
    },
    [userId],
  );
}
