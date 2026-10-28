import type { ConversationBotSection, ConversationBotView } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { serializeConversationRoute } from '@/domain/conversation';
import {
  conversationSessionActionService,
  type ConversationSessionAction,
  type ConversationSessionActionFeatures,
} from '@/services/workspace/conversationSessionActionService';
import { useCallback, useEffect, useRef, useState } from 'react';
import { getCapabilities } from '@/capabilities';
import { toast } from 'sonner';

export type ConversationSessionFeatures = ConversationSessionActionFeatures;

export interface ConversationSessionListActions {
  run(sessionId: string, action: ConversationSessionAction): Promise<boolean>;
  pending: boolean;
  features?: ConversationSessionFeatures;
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
const successMessage = {
  rename: '会话已重命名',
  clear: '会话上下文已清除',
  delete: '会话已删除',
  share: '分享链接已复制，仅 Owner 和协作者可访问',
  logs: '日志已打开',
  dima: 'DIMA 关联面板已打开',
};

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

      // Coding Bot 的写操作必须走 AIXCoding embedded runtime，让 model/runtime、
      // git author、内部会话列表与消息状态保持原路由页语义。分享链接由主应用
      // 生成 TeamClaw 规范 URL，不复制子应用内部路由。
      if (view.bot.isAgentCodingBot) {
        const codingHost = getCapabilities().getCodingBotConversationHost().value;
        if (codingHost?.runSessionAction) {
          if (action.type === 'share') {
            const search = serializeConversationRoute({
              section,
              botId: view.bot.botId,
              ...(isManagedConversationSection(section) ? { origin: 'mine' } : {}),
              sessionId,
            });
            try {
              await navigator.clipboard.writeText(
                `${window.location.origin}/workspace/chat?${search}`,
              );
              toast.success(successMessage.share);
              return true;
            } catch {
              toast.error('复制分享链接失败');
              return false;
            }
          }

          requests.current.add(key);
          refresh((value) => value + 1);
          try {
            const result = await codingHost.runSessionAction({
              botId: view.bot.realBotId,
              userId,
              ownerId: view.bot.ownerId,
              action: (() => {
                if (action.type === 'rename') return { ...action, sessionId };
                if (
                  action.type === 'clear' ||
                  action.type === 'delete' ||
                  action.type === 'logs' ||
                  action.type === 'dima'
                ) {
                  return { type: action.type, sessionId };
                }
                return { type: 'share' as const, sessionId };
              })(),
            });
            if (action.type === 'rename' || action.type === 'clear' || action.type === 'delete') {
              if (result.ok) conversationSessionActionService.apply(target, action);
            }
            if (!result.ok) {
              toast.error(result.message);
              return false;
            }
            toast.success(successMessage[action.type]);
            return true;
          } catch {
            toast.error('Coding Bot 会话操作失败，请稍后重试。');
            return false;
          } finally {
            requests.current.delete(key);
            refresh((value) => value + 1);
          }
        }
        if (action.type === 'logs' || action.type === 'dima') {
          toast.error('Coding Bot 会话操作通道未就绪，请稍后重试。');
          return false;
        }
      }

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
