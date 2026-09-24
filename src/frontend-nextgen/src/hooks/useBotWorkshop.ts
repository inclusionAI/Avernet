import { getCapabilities } from '@/capabilities';
import type { BotManagementVerb } from '@/domain/botWorkshop';
import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useBotWorkshopNavigation } from '@/hooks/useBotWorkshopNavigation';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { useVisibleInterval } from '@/hooks/useVisibleInterval';
import { botHealthCheckService } from '@/services/botHealthCheck';
import type { BotDomain } from '@/services/botWorkshop';
import { botWorkshopService, getBotActionAvailability, getInventoryActionAvailability } from '@/services/botWorkshop';
import { botManagementService } from '@/services/botWorkshop/botManagementService';
import { getBotManagementErrorMessage } from '@/services/botWorkshop/botWorkshopErrorPolicy';
import { localBotService } from '@/services/botWorkshop/localBotService';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { useAgentCodingTemplates } from './useAgentCodingTemplates';
import { useBotWorkshopAccess } from './useBotWorkshopAccess';
import { useBotWorkshopCreateFlow } from './useBotWorkshopCreateFlow';
import { useBotWorkshopLocks } from './useBotWorkshopLocks';

/** 动作名即路由键：新增动词时补一行 runner/toast，漏补会在编译期报错而非静默无操作。 */
const RUN_ACTION_RUNNER: Record<BotManagementVerb, (bot: BotDomain) => Promise<void>> = {
  open_folder: async (bot) => {
    await localBotService.openFolder(bot.id);
  },
  delete: (bot) => botWorkshopService.remove(bot),
  restart: (bot) => botWorkshopService.restart(bot),
  engine_restart: (bot) =>
    bot.deployment === 'local' ? botWorkshopService.restart(bot) : botWorkshopService.restartEngine(bot.id),
  upgrade: (bot) => botWorkshopService.enableService(bot.id),
  restart_publish: (bot) => botWorkshopService.restartPublish(bot),
};

const RUN_ACTION_SUCCESS_TOAST: Record<BotManagementVerb, string> = {
  open_folder: '打开目录请求已提交',
  delete: 'Bot 已删除',
  restart: '重启请求已提交',
  engine_restart: '重启请求已提交',
  upgrade: '已开启服务化',
  restart_publish: '重启发布已提交',
};

