import type { Space } from '@/domain/admin/models';
import type { TodoBotOption } from '@/domain/todos/models';

export function botBelongsToCurrentSpace(
  bot: { id: string; ownerId?: string },
  space: Space | undefined,
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
  bots: Array<{ id: string; name?: string; ownerId?: string }>,
  space: Space | undefined,
  currentUserId: string | undefined,
): TodoBotOption[] {
  return bots
    .filter((bot) => botBelongsToCurrentSpace(bot, space, currentUserId))
    .filter((bot) => bot.id)
    .map((bot) => ({ botId: bot.id, botName: bot.name || bot.id }));
}
