import type { MoreConfigTab } from '@/components/BotWorkshop/Editor/MoreConfigPanel';
import type { BotDomain } from '@/domain/botWorkshop';
import { canManageHosting } from '@/domain/dwsHosting';
import { useDwsEligibility } from '@/hooks/useDwsEligibility';
import { useEffect, useState } from 'react';
import { mainTabs, type MainTab } from './detailTabs';

export function useHostingTabs(
  id: string | null,
  actorId: string | null | undefined,
  bot: BotDomain | null | undefined,
  isOwner: boolean,
  routines: boolean,
) {
  const [tab, setTab] = useState<MainTab>('capability');
  const [more, setMore] = useState<MoreConfigTab>();
  useEffect(() => {
    setTab('capability');
    setMore(undefined);
  }, [id]);
  useEffect(() => {
    if (!isOwner && more === 'engine') setMore(undefined);
  }, [isOwner, more]);
  const hostingAllowed = useDwsEligibility(
    id,
    actorId,
    !!bot && canManageHosting(isOwner, bot.serviceMode, bot.runtime.engine, bot.deployment),
  );
  useEffect(() => {
    if (!hostingAllowed && tab === 'hosting') setTab('capability');
  }, [hostingAllowed, tab]);
  const visibleMainTabs = mainTabs.filter(
    (item) => (item.key !== 'routine' || routines) && (item.key !== 'hosting' || hostingAllowed),
  );
  return { tab, setTab, more, setMore, hostingAllowed, visibleMainTabs };
}
