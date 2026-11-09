import type { BotDomain, BotManagementVerb } from '@/domain/botWorkshop';
import { isDormantPersonalBot } from '@/services/botWorkshop/botDormantService';
import { botManagementActionService } from '@/services/botWorkshop/botManagementActionService';
import { applyRestartOutcome, type BotRestartResponse } from '@/services/botWorkshop/botRestartTracking';
import { getBotManagementErrorMessage } from '@/services/botWorkshop/botWorkshopErrorPolicy';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useRef } from 'react';
import { toast } from 'sonner';

const RESTART_ACTIONS = new Set<BotManagementVerb>(['restart', 'engine_restart', 'restart_publish']);

export function useBotWorkshopActions(load: () => Promise<void>, userId?: string) {
  const pending = useRef(new Set<string>());

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
          const status = typeof result.status === 'string' ? result.status : '';
          if (!status) return;
          useBotWorkshopStore.setState((state) => ({
            items: state.items.map((item) =>
              item.id === bot.id &&
              item.ownerId === bot.ownerId &&
              (isDormantPersonalBot(item) || item.rawStatus?.toUpperCase() === 'REACTIVATING')
                ? {
                    ...item,
                    rawStatus: status,
                    rawDisplayState: undefined,
                    lifecycle: status.toUpperCase() === 'ACTIVE' ? 'running' : 'deploying',
                    actions: item.actions.filter((verb) => verb !== 'activate'),
                  }
                : item,
            ),
          }));
        };
        updateActivation();
        if (action === 'restart') {
          // 重启响应已携带落库后的真值：同步引擎（openclaw）直接修正展示；
          // coding 引擎返回 ext.aicoding.restart journal，改为本地 pin + 库存轮询收敛。
          applyRestartOutcome(bot, result as BotRestartResponse | undefined);
        }
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
      } catch (error) {
        const fallback = action === 'delete' ? 'Bot 删除失败，请稍后重试' : '操作失败';
        toast.error(getBotManagementErrorMessage(action, error, fallback));
        throw error;
      } finally {
        pending.current.delete(key);
      }
    },
    [load, userId],
  );
}
