import type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationSessionScope,
} from '@/domain/conversation';
import { useConversationStore } from '@/stores/conversationStore';

export interface ConversationBotSelectionTarget {
  botId: string;
  section: ConversationBotSection;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
  /** Coding Bot 的 others 复用扁平会话列表，不走好友分组目录。 */
  flatCodingOthers?: boolean;
}

/** 仅返回目标当前来源/范围的列表，不能把上一范围缓存当成已加载。 */
function getList(target: ConversationBotSelectionTarget) {
  if (target.origin !== 'mine' && !target.flatCodingOthers) return undefined;
  const state = useConversationStore.getState();
  if (target.section === 'friend') return state.friendBotSessionsByBotId[target.botId];
  const cache = state.sessionsByBotId[target.botId];
  return cache?.origin === target.origin && cache.scope === target.scope ? cache.sessions : undefined;
}

// 2026-11-02 反馈治理:移除展开即自动首选(begin/complete)——展开 Bot 行只控制列表显隐,
// 主舞台选中仅由用户点具体会话/深链/新建会话驱动,正在阅读的会话不再被展开动作切走。
export const conversationBotSelectionService = {
  getList,

  showAll(botId: string, section: ConversationBotSection): void {
    // 「查看全部会话」按分区路由:好友 Bot 写好友范围(不投影主选中/URL);管理/团队沿用管理范围。
    const store = useConversationStore.getState();
    if (section === 'friend') store.setFriendBotScope(botId, 'all');
    else store.setManagedBotScope(botId, 'all');
  },
};
