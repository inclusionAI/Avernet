import { serviceBotEvalService } from '@/services/botWorkshop/serviceBotEvalService';
import type { BotDomain } from '@/services/botWorkshop';
import { useEffect, useMemo, useState } from 'react';

const evalAvailabilityKey = (bot: BotDomain) =>
  `${bot.id}:${bot.ownerId ?? ''}:${String(bot.publicationVersion ?? bot.liveVersion ?? 'latest')}`;

/** 服务 Bot 草稿态列表项的评测入口可见性。 */
export function useServiceBotEvalMenuVisibility(bots: BotDomain[]) {
  const targets = useMemo(
    () =>
      bots.filter((bot) => bot.serviceMode === 'service' && bot.lifecycle === 'draft'),
    [bots],
  );
  const [visibility, setVisibility] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!targets.length) return;
    let cancelled = false;
    const loadVisibility = async () => {
      const entries = await Promise.all(
        targets.map(async (bot) => {
          const version = bot.publicationVersion ?? bot.liveVersion ?? 'latest';
          try {
            const detail = (await serviceBotEvalService.detail(
              bot.id,
              String(version),
              bot.ownerId,
            ));
            return [
              evalAvailabilityKey(bot),
              detail.tabs?.simple_cert_submit?.visible !== false,
            ] as const;
          } catch {
            return [evalAvailabilityKey(bot), false] as const;
          }
        }),
      );
      if (!cancelled) setVisibility(Object.fromEntries(entries));
    };
    void loadVisibility();
    return () => {
      cancelled = true;
    };
  }, [targets]);

  const decoratedBots = useMemo(
    () =>
      bots.map((bot) => ({
        ...bot,
        evalGateVisible: visibility[evalAvailabilityKey(bot)] ?? false,
      })),
    [bots, visibility],
  );

  return decoratedBots;
}
