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
 * useFriendBots —— 通过 Friend Connections + metadata/queries 两步拉取当前身份的好友 Bot 列表，
 * 映射为 ChatBotView 与 mine Bot 统一消费。
 */
export function useFriendBots(
  activeIdentityId: string | null,
  isUserIdentity: boolean,
  enabled: boolean = true,
): UseFriendBotsResult {
  const [friendBots, setFriendBots] = useState<ChatBotView[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setFriendBots([]);
      return;
    }
    let actorId = activeIdentityId;
    if (isUserIdentity) {
      const currentUserId = getCapabilities().getCurrentOpenApiUserId({
        activeIdentityId: activeIdentityId ?? undefined,
      });
      if (currentUserId.status === 'available' && currentUserId.value) actorId = currentUserId.value;
    }
    if (!actorId || actorId === 'me') {
      setFriendBots([]);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    collaborationCandidateService
      .listFriends(actorId, {
        actorType: isUserIdentity ? 'human' : 'bot',
        offset: 0,
        limit: 100,
      })
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
  }, [activeIdentityId, isUserIdentity, enabled]);

  return { friendBots, isLoading };
}
