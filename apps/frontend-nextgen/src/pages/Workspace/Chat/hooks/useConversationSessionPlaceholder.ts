import type { ConversationBotView } from '@/domain/conversation';
import { conversationBotSelectionService } from '@/services/workspace/conversationBotSelectionService';
import { useConversationStore } from '@/stores/conversationStore';
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
  const list = conversationBotSelectionService.getList({
    botId: view.bot.botId,
    section: view.section,
    origin: 'mine',
    scope: state.selectedScope,
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
      : state.selectedScope === 'favorite'
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
      conversationBotSelectionService.showAll(view.bot.botId);
      options.sessions.retrySessions(view.bot.botId, view.section);
    },
  };
}
