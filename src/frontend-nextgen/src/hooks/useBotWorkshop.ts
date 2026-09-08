import { getCapabilities } from '@/capabilities';
import type { BotManagementVerb } from '@/domain/botWorkshop';
import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useBotWorkshopNavigation } from '@/hooks/useBotWorkshopNavigation';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { useVisibleInterval } from '@/hooks/useVisibleInterval';
import { botHealthCheckService } from '@/services/botHealthCheck';
import type { BotCreateInput, BotDomain } from '@/services/botWorkshop';
import { botWorkshopService, getBotActionAvailability, getInventoryActionAvailability } from '@/services/botWorkshop';
import { botManagementService } from '@/services/botWorkshop/botManagementService';
import { resolveBotRuntimeStage } from '@/services/botWorkshop/botRuntimeStage';
import { getBotManagementErrorMessage } from '@/services/botWorkshop/botWorkshopErrorPolicy';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { useAgentCodingTemplates } from './useAgentCodingTemplates';
import { useBotCreateAuthorization } from './useBotCreateAuthorization';
import { useBotWorkshopAccess } from './useBotWorkshopAccess';

/** 动作名即路由键：新增动词时补一行 runner/toast，漏补会在编译期报错而非静默无操作。 */
const RUN_ACTION_RUNNER: Record<BotManagementVerb, (bot: BotDomain) => Promise<void>> = {
  delete: (bot) => botWorkshopService.remove(bot),
  restart: (bot) => botWorkshopService.restart(bot),
  engine_restart: (bot) => botWorkshopService.restartEngine(bot.id),
  upgrade: (bot) => botWorkshopService.enableService(bot.id),
  restart_publish: (bot) => botWorkshopService.restartPublish(bot),
};

const RUN_ACTION_SUCCESS_TOAST: Record<BotManagementVerb, string> = {
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
  const [creating, setCreating] = useState(false);
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
  const refreshVisibleList = useCallback(() => void load({ silent: true }), [load]);
  useVisibleInterval(refreshVisibleList, 30_000, requestIdentity.ready && spaceInitialized && Boolean(spaceId));
  const handleCreated = useCallback(
    async (bot: BotDomain) => {
      state.setCreateScenario(undefined);
      toast.success(`${bot.name} 已创建`);
      await load();
    },
    [load, state.setCreateScenario],
  );
  const createAuthorization = useBotCreateAuthorization(handleCreated);
  const submitCreate = useCallback(
    async (input: BotCreateInput) => {
      setCreating(true);
      try {
        const result = await botWorkshopService.create(input);
        if (result.type === 'authorization_required') {
          createAuthorization.beginAuthorization(result);
          return;
        }
        if (result.type === 'created_with_pending_after_create') {
          const actions = result.afterCreateFailures.map((failure) => failure.key).join('、');
          toast.warning(`Bot 已创建，但后续配置未全部完成${actions ? `：${actions}` : ''}`);
        }
        await handleCreated(result.bot);
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Bot 创建失败';
        toast.error(message);
        throw error;
      } finally {
        setCreating(false);
      }
    },
    [createAuthorization.beginAuthorization, handleCreated],
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
        await RUN_ACTION_RUNNER[action](bot);
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
  const claimLock = useCallback(
    async (bot: BotDomain) => {
      const toastId = toast.loading('正在抢占编辑锁...');
      try {
        await botManagementService.stealEditLock(bot);
        toast.success('已成功获取编辑锁', { id: toastId });
        await load();
        const params = new URLSearchParams({
          type: 'edit',
          id: bot.id,
          runtime_stage: resolveBotRuntimeStage(bot.lifecycle),
        });
        history.push(`/bot-workshop/detail?${params.toString()}`);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '抢占编辑锁失败', { id: toastId });
        throw error;
      }
    },
    [load],
  );
  return {
    ...state,
    spaceId,
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
    creating,
    createAuthorization: createAuthorization.authorization,
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
      createAuthorization.cancelAuthorization();
      state.setCreateScenario(undefined);
    },
    submitCreate,
    runAction,
    claimLock,
    ...accessControl,
    logActionFor: (bot: BotDomain) =>
      getBotActionAvailability(bot, { apiReady: { logs: true } }).find((action) => action.action === 'logs'),
    inventoryActionFor: getInventoryActionAvailability,
  };
}
