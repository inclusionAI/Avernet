import { supportsEngineVersion, type EngineVersionTarget } from '@/domain/botEngineVersion';
import type { ConversationBotSection } from '@/domain/conversation/types';
import type { ChatBotView } from '@/services/workspace/botSessionService';

export function buildChatEngineVersionTarget(input: {
  bot: ChatBotView | null;
  section: ConversationBotSection | null;
  origin: 'mine' | 'others';
  sessionId: string | null;
  userId: string | null;
}): EngineVersionTarget | null {
  const { bot, section, origin, sessionId, userId } = input;
  if (
    !bot ||
    !sessionId ||
    !userId ||
    (section !== 'managed' && section !== 'team') ||
    origin !== 'mine' ||
    bot.isFriendBot ||
    bot.botType?.trim().toLowerCase() === 'desktop' ||
    !bot.online ||
    bot.status?.toLowerCase() === 'hidden' ||
    !bot.chatable ||
    !bot.ownerId ||
    (bot.isTeamBot && !bot.engineOwnerId)
  )
    return null;

  const engine = bot.engine?.trim() ?? '';
  if (!supportsEngineVersion(engine)) return null;

  return {
    key: `chat:${userId}:${section}:${bot.botId}:${sessionId}`,
    botId: bot.realBotId,
    userId,
    ownerId: bot.engineOwnerId ?? bot.ownerId,
    engine,
    enabled: true,
    restartPermission: {
      actions: bot.actions ?? [],
      disabledActions: bot.disabledActions,
      serviceBot: bot.botType?.toLowerCase() === 'service',
      // Chat engine inspection and restart address the draft runtime.
      runtimeStage: bot.runtimeStage ?? 'draft',
    },
  };
}
