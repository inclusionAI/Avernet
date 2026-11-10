import { getCapabilities } from '@/capabilities';
import { useBotWorkshopActions } from './useBotWorkshopActions';
import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useBotWorkshopNavigation } from '@/hooks/useBotWorkshopNavigation';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { useVisibleInterval } from '@/hooks/useVisibleInterval';
import { botHealthCheckService } from '@/services/botHealthCheck';
import type { BotCreateInput, BotDomain } from '@/services/botWorkshop';
import {
  botWorkshopService,
  canEnterBotDetail,
  getBotActionAvailability,
  getInventoryActionAvailability,
} from '@/services/botWorkshop';
import { botManagementService } from '@/services/botWorkshop/botManagementService';
import {
  needsFastInventorySync,
  reconcileRestartPins,
  resolveExpiredRestartPin,
} from '@/services/botWorkshop/botRestartTracking';
import { canOpenDraftOrPrestableServiceCodingBot } from '@/services/botWorkshop/botPolicy';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { useBotWorkshopAccess } from './useBotWorkshopAccess';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { useBotWorkshopCopyHost } from './useBotWorkshopCopyHost';
import { useBotWorkshopCreateFlow } from './useBotWorkshopCreateFlow';
import { useBotWorkshopHealthCheck } from './useBotWorkshopHealthCheck';
import { useBotWorkshopLocks } from './useBotWorkshopLocks';

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
  const resolvedPins = useRef(new Set<string>());
  const { keyword, engine, deployment, serviceMode, page, pageSize } = state;
  const navigation = useBotWorkshopNavigation();
  const currentUser = getCapabilities().getCurrentOpenApiUserId({ activeIdentityId });
  const currentOpenApiUserId = currentUser.status === 'available' ? currentUser.value?.trim() || undefined : undefined;
  const canCreateLocal = currentSpace?.spaceType === 'PERSONAL';
  const localCreateRestriction = '桌面 Bot 仅支持在个人空间创建和运维';
  const localCreateDisabledReason = canCreateLocal ? undefined : localCreateRestriction;
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
        // 重启 pin 在每次库存同步时合并：滞后 ACTIVE 不打断「重启中」，过渡态后跟随服务端。
        result.items = reconcileRestartPins(useBotWorkshopStore.getState().items, result.items);
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
  const copyHost = useBotWorkshopCopyHost({ load, currentOpenApiUserId });
  const refreshVisibleList = useCallback(() => void load({ silent: true }), [load]);
  useVisibleInterval(
    refreshVisibleList,
    state.items.some((bot) => needsFastInventorySync(bot)) ? 5_000 : 30_000,
    requestIdentity.ready && spaceInitialized && Boolean(spaceId) && !createFlow.creating && !createFlow.authorization,
  );
  // Pin 超时后的详情接口兜底：按重启响应记录的 operation_id 判定终态，避免本地卡死。
  useEffect(() => {
    const expired = state.items.filter((bot) => bot.pendingRestart && Date.now() > bot.pendingRestart.deadlineAt);
    if (!expired.length) return;
    void (async () => {
      for (const bot of expired) {
        const pin = bot.pendingRestart;
        if (!pin) continue;
        const key = `${bot.ownerId}:${bot.id}:${pin.operationId}:${pin.extensions ?? 0}`;
        if (resolvedPins.current.has(key)) continue;
        resolvedPins.current.add(key);
        const resolution = await resolveExpiredRestartPin(bot);
        if (resolution.status === 'SUCCEEDED') {
          toast.success('Bot 重启完成');
        } else if (resolution.status === 'FAILED') {
          toast.error(resolution.message || '本次重启失败，请查看 Bot 日志');
        } else if (resolution.status === 'RUNNING' && !resolution.extended) {
          toast.info('重启仍在后台执行，状态可能暂时滞后');
        }
        if (resolution.status !== 'RUNNING' || !resolution.extended) void load({ silent: true });
      }
    })();
  }, [state.items, load]);

  const accessControl = useBotWorkshopAccess(currentOpenApiUserId, load);
  const openHealthCheck = useBotWorkshopHealthCheck(currentOpenApiUserId);
  const runAction = useBotWorkshopActions(load, currentOpenApiUserId);
  const locks = useBotWorkshopLocks(load);
  const canEnterDetail = useCallback(
    (bot: BotDomain) => canEnterBotDetail(bot, currentOpenApiUserId),
    [currentOpenApiUserId],
  );
  return {
    ...state,
    spaceId,
    currentSpaceKind: currentSpace?.spaceType === 'TEAM' ? 'team' : 'personal',
    canOpenConversation: (bot: BotDomain) =>
      canOpenDraftOrPrestableServiceCodingBot(bot, currentOpenApiUserId) ||
      Boolean(currentOpenApiUserId && bot.actions.includes('chat') && !bot.disabledActions?.chat),
    canEnterDetail,
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
    openTaskInstances: (bot: BotDomain) => {
      const params = new URLSearchParams({ bot_id: bot.id, bot_name: bot.name });
      if (bot.ownerId) params.set('owner_id', bot.ownerId);
      history.push(`/bot-workshop/scheduled-tasks?${params.toString()}`);
    },
    // 可见用户可进入；管理操作另行校验 ADMIN/OWNER 与编辑锁。
    canOpenTaskInstances: (bot: BotDomain) =>
      Boolean(currentOpenApiUserId && (bot.actions.includes('view') || bot.ownerId === currentOpenApiUserId || bot.actions.includes('edit'))),
    containerUserId: requestIdentity.userId,
    canViewContainers: (bot: BotDomain) => Boolean(bot.runtime.engine !== 'teclaw' && currentOpenApiUserId && (bot.ownerId === currentOpenApiUserId || bot.actions.includes('edit'))),
    canRestartContainer: (bot: BotDomain) => Boolean(bot.runtime.engine !== 'teclaw' && currentOpenApiUserId && bot.ownerId === currentOpenApiUserId),
    creating: createFlow.creating,
    createAuthorization: createFlow.authorization,
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
    localCreateDisabledReason,
    openCreateLocal: () => {
      if (!canCreateLocal) {
        toast.error(localCreateRestriction);
        return;
      }
      Tracert.click(Points.botWorkshop.createLocal);
      state.setCreateScenario('local');
    },
    openCreateCloud: () => {
      Tracert.click(Points.botWorkshop.createCloud);
      state.setCreateScenario('cloud');
    },
    closeCreate: () => {
      createFlow.cancelAuthorization();
      state.setCreateScenario(undefined);
    },
    submitCreate: async (input: BotCreateInput) => {
      Tracert.click(Points.botWorkshop.createSubmit);
      if (input.scenario === 'local' && !canCreateLocal) {
        const error = new Error(localCreateRestriction);
        toast.error(error.message);
        throw error;
      }
      await createFlow.submitCreate(input);
    },
    ...copyHost,
    runAction,
    ...locks,
    ...accessControl,
    logActionFor: (bot: BotDomain) =>
      getBotActionAvailability(bot, { apiReady: { logs: true } }).find((action) => action.action === 'logs'),
    inventoryActionFor: (bot: BotDomain, action: Parameters<typeof getInventoryActionAvailability>[1]) => {
      if (action === 'view' && !canEnterDetail(bot)) {
        return { action: 'view' as const, visible: true, enabled: true };
      }
      return getInventoryActionAvailability(bot, action);
    },
  };
}
