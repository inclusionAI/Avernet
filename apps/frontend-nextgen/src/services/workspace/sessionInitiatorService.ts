import type { SessionInitiator } from '@/domain/conversation/sessionInitiator';
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { imageAvatarSource } from '@/utils/avatarSource';

interface Entry {
  value: SessionInitiator;
  expiresAt: number;
}
const MAX_ENTRIES = 500;
const BATCH_SIZE = 50;
let scope = '';
let cache = new Map<string, Entry>();
let pending = new Map<string, Promise<SessionInitiator>>();

/** Bounded, viewer-scoped, memory-only enrichment. Never blocks session/history requests. */
export async function loadSessionInitiators(
  viewerId: string,
  ids: string[],
): Promise<Record<string, SessionInitiator>> {
  if (!viewerId || !ids.length) return {};
  if (scope !== viewerId) {
    scope = viewerId;
    cache = new Map();
    pending = new Map();
  }
  // Capture this viewer's maps so late responses cannot populate another viewer's cache.
  const entries = cache;
  const requests = pending;
  const unique = [...new Set(ids.filter(Boolean))];
  const missing = unique.filter((id) => !requests.has(id) && (entries.get(id)?.expiresAt ?? 0) <= Date.now());
  for (let start = 0; start < missing.length; start += BATCH_SIZE) {
    const batch = missing.slice(start, start + BATCH_SIZE);
    const lookup = queryCollaborationBots({ bot_ids: batch.map((id) => `human_${id}`) })
      .then((response) => {
        const profiles = new Map<string, SessionInitiator>();
        for (const item of response.data?.items ?? []) {
          if (item.kind !== 'human' || !item.bot_id.startsWith('human_')) continue;
          const userId = item.bot_id.slice(6);
          if (!batch.includes(userId)) continue;
          const name = item.name?.trim();
          profiles.set(userId, {
            userId,
            displayName: name && name !== userId && name !== item.bot_id ? name : undefined,
            avatarUrl: imageAvatarSource(item.avatar_url),
          });
        }
        return profiles;
      })
      .catch(() => new Map<string, SessionInitiator>());
    for (const id of batch) {
      const request = lookup.then((profiles) => {
        const value = profiles.get(id) ?? { userId: id };
        entries.set(id, { value, expiresAt: Date.now() + (value.displayName ? 300_000 : 30_000) });
        while (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value!);
        requests.delete(id);
        return value;
      });
      requests.set(id, request);
    }
  }
  const result = await Promise.all(
    unique.map(async (id) => {
      const value = await (requests.get(id) ?? Promise.resolve(entries.get(id)?.value ?? { userId: id }));
      return [id, value] as const;
    }),
  );
  return Object.fromEntries(result);
}
