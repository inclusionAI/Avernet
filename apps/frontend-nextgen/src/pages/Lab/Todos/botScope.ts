import type { BotDomain } from '@/domain/botWorkshop';
import type { TodoBotOption, TodoSpace } from '@/domain/todos/models';

export function botBelongsToCurrentSpace(
  bot: Pick<BotDomain, 'id' | 'ownerId'>,
  space: TodoSpace | undefined,
  currentUserId: string | undefined,
): boolean {
  if (!space) return false;
  if (space.spaceType === 'TEAM') return true;
  if (space.spaceType !== 'PERSONAL') return false;
  const ownerId = bot.ownerId?.trim();
  const userId = currentUserId?.trim();
  return Boolean(ownerId && userId && ownerId === userId);
}

export function eligibleTodoBots(
  bots: Array<Pick<BotDomain, 'id' | 'ownerId'> & { name?: string }>,
  space: TodoSpace | undefined,
  currentUserId: string | undefined,
): TodoBotOption[] {
  return bots
    .filter((bot) => botBelongsToCurrentSpace(bot, space, currentUserId))
    .map((bot) => ({ botId: bot.id, botName: bot.name || bot.id }));
}
