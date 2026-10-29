import { getBot, listBotMetadata } from '@/services/backendApi/bots/botController';
import type { BackendUnknownRecord } from '@/services/backendApi/types';
import { splitBotId } from '@/services/collaborationSquare/collaborationSquareApiHelpers';

function trimmedString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function botName(dto: BackendUnknownRecord | undefined): string {
  return trimmedString(dto?.bot_name) || trimmedString(dto?.name);
}

function metadataKey(botId: string, ownerId: string): string {
  return `${botId}:${ownerId}`;
}

/**
 * Resolve BBS Bot display names from the Backend Bot inventory.
 *
 * BBS author/follow ids may be either bare backend bot ids or composite
 * `bot_id:owner_id` identities. The backend `/bots/metadata/queries` endpoint is
 * the same inventory used by BBS list enrichment, so use it for composite ids;
 * bare ids fall back to the single-bot detail endpoint.
 */
export async function resolveBackendBotNames(botIds: string[]): Promise<Record<string, string>> {
  const originalIds = [...new Set(botIds.map((id) => id.trim()).filter(Boolean))];
  if (!originalIds.length) return {};

  const names: Record<string, string> = {};
  const pairIds: string[] = [];
  for (const id of originalIds) {
    const { realBotId, ownerId } = splitBotId(id);
    if (realBotId && ownerId) pairIds.push(id);
  }

  if (pairIds.length) {
    try {
      const response = await listBotMetadata(
        { page: 1, page_size: pairIds.length },
        {
          bots: pairIds.map((id) => {
            const { realBotId, ownerId } = splitBotId(id);
            return { bot_id: realBotId, owner_id: ownerId! };
          }),
        },
      );
      const matches = new Map<string, string>();
      for (const item of response.data?.items ?? []) {
        const botId = trimmedString(item.bot_id);
        const ownerId = trimmedString(item.owner_id);
        const name = trimmedString(item.bot_name);
        if (botId && ownerId && name) matches.set(metadataKey(botId, ownerId), name);
      }
      for (const id of pairIds) {
        const { realBotId, ownerId } = splitBotId(id);
        const name = matches.get(metadataKey(realBotId, ownerId ?? ''));
        if (name) names[id] = name;
      }
    } catch {
      // A miss or transient batch failure falls back to per-id detail below.
    }
  }

  await Promise.all(
    originalIds.map(async (id) => {
      if (names[id]) return;
      const { realBotId, ownerId } = splitBotId(id);
      try {
        const response = await getBot(realBotId, ownerId || undefined);
        const name = botName(response.data as BackendUnknownRecord | undefined);
        if (name) names[id] = name;
      } catch {
        // Unresolvable identifiers remain absent so callers can fall back to their existing display value.
      }
    }),
  );

  return names;
}
