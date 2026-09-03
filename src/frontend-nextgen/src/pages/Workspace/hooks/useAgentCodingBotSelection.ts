import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useState } from 'react';

interface UseAgentCodingBotSelectionOptions {
  activeIdentityId?: string | null;
  toggleBotExpanded: (botId: string, sectionKey?: string) => void;
}

export function useAgentCodingBotSelection({ activeIdentityId, toggleBotExpanded }: UseAgentCodingBotSelectionOptions) {
  const [selectedAgentCodingBot, setSelectedAgentCodingBot] = useState<ChatBotView | null>(null);

  useEffect(() => {
    setSelectedAgentCodingBot(null);
  }, [activeIdentityId]);

  const onSelectAgentCodingBot = useCallback((bot: ChatBotView) => {
    if (!bot.isAgentCodingBot) return;
    useWorkspaceStore.getState().selectBotSession(null);
    setSelectedAgentCodingBot(bot);
  }, []);

  const onToggleBotExpanded = useCallback(
    (botId: string, sectionKey?: string) => {
      setSelectedAgentCodingBot(null);
      toggleBotExpanded(botId, sectionKey);
    },
    [toggleBotExpanded],
  );

  return { selectedAgentCodingBot, onSelectAgentCodingBot, onToggleBotExpanded };
}
