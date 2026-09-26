import type { ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useCallback, useEffect, useState } from 'react';

export interface UseOwnedBotsResult {
  chatBots: ChatBotView[];
  hasAgentCodingBots: boolean;
  isLoading: boolean;
  error: string | null;
  reload: () => void;
}

/** 侧边栏「已管理 Bot」数据源：用户身份下通过 GET /openapi/v1/bots 拉取，Bot 身份返回空列表。 */
export function useOwnedBots(activeIdentityId: string | null, isUserIdentity: boolean): UseOwnedBotsResult {
  const [chatBots, setChatBots] = useState<ChatBotView[]>([]);
  const [hasAgentCodingBots, setHasAgentCodingBots] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    if (!activeIdentityId || !isUserIdentity) {
      setChatBots([]);
      setHasAgentCodingBots(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void botSessionService
      .listOwnedBotsWithMeta(activeIdentityId)
      .then((res) => {
        if (cancelled) return;
        if (res.ok) {
          setChatBots(res.data.bots);
          setHasAgentCodingBots(res.data.hasAgentCodingBots);
        } else {
          setChatBots([]);
          setHasAgentCodingBots(false);
          setError(res.error.friendlyMessage);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeIdentityId, isUserIdentity, reloadNonce]);

  const reload = useCallback(() => setReloadNonce((current) => current + 1), []);

  return { chatBots, hasAgentCodingBots, isLoading, error, reload };
}
