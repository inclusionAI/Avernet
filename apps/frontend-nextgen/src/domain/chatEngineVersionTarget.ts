import { supportsEngineVersion, type EngineVersionTarget } from '@/domain/botEngineVersion';
import type { ConversationBotSection } from '@/domain/conversation/types';
import type { ChatBotView } from '@/services/workspace/botSessionService';

export interface ChatEngineVersionInput {
  bot: ChatBotView | null;
  section: ConversationBotSection | null;
  origin: 'mine' | 'others';
  sessionId: string | null;
  userId: string | null;
}

export function isChatEngineVersionCandidate(input: ChatEngineVersionInput): boolean {
  const { bot, section, origin, sessionId, userId } = input;
  if (
    !bot ||
    !sessionId ||
    !userId ||
    (section !== 'managed' && section !== 'team') ||
    origin !== 'mine' ||
    bot.isFriendBot ||
    // Published team services cannot use the draft-only engine inspection/update API.
    (section === 'team' && bot.botType === 'service') ||
    bot.botType?.trim().toLowerCase() === 'desktop' ||
    !bot.online ||
    bot.status?.toLowerCase() === 'hidden' ||
    !bot.chatable ||
    !bot.ownerId ||
    (bot.isTeamBot && !bot.engineOwnerId)
  )
    return false;

  return supportsEngineVersion(bot.engine?.trim() ?? '');
}

export function buildChatEngineVersionTarget(input: ChatEngineVersionInput): EngineVersionTarget | null {
  const { bot, section, sessionId, userId } = input;
  if (!bot || !bot.ownerId || !userId || !sessionId || !isChatEngineVersionCandidate(input)) return null;
  const engine = bot.engine?.trim() ?? '';
  if (!supportsEngineVersion(engine)) return null;

  return {
    permissionSource: 'backend',
    key: `chat:${userId}:${section}:${bot.botId}:${sessionId}`,
    botId: bot.realBotId,
    userId,
    ownerId: bot.engineOwnerId ?? bot.ownerId,
    engine,
    enabled: true,
    restartPermission: {
      actions: bot.actions ?? [],
      disabledActions: bot.disabledActions,
      serviceBot: bot.botType?.trim().toLowerCase() === 'service',
      // Chat engine inspection and restart address the draft runtime.
      runtimeStage: bot.runtimeStage ?? 'draft',
    },
  };
}
