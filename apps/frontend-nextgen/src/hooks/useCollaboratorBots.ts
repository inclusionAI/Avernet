import type { BotDomain } from '@/domain/botWorkshop';
import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { splitBotId } from '@/services/workspace/botSessionService';
import { useEffect } from 'react';

/**
 * Load collaborative (friend) bots and merge into the inventory list.
 * Called by useBotWorkshop to add "我协作的 Bot" visibility to the
 * Bot management list.
 */
export function useCollaboratorBots(params: {
  userId?: string;
  ready: boolean;
  enabled: boolean;
  inventoryItems: BotDomain[];
  setCollaboratorBots: (bots: BotDomain[], loading: boolean, error?: string) => void;
}) {
  const { userId, ready, enabled, inventoryItems, setCollaboratorBots } = params;

  useEffect(() => {
    if (!ready || !enabled || !userId) {
      setCollaboratorBots([], false);
      return;
    }
    let cancelled = false;
    setCollaboratorBots([], true);
    collaborationCandidateService
      .listFriends(userId, {
        actorType: 'human',
        offset: 0,
        limit: 100,
      })
      .then((res) => {
        if (cancelled) return;
        if (!res.ok) {
          setCollaboratorBots([], false, res.error.friendlyMessage);
          return;
        }
        const inventoryIds = new Set(inventoryItems.map((item) => item.id));
        const mapped: BotDomain[] = res.data.items
          .filter((bot) => {
            const { realBotId } = splitBotId(bot.id);
            return !inventoryIds.has(realBotId) && !inventoryIds.has(bot.id);
          })
          .map((bot) => {
            const { realBotId, ownerId } = splitBotId(bot.id);
            const runtime = resolveBotRuntime({
              engine: bot.engine,
              botType: bot.botType,
              botId: realBotId,
            });
            return {
              id: realBotId,
              name: bot.name,
              ownerId,
              entityKey: `${ownerId ?? 'unknown'}:personal:${realBotId}`,
              spaceKind: 'personal' as const,
              ownership: 'personal' as const,
              deployment: 'cloud' as const,
              serviceMode: 'non-service' as const,
              canUpgradeToService: false,
              lifecycle: 'draft',
              runtime: {
                engine: runtime.engine || 'openclaw',
                isAgentCodingBot: runtime.isAgentCodingBot || false,
                templateType: bot.templateType,
                templateName: bot.templateName,
                capabilityProfile: {
                  canPublish: false,
                  canEdit: false,
                  canChat: true,
                  canViewLogs: false,
                },
                visibleInOpenCore: true,
                isDefaultBot: false,
              },
              completeness: 'complete' as const,
              warnings: [],
              actions: ['view', 'chat'],
              disabledActions: {
                edit: '协作 Bot 仅支持查看和对话',
                delete: '协作 Bot 仅支持查看和对话',
                restart: '协作 Bot 仅支持查看和对话',
                publish: '协作 Bot 仅支持查看和对话',
              },
              isCollaboratorBot: true,
            } as BotDomain;
          });
        if (!cancelled) setCollaboratorBots(mapped, false);
      })
      .catch(() => {
        if (!cancelled) setCollaboratorBots([], false, '协作 Bot 列表加载失败');
      });
    return () => {
      cancelled = true;
    };
  }, [userId, ready, enabled, inventoryItems, setCollaboratorBots]);
}
