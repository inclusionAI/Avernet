import type { BotChatDetailSelection, BotChatFilters, BotChatRelationScope } from '@/domain/botChats';
import { resolveBotChatRelationScope } from '@/services/botWorkshop/botChatRelations';
import { botChatService } from '@/services/botWorkshop/botChatService';
import { identityService, isTestUserIdentity, resolveUserId } from '@/services/workspace';
import { useBotChatStore } from '@/stores/botChatStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useLocation } from '@umijs/max';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export function useBotChats() {
  const state = useBotChatStore();
  const location = useLocation();
  const [initializationError, setInitializationError] = useState<string>();

  const identities = useWorkspaceStore.getState().identities;
  const resolveActingUser = useCallback(async () => {
    const isRealHuman = (identity: (typeof identities)[number]) =>
      identity.kind === 'user' && identity.id !== 'me' && !isTestUserIdentity(identity.id);
    const existing = identities.find(isRealHuman);
    if (existing) return resolveUserId(existing.id);
    const loaded = await identityService.loadIdentities();
    if (!loaded.ok) throw new Error(loaded.error.friendlyMessage);
    const human = loaded.data.identities.find(isRealHuman);
    if (!human) throw new Error('无法确认当前用户身份，请重新登录后再试。');
    return resolveUserId(human.id);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const botId = params.get('bot_id')?.trim();
    if (!botId) {
      setInitializationError('缺少 bot_id，请从 Bot 工坊重新进入日志页。');
      useBotChatStore.getState().reset();
      return;
    }
    let disposed = false;
    setInitializationError(undefined);
    void (async () => {
      let userId: string;
      try {
        // user_id 恒取当前登录用户，禁止通过 URL 参数指定他人身份（否则可越权查看全局日志）。
        userId = await resolveActingUser();
      } catch (error) {
        if (disposed) return;
        const message = error instanceof Error ? error.message : '日志加载失败';
        setInitializationError(message);
        toast.error(message);
        return;
      }
      if (disposed) return;
      const context = {
        botId,
        botName: params.get('bot_name')?.trim() || botId,
        ownerId: params.get('owner_id')?.trim() || undefined,
        userId,
      };
      useBotChatStore.getState().openFor(context);
      await botChatService.list(context, useBotChatStore.getState().appliedFilters).catch(() => undefined);
    })();
    return () => {
      disposed = true;
      useBotChatStore.getState().reset();
    };
  }, [location.search, resolveActingUser]);

  const query = useCallback(async () => {
    const current = useBotChatStore.getState();
    if (!current.context) return;
    current.applyFilters();
    await botChatService.list(current.context, current.filters).catch(() => undefined);
  }, []);

  const resetFilters = useCallback(async () => {
    const current = useBotChatStore.getState();
    if (!current.context) return;
    current.clearFilters();
    await botChatService.list(current.context, useBotChatStore.getState().appliedFilters).catch(() => undefined);
  }, []);

  const changePage = useCallback(async (page: number) => {
    const current = useBotChatStore.getState();
    if (!current.context || !current.page) return;
    await botChatService.list(current.context, current.appliedFilters, page, current.page.limit).catch(() => undefined);
  }, []);

  const changePageSize = useCallback(async (limit: number) => {
    const current = useBotChatStore.getState();
    if (!current.context) return;
    await botChatService.list(current.context, current.appliedFilters, 1, limit).catch(() => undefined);
  }, []);

  const openDetail = useCallback(async (selection: BotChatDetailSelection | string) => {
    const current = useBotChatStore.getState();
    const context = current.context;
    if (!context) return;
    const isListSelection = typeof selection !== 'string';
    const traceId = isListSelection ? selection.traceId : selection;
    const selectedSessionId = isListSelection ? selection.sessionId : undefined;
    const selectedBotId = isListSelection ? selection.botId : undefined;
    const relatedItem =
      current.related?.items.find((item) => item.id === traceId) ??
      current.page?.items.find((item) => item.id === traceId);
    const groupId = current.relationScope === 'group' ? current.detail?.groupId : undefined;
    const sourceBotId = selectedBotId ?? relatedItem?.botId;
    // The related list already came from the session query. Clicking a related
    // trace should only load that trace's detail, not query the same session again.
    const sessionId = selectedSessionId;
    const preserveRelated = !isListSelection && !groupId;
    const detailRequest = sessionId
      ? botChatService.detail(context, traceId, groupId, sourceBotId, sessionId, preserveRelated)
      : sourceBotId
      ? preserveRelated
        ? botChatService.detail(context, traceId, groupId, sourceBotId, undefined, true)
        : botChatService.detail(context, traceId, groupId, sourceBotId)
      : preserveRelated
      ? botChatService.detail(context, traceId, groupId, undefined, undefined, true)
      : botChatService.detail(context, traceId, groupId);
    const detail = await detailRequest.catch(() => undefined);
    if (detail) {
      if (groupId) return;
      if (!isListSelection) return;
      const scope = resolveBotChatRelationScope(detail, useBotChatStore.getState().relationScope);
      await botChatService.related(context, detail, scope).catch(() => undefined);
    }
  }, []);

  const loadRelated = useCallback(async (scope: BotChatRelationScope) => {
    const current = useBotChatStore.getState();
    if (!current.context || !current.detail) return;
    const effectiveScope = resolveBotChatRelationScope(current.detail, scope);
    await botChatService.related(current.context, current.detail, effectiveScope).catch(() => undefined);
  }, []);

  const loadMoreRelated = useCallback(async () => {
    const current = useBotChatStore.getState();
    if (!current.context || !current.detail || !current.related?.hasMore || current.relatedLoading) return;
    await botChatService
      .related(current.context, current.detail, current.relationScope, current.related.page + 1, true)
      .catch(() => undefined);
  }, []);

  return {
    ...state,
    initializationError,
    query,
    resetFilters,
    changePage,
    changePageSize,
    openDetail,
    loadRelated,
    loadMoreRelated,
    backToWorkshop: () => {
      useBotChatStore.getState().reset();
      history.push('/bot-workshop');
    },
    backToList: state.backToList,
    setFilter: (key: keyof BotChatFilters, value: string) => state.setFilter(key, value),
  };
}
