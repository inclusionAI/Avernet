import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import type { IdentityView } from '@/domain/collaboration';
import { useEffect, useMemo, useState } from 'react';
import type { UseGroupCollaborationPickerResult } from './useGroupCollaborationPicker';

type BotOption = { id: string; name: string; avatarUrl?: string };

/** 合并各列表与语义发现元数据；切换搜索后仍保留已选发现 Bot 的名称与头像。 */
export function useAllAvailableBots(
  activeIdentity: IdentityView | null | undefined,
  picker: UseGroupCollaborationPickerResult,
): BotOption[] {
  const actorId = activeIdentity?.id;
  const [discovered, setDiscovered] = useState<{ actorId: string | undefined; bots: BotOption[] }>(
    () => ({ actorId, bots: [] }),
  );
  // 仅缓存同一工作身份下的发现元数据，不改变可见列表或普通候选的分页。
  useEffect(() => {
    setDiscovered((previous) => {
      if (previous.actorId === actorId && picker.recommendations.length === 0) return previous;
      const byId = new Map((previous.actorId === actorId ? previous.bots : []).map((bot) => [bot.id, bot]));
      picker.recommendations.forEach(({ id, name, avatarUrl }) => byId.set(id, { id, name, avatarUrl }));
      return { actorId, bots: Array.from(byId.values()) };
    });
  }, [actorId, picker.recommendations]);

  return useMemo(() => {
    const map = new Map<string, BotOption>();
    [
      ...(discovered.actorId === actorId ? discovered.bots : []),
      ...picker.friends, ...picker.mine, ...picker.candidates, ...picker.recommendations,
    ].forEach((bot) => {
      map.set(bot.id, { id: bot.id, name: bot.name, avatarUrl: resolveBotAvatarUrl(bot.avatarUrl, bot.id) });
    });
    if (activeIdentity?.kind === 'bot') {
      map.set(activeIdentity.id, {
        id: activeIdentity.id,
        name: activeIdentity.displayName,
        avatarUrl: resolveBotAvatarUrl(activeIdentity.avatarUrl?.trim() || map.get(activeIdentity.id)?.avatarUrl, activeIdentity.id),
      });
    }
    return Array.from(map.values());
  }, [activeIdentity, actorId, discovered, picker.candidates, picker.friends, picker.mine, picker.recommendations]);
}
