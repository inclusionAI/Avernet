import type { BotDomain, BotManagementVerb } from '@/domain/botWorkshop';
import { isDormantPersonalBot } from '@/services/botWorkshop/botDormantService';
import { botManagementActionService } from '@/services/botWorkshop/botManagementActionService';
import { getBotManagementErrorMessage } from '@/services/botWorkshop/botWorkshopErrorPolicy';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';

export const RESTART_CATCH_UP_DELAY_MS = 3_000;

const RESTART_ACTIONS = new Set<BotManagementVerb>(['restart', 'engine_restart', 'restart_publish']);

type BotWorkshopLoad = (options?: { silent?: boolean }) => Promise<void>;

export function useBotWorkshopActions(load: BotWorkshopLoad, userId?: string) {
  const pending = useRef(new Set<string>());
  const restartCatchUpTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(
    () => () => {
      if (restartCatchUpTimer.current) clearTimeout(restartCatchUpTimer.current);
    },
    [],
  );

  const scheduleRestartCatchUp = useCallback(() => {
    if (restartCatchUpTimer.current) clearTimeout(restartCatchUpTimer.current);
    restartCatchUpTimer.current = setTimeout(() => {
      restartCatchUpTimer.current = undefined;
      void load({ silent: true });
    }, RESTART_CATCH_UP_DELAY_MS);
  }, [load]);

  return useCallback(
    async (action: BotManagementVerb, bot: BotDomain) => {
      const key = `${bot.ownerId}:${bot.id}:${action}`;
      if (pending.current.has(key)) return;
      pending.current.add(key);
      try {
        if (bot.runtime?.engine === 'teclaw' && RESTART_ACTIONS.has(action)) {
          throw new Error('TeClaw 暂不支持重启操作');
        }
        const result =
          action === 'activate'
            ? await botManagementActionService.run(action, bot, userId)
            : await botManagementActionService.run(action, bot);
        const updateActivation = () => {
          if (!result) return;
          useBotWorkshopStore.setState((state) => ({
            items: state.items.map((item) =>
              item.id === bot.id &&
              item.ownerId === bot.ownerId &&
              (isDormantPersonalBot(item) || item.rawStatus?.toUpperCase() === 'REACTIVATING')
                ? {
                    ...item,
                    rawStatus: result.status,
                    rawDisplayState: undefined,
                    lifecycle: result.status.toUpperCase() === 'ACTIVE' ? 'running' : 'deploying',
                    actions: item.actions.filter((verb) => verb !== 'activate'),
                  }
                : item,
            ),
          }));
        };
        updateActivation();
        if (action === 'delete' && bot.deployment === 'local') {
          useWorkspaceStore.setState((workspace) => {
            const expandedBotIds = { ...workspace.expandedBotIds };
            const expandedBotSectionKey = { ...workspace.expandedBotSectionKey };
            delete expandedBotIds[bot.id];
            delete expandedBotSectionKey[bot.id];
            return {
              expandedBotIds,
              expandedBotSectionKey,
              selectedBotSessionId: workspace.expandedBotIds[bot.id] ? null : workspace.selectedBotSessionId,
              lastSessionByIdentity: Object.fromEntries(
                Object.entries(workspace.lastSessionByIdentity).map(([id, memo]) => [
                  id,
                  memo.expandedBotId === bot.id
                    ? { ...memo, expandedBotId: null, botSessionId: null, botSectionKey: null }
                    : memo,
                ]),
              ),
            };
          });
        }
        toast.success(botManagementActionService.successMessage(action));
        await load();
        // Mutation success only means the restart was accepted. The first
        // inventory read can still observe the previous runtime status, so issue
        // one bounded silent follow-up and let that response stay authoritative.
        if (RESTART_ACTIONS.has(action)) scheduleRestartCatchUp();
      } catch (error) {
        const fallback = action === 'delete' ? 'Bot 删除失败，请稍后重试' : '操作失败';
        toast.error(getBotManagementErrorMessage(action, error, fallback));
        throw error;
      } finally {
        pending.current.delete(key);
      }
    },
    [load, scheduleRestartCatchUp, userId],
  );
}
