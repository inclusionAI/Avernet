import type { SpaceBot } from '@/domain/evolution/types';
import type { evolutionApi } from '@/services/evolution/evolutionService';
import { useCallback, useMemo } from 'react';
import { useResource } from './shared';

/** Resolve current-space names by owner + Bot ID, including directory pages after the first. */
export function useEvolutionBotNames(context: { api: typeof evolutionApi; key: string; ready: boolean }, enabled = true) {
  const bots = useResource(
    useCallback(async () => {
      const items: SpaceBot[] = [];
      for (let page = 1; ; page += 1) {
        const batch = await context.api.bots(page);
        items.push(...batch.items);
        if (!batch.items.length || items.length >= batch.total) return items;
      }
    }, [context.key, context.api]),
    context.ready && enabled,
  );
  const names = useMemo(() => {
    const result = new Map<string, string>();
    for (const bot of bots.data ?? []) {
      if (bot.botName?.trim()) result.set(JSON.stringify([bot.ownerId, bot.botId]), bot.botName);
    }
    return result;
  }, [bots.data]);
  return useCallback(
    (ownerId: string, botId: string) => names.get(JSON.stringify([ownerId, botId])) || botId,
    [names],
  );
}
