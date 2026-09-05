import { getCapabilities } from '@/capabilities';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { splitBotId } from '@/services/workspace/botSessionService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { useCallback, useEffect, useState } from 'react';

export interface UseFriendBotsResult {
  friendBots: ChatBotView[];
  isLoading: boolean;
  error: string | null;
  reload: () => void;
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
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setFriendBots([]);
      setError(null);
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
      setError(null);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    collaborationCandidateService
      .listFriends(actorId, {
        actorType: isUserIdentity ? 'human' : 'bot',
        offset: 0,
        limit: 100,
      })
      .then((res) => {
        if (cancelled) return;
        if (res.ok) setFriendBots(res.data.items.map(toChatBotView));
        else {
          setFriendBots([]);
          setError(res.error.friendlyMessage);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeIdentityId, isUserIdentity, enabled, reloadNonce]);

  const reload = useCallback(() => setReloadNonce((current) => current + 1), []);

  return { friendBots, isLoading, error, reload };
}
