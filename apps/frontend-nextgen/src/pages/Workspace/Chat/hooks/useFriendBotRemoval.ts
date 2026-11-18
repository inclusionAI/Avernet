// 移除好友(用户身份视角)装配 Hook:二次确认 → DELETE friend-connection → 清理侧栏缓存并刷新目录。
// AC-04 历史单聊会话隐藏:好友关系解除后该 Bot 从好友目录消失,其会话缓存一并丢弃;
// 会话与消息数据保留在后端,重新添加好友后目录/会话重新拉取即恢复显示(AC-08)。
// AC-05 选中态清除:仅当移除对象是当前选中的好友 Bot 时清空主舞台选中。
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

export interface UseFriendBotRemovalResult {
  /** 待确认移除的好友 Bot;非空即弹窗打开。 */
  target: ChatBotView | null;
  removing: boolean;
  requestRemoval(bot: ChatBotView): void;
  cancelRemoval(): void;
  confirmRemoval(): Promise<void>;
}

export function useFriendBotRemoval(options: {
  /** 仅从好友目录移除该项:不重拉整目录,避免影响管理/协作 Bot 的选中会话。 */
  removeFriendBot(botId: string): void;
  /** 清空主舞台选中(移除的正是当前选中好友时调用)。 */
  clearSelection(): void;
}): UseFriendBotRemovalResult {
  const { removeFriendBot, clearSelection } = options;
  const [target, setTarget] = useState<ChatBotView | null>(null);
  const [removing, setRemoving] = useState(false);

  const requestRemoval = useCallback((bot: ChatBotView) => {
    setTarget(bot);
  }, []);

  const cancelRemoval = useCallback(() => {
    setTarget(null);
  }, []);

  const confirmRemoval = useCallback(async () => {
    if (!target || removing) return;
    const bot = target;
    setRemoving(true);
    const result = await collaborationCandidateService.removeFriend(bot.botId);
    setRemoving(false);
    if (!result.ok) {
      // 失败保持弹窗打开,错误提示由页面 toast 呈现,不暴露后端细节。
      toast.error(result.error.friendlyMessage);
      return;
    }
    const store = useConversationStore.getState();
    if (store.selectedSection === 'friend' && store.selectedBotId === bot.botId) {
      clearSelection();
    }
    store.forgetFriendBot(bot.botId);
    setTarget(null);
    removeFriendBot(bot.botId);
    toast.success('已移除好友');
  }, [target, removing, clearSelection, removeFriendBot]);

  return { target, removing, requestRemoval, cancelRemoval, confirmRemoval };
}