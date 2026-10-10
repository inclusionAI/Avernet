import type { ConversationBotSection, ConversationBotView } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import {
  conversationSessionActionService,
  type ConversationSessionAction,
} from '@/services/workspace/conversationSessionActionService';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface ConversationSessionListActions {
  run(sessionId: string, action: ConversationSessionAction): Promise<boolean>;
  pending: boolean;
}

export interface ConversationSessionActionsModel {
  run(
    botId: string,
    section: ConversationBotSection,
    sessionId: string,
    action: ConversationSessionAction,
  ): Promise<boolean>;
  isPending(botId: string, section: ConversationBotSection): boolean;
}

const keyOf = (botId: string, section: ConversationBotSection) => JSON.stringify([section, botId]);
const successMessage = { rename: '会话已重命名', clear: '会话上下文已清除', delete: '会话已删除' };

/** 同一 Bot 的会话写操作串行；账号变化/卸载后旧请求既不写回也不弹成功提示。 */
export function useConversationSessionActions(options: {
  userId: string | null;
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
}): ConversationSessionActionsModel {
  const { userId } = options;
  const context = useRef(options);
  context.current = options;
  const epoch = useRef(0);
  const requests = useRef(new Set<string>());
  const [, refresh] = useState(0);
  useEffect(() => {
    epoch.current += 1;
    requests.current.clear();
    refresh((value) => value + 1);
    return () => {
      epoch.current += 1;
    };
  }, [userId]);

  const run = useCallback(
    async (botId: string, section: ConversationBotSection, sessionId: string, action: ConversationSessionAction) => {
      if (!userId || context.current.userId !== userId) return false;
      const view = (
        isManagedConversationSection(section) ? context.current.managedBots : context.current.friendBots
      ).find((item) => item.bot.botId === botId);
      const key = keyOf(botId, section);
      if (!view || requests.current.has(key)) return false;
      const started = epoch.current;
      const isCurrent = () => started === epoch.current && context.current.userId === userId;
      const target = { bot: view.bot, section, sessionId, userId };
      requests.current.add(key);
      refresh((value) => value + 1);
      try {
        const result = await conversationSessionActionService.execute({ ...target, action });
        if (!isCurrent()) return false;
        if (!result.ok) {
          toast.error(result.error.friendlyMessage);
          return false;
        }
        conversationSessionActionService.apply(target, result.data);
        toast.success(successMessage[action.type]);
        return true;
      } catch {
        if (isCurrent()) toast.error('会话操作失败，请稍后重试。');
        return false;
      } finally {
        if (isCurrent()) {
          requests.current.delete(key);
          refresh((value) => value + 1);
        }
      }
    },
    [userId],
  );

  return { run, isPending: (botId, section) => requests.current.has(keyOf(botId, section)) };
}
