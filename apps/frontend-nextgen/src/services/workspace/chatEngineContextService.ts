import { mapSpaceDto } from '@/domain/admin/mappers';
import { listSpaces } from '@/services/backendApi/admin/spaceController';
import { listBotInventory } from '@/services/backendApi/bots/botController';
import { isEnvelopeFailure, type BackendUnknownRecord } from '@/services/backendApi/types';
import { resolveServiceEngineLifecycle } from '@/services/botWorkshop/botLifecycleResolver';
import { mapBotActionPermissions } from './botActionPermissionMapper';
import type { ChatBotView } from './botSessionService';

const PAGE_SIZE = 100;

function readPage(response: unknown): { items: BackendUnknownRecord[]; total: number } {
  const envelope = response as { data?: { items?: BackendUnknownRecord[]; total?: number } };
  const { items, total } = envelope.data ?? {};
  if (isEnvelopeFailure(response) || !Array.isArray(items) || typeof total !== 'number' || !Number.isFinite(total) || total < 0) {
    throw new Error('Engine context page is unavailable');
  }
  return { items, total };
}

/** Optional advisory metadata, never the source of chat access or routing. */
export async function loadChatEngineContext(bot: ChatBotView, userId: string, signal: AbortSignal): Promise<ChatBotView | null> {
  const ownerId = bot.engineOwnerId ?? bot.ownerId;
  if (!ownerId || !userId.trim() || (bot.isTeamBot && !bot.spaceId)) return null;
  let spaceId: string | undefined;
  // Resolve accessibility for the acting user, not the Bot's owner. A
  // collaboration record alone never authorizes a Space inventory read.
  for (let page = 1; !signal.aborted; page += 1) {
    const result = readPage(await listSpaces({ user_id: userId, scope: 'accessible', page_no: page, page_size: PAGE_SIZE }, signal));
    const found = result.items.map((row) => mapSpaceDto(row).item).find((space) =>
      bot.spaceId ? String(space.spaceId) === bot.spaceId : space.spaceType === 'PERSONAL' && ownerId === userId,
    );
    if (found) { spaceId = String(found.spaceId); break; }
    if (!result.items.length || page * PAGE_SIZE >= result.total) break;
  }
  if (!spaceId || signal.aborted) return null;

  const matches: BackendUnknownRecord[] = [];
  for (let page = 1; !signal.aborted; page += 1) {
    // The existing inventory API has no bot_id filter. Narrow by the selected
    // name and validate the exact Bot/owner below; never enrich other Bots.
    const result = readPage(await listBotInventory({ user_id: userId, keyword: bot.displayName, page, page_size: PAGE_SIZE }, spaceId, signal));
    if (result.items.length !== Math.min(PAGE_SIZE, Math.max(0, result.total - (page - 1) * PAGE_SIZE))) return null;
    matches.push(...result.items.filter((row) => row.bot_id === bot.realBotId && row.owner_entity_id === ownerId));
    if (!result.items.length || page * PAGE_SIZE >= result.total) break;
  }
  if (signal.aborted || !matches.length) return null;
  const serviceBot = bot.botType?.trim().toLowerCase() === 'service';
  if (serviceBot && matches.some((row) => resolveServiceEngineLifecycle(row) !== 'draft')) return null;
  // Conflicting cards cannot lend each other restart capability.
  const permissions = matches.map((row) => mapBotActionPermissions(row.actions, row.disabled_actions));
  if (permissions.some((permission) => !permission.actions.includes('restart') || permission.disabledActions.restart)) return null;
  return { ...bot, ...permissions[0], ...(serviceBot ? { serviceLifecycle: 'draft' as const } : {}) };
}