export function useBotWorkshop() {
  const state = useBotWorkshopStore();
  const requestIdentity = useBotWorkshopRequestIdentity();
  const activeIdentityId = useWorkspaceStore((workspace) => workspace.activeIdentityId);
  const currentSpaceId = useSpaceContext((space) => space.currentSpaceId);
  const currentSpace = useSpaceContext((space) => space.currentSpace);
  const spaceInitialized = useSpaceContext((space) => space.initialized);
  const spaceLoading = useSpaceContext((space) => space.loading);
  const spaceError = useSpaceContext((space) => space.error);
  const spaceId = currentSpaceId === undefined ? '' : String(currentSpaceId);
  const loadSequence = useRef(0);
  const { keyword, engine, deployment, serviceMode, page, pageSize } = state;
  const canUseAgentCoding = getCapabilities()
    .getBotEngineOptions()
    .value.some(({ value }) => value === 'aicoding');
  const agentCodingTemplates = useAgentCodingTemplates(state.createScenario === 'cloud' && canUseAgentCoding);
  const navigation = useBotWorkshopNavigation();
  const currentUser = getCapabilities().getCurrentOpenApiUserId({ activeIdentityId });
  const currentOpenApiUserId = currentUser.status === 'available' ? currentUser.value?.trim() || undefined : undefined;
  const load = useCallback(
    async (options?: { silent?: boolean }) => {
      if (!requestIdentity.ready || !spaceInitialized || !spaceId) return;
      const sequence = ++loadSequence.current;
      if (!options?.silent) {
        const current = useBotWorkshopStore.getState();
        state.setResult({
          items: current.items,
          total: current.total,
          hasMore: current.hasMore,
          loading: true,
          error: undefined,
        });
      }
      try {
        const result = await botWorkshopService.list({
          currentUserId: currentOpenApiUserId,
          spaceId,
          keyword,
          engine,
          deployment,
          serviceMode,
          page,
          pageSize,
        });
        result.items = await botManagementService.fillOwnerNames(result.items, currentOpenApiUserId);
        if (sequence !== loadSequence.current) return;
        state.setResult({
          items: result.items,
          total: result.total,
          hasMore: result.hasMore,
          loading: false,
          error: undefined,
        });
      } catch (error) {
        if (sequence !== loadSequence.current) return;
        if (options?.silent) return;
        const message = error instanceof Error ? error.message : 'Bot 列表加载失败';
        state.setResult({ items: [], total: undefined, hasMore: undefined, loading: false, error: message });
      }
    },
    [
      currentOpenApiUserId,
      deployment,
      engine,
      keyword,
      page,
      pageSize,
      requestIdentity.ready,
      serviceMode,
      spaceId,
      spaceInitialized,
    ],
  );
  useEffect(() => {
    void load();
  }, [load]);
  const createFlow = useBotWorkshopCreateFlow({ load, setCreateScenario: state.setCreateScenario });
  const refreshVisibleList = useCallback(() => void load({ silent: true }), [load]);
  useVisibleInterval(
    refreshVisibleList,
    30_000,
    requestIdentity.ready && spaceInitialized && Boolean(spaceId) && !createFlow.creating && !createFlow.authorization,
  );
  const accessControl = useBotWorkshopAccess(currentOpenApiUserId, load);
  const openHealthCheck = useCallback(
    (bot: BotDomain) => {
      const target = botHealthCheckService.toTarget(bot, currentOpenApiUserId);
      if (!target) {
        const availability = botHealthCheckService.resolveAvailability(bot, currentOpenApiUserId);
        toast.error(availability.disabledReason ?? '缺少当前用户身份，无法发起健康检查');
        return;
      }
      history.push(`/bot-workshop/health-check?id=${encodeURIComponent(target.botId)}`);
    },
    [currentOpenApiUserId],
  );
  const runAction = useCallback(
    async (action: BotManagementVerb, bot: BotDomain) => {
      try {
        if (bot.runtime?.engine === 'teclaw' && ['restart', 'engine_restart', 'restart_publish'].includes(action))
          throw new Error('TeClaw 暂不支持重启操作');
        await RUN_ACTION_RUNNER[action](bot);
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
        toast.success(RUN_ACTION_SUCCESS_TOAST[action]);
        await load();
      } catch (error) {
        const fallback = action === 'delete' ? 'Bot 删除失败，请稍后重试' : '操作失败';
        toast.error(getBotManagementErrorMessage(action, error, fallback));
        throw error;
      }
    },
    [load],
  );
  const locks = useBotWorkshopLocks(load);
  return {
    ...state,
    spaceId,
    currentSpaceKind: currentSpace?.spaceType === 'TEAM' ? 'team' : 'personal',
    canOpenConversation: (bot: BotDomain) => Boolean(currentOpenApiUserId && bot.ownerId === currentOpenApiUserId),
    loading: requestIdentity.loading || spaceLoading || !spaceInitialized || state.loading,
    error: requestIdentity.error ?? spaceError ?? state.error,
    retry: load,
    ...navigation,
    getHealthCheckAvailability: (bot: BotDomain) =>
      botHealthCheckService.resolveAvailability(bot, currentOpenApiUserId),
    openHealthCheck,
    openLogs: (bot: BotDomain) => {
      const params = new URLSearchParams({ bot_id: bot.id, bot_name: bot.name });
      if (bot.ownerId) params.set('owner_id', bot.ownerId);
      history.push(`/bot-workshop/logs?${params.toString()}`);
    },
    creating: createFlow.creating,
    createAuthorization: createFlow.authorization,
    agentCodingTemplates: agentCodingTemplates.templates,
    agentCodingTemplatesLoading: agentCodingTemplates.loading,
    agentCodingTemplatesError: agentCodingTemplates.error,
    retryAgentCodingTemplates: agentCodingTemplates.retry,
    createSpaces: state.createScenario
      ? botWorkshopService.getCreateSpaces(
          state.createScenario,
          spaceId || undefined,
          currentOpenApiUserId,
          currentSpace
            ? {
                id: String(currentSpace.spaceId),
                name: currentSpace.spaceName,
                ownership: currentSpace.spaceType === 'PERSONAL' ? 'personal' : 'team',
                canCreate: true,
              }
            : undefined,
        )
      : [],
    openCreateLocal: () => state.setCreateScenario('local'),
    openCreateCloud: () => state.setCreateScenario('cloud'),
    closeCreate: () => {
      createFlow.cancelAuthorization();
      state.setCreateScenario(undefined);
    },
    submitCreate: createFlow.submitCreate,
    runAction,
    ...locks,
    ...accessControl,
    logActionFor: (bot: BotDomain) =>
      getBotActionAvailability(bot, { apiReady: { logs: true } }).find((action) => action.action === 'logs'),
    inventoryActionFor: getInventoryActionAvailability,
  };
}
