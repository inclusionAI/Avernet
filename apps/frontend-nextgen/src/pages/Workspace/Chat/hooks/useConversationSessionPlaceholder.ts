import type { ConversationBotView } from '@/domain/conversation';
import { conversationBotSelectionService } from '@/services/workspace/conversationBotSelectionService';
import { useConversationStore } from '@/stores/conversationStore';
import { selectedSnapshotMatches } from '@/stores/conversationStoreState';
import { useRef, useState } from 'react';
import { toast } from 'sonner';
import type { ConversationSessionsModel } from './useConversationSessions.types';

export interface ConversationSessionPlaceholderModel {
  botName: string;
  status: 'loading' | 'error' | 'empty' | 'favorite-empty' | 'unselected';
  error?: string;
  creating: boolean;
  create(): Promise<void>;
  retry(): void;
  showAll(): void;
}

/** 区分未选 Bot、目标列表加载、失败与确认空列表，禁止显示上一 Bot 的聊天内容。 */
export function useConversationSessionPlaceholder(options: {
  userId: string | null;
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  sessions: ConversationSessionsModel;
}): ConversationSessionPlaceholderModel | null {
  const state = useConversationStore();
  const [creating, setCreating] = useState(false);
  const inFlight = useRef(false);
  const view = [...options.managedBots, ...options.friendBots].find(
    (item) => item.bot.botId === state.selectedBotId && item.section === state.selectedSection,
  );
  if (!view || state.selectedOrigin !== 'mine') return null;
  // 已打开的主会话被过滤条件移出列表(spec: 搜索只改变侧栏列表,不自动关闭主会话),
  // 占位一律不遮蔽:加载/空态/失败/未命中都不会切换舞台,仍由保留快照驱动。
  if (
    selectedSnapshotMatches(state.selectedSessionSnapshot, {
      botId: state.selectedBotId,
      section: state.selectedSection,
      origin: state.selectedOrigin,
      scope: state.selectedScope,
      friendUserId: state.selectedFriendUserId,
      sessionId: state.selectedSessionId,
    })
  )
    return null;
  // 好友 Bot 的生效范围读 scopeByFriendBotId(选中范围恒 all,不随好友筛选投影);
  // 管理/团队 Bot 沿用已同步的 selectedScope。
  const effectiveScope =
    view.section === 'friend' ? state.scopeByFriendBotId[view.bot.botId] ?? 'all' : state.selectedScope;
  const list = conversationBotSelectionService.getList({
    botId: view.bot.botId,
    section: view.section,
    origin: 'mine',
    scope: effectiveScope,
  });
  if (list && !list.loading && !list.error && list.items.some((item) => item.sessionId === state.selectedSessionId))
    return null;
  const status =
    !list || list.loading
      ? 'loading'
      : list.error
      ? 'error'
      : list.items.length || list.hasMore
      ? 'unselected'
      : effectiveScope === 'favorite'
      ? 'favorite-empty'
      : 'empty';
  return {
    botName: view.bot.displayName,
    status,
    error: list?.error ?? undefined,
    creating,
    create: async () => {
      if (inFlight.current || !options.userId || status !== 'empty') return;
      inFlight.current = true;
      setCreating(true);
      try {
        await options.sessions.createSession(view.bot.botId);
      } catch {
        toast.error('创建会话失败，请稍后重试。');
      } finally {
        inFlight.current = false;
        setCreating(false);
      }
    },
    retry: () => options.sessions.retrySessions(view.bot.botId, view.section),
    showAll: () => {
      conversationBotSelectionService.showAll(view.bot.botId, view.section);
      options.sessions.retrySessions(view.bot.botId, view.section);
    },
  };
}
