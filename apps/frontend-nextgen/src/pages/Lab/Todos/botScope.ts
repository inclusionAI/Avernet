import type { BotDomain } from '@/domain/botWorkshop';
import type { TodoBotOption, TodoSpace } from '@/domain/todos/models';
import { todoBotCatalogId } from '@/services/todos/todoBotCatalogService';

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
  publicCatalogIds: ReadonlySet<string>,
): TodoBotOption[] {
  return bots
    .filter((bot) => botBelongsToCurrentSpace(bot, space, currentUserId))
    .filter((bot) => {
      const catalogId = todoBotCatalogId(bot);
      return Boolean(catalogId && publicCatalogIds.has(catalogId));
    })
    .map((bot) => ({ botId: bot.id, botName: bot.name || bot.id }));
}
