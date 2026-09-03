import { parseSquareDeepLink } from '@/domain/collaborationSquare/mapper';
import type { PublicBot, PublicGroup, SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { useEffect } from 'react';

interface UseSquareDeepLinkOptions {
  resource: SquareResource;
  openBotProfile: (bot: PublicBot) => Promise<void>;
  openGroupMembers: (group: PublicGroup) => Promise<void>;
  handleTargetInvalid: (resource: SquareResource, id: string) => void;
}

/**
 * bot/group 的 SquareDeepLink 深链解析。任务广场本期不接深链（Non-goal），resource='task' 时跳过。
 * 从 useCollaborationSquare 抽出以守 Hook ≤250 行体积门禁；行为与原内联 effect 等价。
 */
export function useSquareDeepLink({
  resource,
  openBotProfile,
  openGroupMembers,
  handleTargetInvalid,
}: UseSquareDeepLinkOptions) {
  const loading = useCollaborationSquareStore((state) => state.loading);
  const bots = useCollaborationSquareStore((state) => state.bots);
  const groups = useCollaborationSquareStore((state) => state.groups);
  useEffect(() => {
    if (loading || resource === 'task' || typeof window === 'undefined') return;
    const deepLink = parseSquareDeepLink(window.location.search, resource);
    if (!deepLink) return;
    if (resource === 'bot') {
      const bot = bots.find((item) => item.id === deepLink.id);
      if (bot) void openBotProfile(bot);
      else handleTargetInvalid('bot', deepLink.id);
    } else {
      const group = groups.find((item) => item.id === deepLink.id);
      if (group) void openGroupMembers(group);
      else handleTargetInvalid('group', deepLink.id);
    }
  }, [bots, groups, handleTargetInvalid, loading, openBotProfile, openGroupMembers, resource]);
}
