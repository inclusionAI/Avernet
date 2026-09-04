import type { ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useEffect, useState } from 'react';

export interface UseOwnedBotsResult {
  chatBots: ChatBotView[];
  hasAgentCodingBots: boolean;
  isLoading: boolean;
}

/** 侧边栏「已管理 Bot」数据源：用户身份下通过 GET /openapi/v1/bots 拉取，Bot 身份返回空列表。 */
export function useOwnedBots(activeIdentityId: string | null, isUserIdentity: boolean): UseOwnedBotsResult {
  const [chatBots, setChatBots] = useState<ChatBotView[]>([]);
  const [hasAgentCodingBots, setHasAgentCodingBots] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!activeIdentityId || !isUserIdentity) {
      setChatBots([]);
      setHasAgentCodingBots(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    void botSessionService
      .listOwnedBotsWithMeta(activeIdentityId)
      .then((res) => {
        if (cancelled) return;
        setChatBots(res.ok ? res.data.bots : []);
        setHasAgentCodingBots(res.ok ? res.data.hasAgentCodingBots : false);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeIdentityId, isUserIdentity]);

  return { chatBots, hasAgentCodingBots, isLoading };
}
