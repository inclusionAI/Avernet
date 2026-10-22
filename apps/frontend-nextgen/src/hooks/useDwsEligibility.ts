import { dwsHostingService } from '@/services/botWorkshop/dwsHostingService';
import { useEffect, useState } from 'react';

export function useDwsEligibility(
  botId: string | null | undefined,
  actorId: string | null | undefined,
  eligibleBot: boolean,
) {
  const [grant, setGrant] = useState<{ key: string; available: boolean }>();
  const key = `${botId ?? ''}:${actorId ?? ''}`;
  useEffect(() => {
    setGrant(undefined);
    if (!botId || !actorId || !eligibleBot) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const refresh = async () => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const available = await dwsHostingService.eligibility(botId, controller.signal);
        if (active) setGrant({ key, available });
      } catch {
        if (active) setGrant({ key, available: false });
      } finally {
        clearTimeout(timeout);
        if (active) timer = setTimeout(refresh, 30000);
      }
    };
    void refresh();
    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [botId, actorId, eligibleBot, key]);
  return eligibleBot && grant?.key === key && grant.available === true;
}
