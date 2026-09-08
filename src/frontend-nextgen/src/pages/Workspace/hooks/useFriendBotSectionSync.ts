import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect } from 'react';

/**
 * 好友 bot 列表就绪后，纠正已展开好友 bot 的分区归属为 'friend'。
 * 冷启动深链（?bot=好友bot）在展开时无法判定归属，store 缺省记 'mine'，
 * 会让侧栏好友分区判定为折叠（expanded 需 sectionKey==='friend'）而看不到会话列表。
 * 仅纠正「已展开且归属不是 friend」的项；未展开的好友 bot 与 mine 分区 bot 不受影响。
 */
export function useFriendBotSectionSync(friendBots: ChatBotView[]): void {
  useEffect(() => {
    if (friendBots.length === 0) return;
    const store = useWorkspaceStore.getState();
    for (const bot of friendBots) {
      if (store.expandedBotIds[bot.botId] && store.expandedBotSectionKey[bot.botId] !== 'friend') {
        store.setBotExpandedSection(bot.botId, 'friend');
      }
    }
  }, [friendBots]);
}
