import { getCapabilities } from '@/capabilities';
import { useBotHealthCheck } from '@/hooks/useBotHealthCheck';
import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { botHealthCheckService } from '@/services/botHealthCheck';
import type { BotCreateInput, BotDomain } from '@/services/botWorkshop';
import { botWorkshopService, getBotActionAvailability } from '@/services/botWorkshop';
import { botManagementService } from '@/services/botWorkshop/botManagementService';
import { resolveBotRuntimeStage } from '@/services/botWorkshop/botRuntimeStage';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { useBotCreateAuthorization } from './useBotCreateAuthorization';
import { useBotWorkshopAccess } from './useBotWorkshopAccess';

export function useBotWorkshop() {
  const state = useBotWorkshopStore();
  const healthCheck = useBotHealthCheck();
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
  const currentUser = getCapabilities().getCurrentOpenApiUserId({ activeIdentityId });
  const currentOpenApiUserId = currentUser.status === 'available' ? currentUser.value?.trim() || undefined : undefined;
  const currentUserIdRef = useRef(currentOpenApiUserId);
  currentUserIdRef.current = currentOpenApiUserId;
  const load = useCallback(async () => {
    if (!requestIdentity.ready || !spaceInitialized || !spaceId) return;
    const sequence = ++loadSequence.current;
    state.setResult({
      items: state.items,
      total: state.total,
      hasMore: state.hasMore,
      loading: true,
      error: undefined,
    });
    try {
      const result = await botWorkshopService.list({
        spaceId,
        keyword,
        engine,
        deployment,
        serviceMode,
        page,
        pageSize,
      });
      if (sequence !== loadSequence.current) return;
      const items = await botManagementService.loadServiceLocks(result.items, currentUserIdRef.current);
      if (sequence !== loadSequence.current) return;
      state.setResult({
        items,
        total: result.total,
        hasMore: result.hasMore,
        loading: false,
        error: undefined,
      });
    } catch (error) {
      if (sequence !== loadSequence.current) return;
      const message = error instanceof Error ? error.message : 'Bot 列表加载失败';
      state.setResult({ items: [], total: undefined, hasMore: undefined, loading: false, error: message });
    }
  }, [deployment, engine, keyword, page, pageSize, requestIdentity.ready, serviceMode, spaceId, spaceInitialized]);
  useEffect(() => {
    void load();
  }, [load]);
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
        toast.error('缺少当前用户身份，无法发起健康检查');
        return;
      }
      healthCheck.openHealthCheck(target);
    },
    [currentOpenApiUserId, healthCheck.openHealthCheck],
  );
  const runAction = useCallback(
    async (action: 'delete' | 'restart' | 'engine_restart' | 'upgrade', bot: BotDomain) => {
      try {
        if (action === 'delete') await botWorkshopService.remove(bot);
        if (action === 'restart') await botWorkshopService.restart(bot);
        if (action === 'engine_restart') await botWorkshopService.restartEngine(bot.id);
        if (action === 'upgrade') await botWorkshopService.enableService(bot.id);
        toast.success(action === 'delete' ? 'Bot 已删除' : action === 'upgrade' ? '已开启服务化' : '重启请求已提交');
        await load();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '操作失败');
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
  const openDetail = useCallback((botOrId: BotDomain | string, type: 'view' | 'edit' = 'view') => {
    const id = typeof botOrId === 'string' ? botOrId : botOrId.id;
    const bot = typeof botOrId === 'string' ? undefined : botOrId;
    const params = new URLSearchParams({ type, id });
    if (bot) params.set('runtime_stage', resolveBotRuntimeStage(bot.lifecycle));
    history.push(`/bot-workshop/detail?${params.toString()}`);
  }, []);

  return {
    ...state,
    spaceId,
    loading: requestIdentity.loading || spaceLoading || !spaceInitialized || state.loading,
    error: requestIdentity.error ?? spaceError ?? state.error,
    retry: load,
    openDetail,
    openConversation: () => history.push('/workspace?tab=chat'),
    healthCheck,
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
  };
}
