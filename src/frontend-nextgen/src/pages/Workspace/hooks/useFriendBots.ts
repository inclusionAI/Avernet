import { getCapabilities } from '@/capabilities';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { splitBotId } from '@/services/workspace/botSessionService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { useEffect, useState } from 'react';

export interface UseFriendBotsResult {
  friendBots: ChatBotView[];
  isLoading: boolean;
}

function toChatBotView(bot: {
  id: string;
  name: string;
  online: boolean;
  reachability?: string;
  engine?: string;
  botType?: string;
}): ChatBotView {
  const { realBotId, ownerId } = splitBotId(bot.id);
  return {
    botId: bot.id,
    realBotId,
    ownerId,
    displayName: bot.name,
    online: bot.online,
    reachability: bot.reachability === 'unreachable' ? 'unreachable' : 'reachable',
    chatable: true,
    engine: bot.engine,
    botType: bot.botType,
  };
}

/**
 * useFriendBots —— 当活跃身份为用户（human）时，通过 friendships + metadata/queries 两步拉取好友 Bot 列表，
 * 映射为 ChatBotView 与 mine Bot 统一消费。Bot 身份不加载好友（返回空数组）。
 */
export function useFriendBots(activeIdentityId: string | null, isUserIdentity: boolean): UseFriendBotsResult {
  const [friendBots, setFriendBots] = useState<ChatBotView[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    const currentUserId = getCapabilities().getCurrentOpenApiUserId({
      activeIdentityId: activeIdentityId ?? undefined,
    });
    const actorId =
      currentUserId.status === 'available' && currentUserId.value ? `human_${currentUserId.value}` : activeIdentityId;
    if (!actorId || (!isUserIdentity && !actorId.startsWith('human_'))) {
      setFriendBots([]);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    collaborationCandidateService
      .listFriends(actorId, { offset: 0, limit: 100 })
      .then((res) => {
        if (cancelled) return;
        setFriendBots(res.ok ? res.data.items.map(toChatBotView) : []);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeIdentityId, isUserIdentity]);

  return { friendBots, isLoading };
}
