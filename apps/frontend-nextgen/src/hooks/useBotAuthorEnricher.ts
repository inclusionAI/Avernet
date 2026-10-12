// 社区列表「BOT 作者展示名」回补子 Hook：抽自 useCommunity，把 BOT 作者的 displayName
// 用本人 owned bots 的 bot_name 回补（doc §8 填充规则：后端 §4.1 对 BOT 作者不返 display_name），HUMAN 不动留工号兜底。
// 共享身份：useOwnedBots 取本人 bots（GET /openapi/v1/bots?owner_id=本人 与任务列表同源），botNameById 给回补与「晚到回补」共用。
import type { CommunityAuthor, CommunityTopic } from '@/domain/community/types';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useCallback, useMemo } from 'react';

export interface UseBotAuthorEnricherResult {
  /** 用本人 owned bots 的 bot_name 改写 BOT 作者的 displayName；HUMAN / 未匹配不动。 */
  enrichTopicBotAuthor: (topic: CommunityTopic) => CommunityTopic;
  /** 已就绪的 bot_id→bot_name 映射；空对象表示 bot 列表未就绪。 */
  botNameById: Record<string, string>;
}

function buildBotNameById(bots: ChatBotView[]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const bot of bots) {
    const name = bot.displayName?.trim();
    if (name && bot.realBotId && name !== bot.realBotId) map[bot.realBotId] = name;
  }
  return map;
}

export function useBotAuthorEnricher(currentUserId: string): UseBotAuthorEnricherResult {
  const { chatBots } = useOwnedBots(currentUserId || null, currentUserId.length > 0);
  const botNameById = useMemo<Record<string, string>>(() => buildBotNameById(chatBots), [chatBots]);

  const enrichTopicBotAuthor = useCallback(
    (topic: CommunityTopic): CommunityTopic => {
      if (topic.author.type !== 'bot') return topic;
      const botName = botNameById[topic.author.id];
      if (botName && botName !== topic.author.displayName) {
        const author: CommunityAuthor = { ...topic.author, displayName: botName };
        return { ...topic, author };
      }
      return topic;
    },
    [botNameById],
  );

  return { enrichTopicBotAuthor, botNameById };
}
