import type { BotDomain } from '@/domain/botWorkshop';
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';

export function todoBotCatalogId(bot: Pick<BotDomain, 'id' | 'ownerId'>): string | undefined {
  const botId = bot.id.trim();
  if (!botId) return undefined;
  if (botId.includes(':')) return botId;
  const ownerId = bot.ownerId?.trim();
  return ownerId ? `${botId}:${ownerId}` : undefined;
}

function responseCatalogId(bot: { bot_id: string; created_by?: string }): string | undefined {
  const botId = bot.bot_id.trim();
  if (!botId) return undefined;
  if (botId.includes(':')) return botId;
  const ownerId = bot.created_by?.trim();
  return ownerId ? `${botId}:${ownerId}` : undefined;
}

/** Query BCS collaboration visibility and fail closed for incomplete metadata. */
export async function loadPublicTodoBotCatalogIds(bots: BotDomain[]): Promise<Set<string>> {
  const requested = new Set(bots.map(todoBotCatalogId).filter((id): id is string => Boolean(id)));
  if (requested.size === 0) return new Set();

  const response = await queryCollaborationBots({ bot_ids: [...requested] });
  const confirmed = new Set<string>();
  for (const item of response.data?.items ?? []) {
    if (item.visibility !== 'public') continue;
    const id = responseCatalogId(item);
    if (id && requested.has(id)) confirmed.add(id);
  }
  return confirmed;
}
