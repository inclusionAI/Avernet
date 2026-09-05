import type { SessionView } from '@/domain/collaboration';
import type { DomainError, DomainResult } from '@/services/workspace/identityService';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useMemo } from 'react';
import { toast } from 'sonner';
import { useSessionMutations } from './useSessionLeave';
import { useSessionMap } from './useSessionMap';
import type { UseGroupSessionsResult } from './useGroupSessions.types';
import { useSessionMemberSync } from './useSessionMemberSync';

export type { UseGroupSessionsResult } from './useGroupSessions.types';
function notifyError(err: DomainError): void {
  toast.error(err.friendlyMessage);
}

function errOf(res: { ok: false; error: DomainError }): DomainError {
  return res.error;
}

/**
 * useGroupSessions 编排一个协作群下的会话生命周期：加载、自动选中、
 * 创建/重命名/删除、收藏同步、tab/search/filter 过滤。
 *
 * 层级约束：Hook 调用 Service（groupService / sessionService），
 * 不直接读 DTO 字段；session 列表数据由本 Hook 以 groupId 键控的局部 state 持有
 * （支持多个群同时展开展示各自会话），
 * Store 只承载 selection/tab/search/收藏标记。
 *
 * @param groupId 当前选中群（chat pane 的数据面，选中群切换必重拉）
 * @param expandedGroupIds 侧栏当前展开的群 id（未缓存的展开群会静默加载一次，之后复用缓存）
 */
export function useGroupSessions(groupId: string | null, expandedGroupIds: string[] = []): UseGroupSessionsResult {
  const selectedSessionId = useWorkspaceStore((s) => s.selectedSessionId);
  const sessionSearchText = useWorkspaceStore((s) => s.sessionSearchText);
  const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);

  // 会话集合与加载/缓存机制委托内部子 Hook（本 Hook 聚焦编排与 CRUD）。
  const {
    rawByGroupId,
    isLoading,
    applyMapUpdate,
    updateGroupSessions,
    reloadGroup,
    hasMoreByGroupId,
    isLoadingMoreByGroupId,
    totalByGroupId,
    loadMoreSessions,
    errorByGroupId,
    loadMoreErrorByGroupId,
  } = useSessionMap(groupId, expandedGroupIds, activeIdentityId);

  // 会话成员详情补齐与 mode 更新(从本 Hook 拆出以控体积,详见 useSessionMemberSync)。
  // 注意：useSessionMemberSync 需要选中会话的 participants 长度作为依赖，
  // 但 selectedSession 在下方推导，此处先占位，推导后立即调用。
  const selectSession = useWorkspaceStore((s) => s.selectSession);
  const setSessionSearchText = useWorkspaceStore((s) => s.setSessionSearchText);

  const { deleteSession, leaveSession } = useSessionMutations(applyMapUpdate, selectSession);

  const EMPTY_SESSIONS: SessionView[] = useMemo(() => [], []);

  // 当前选中群的会话列表（未过滤）。
  const sessionViews = useMemo(
    () => (groupId ? rawByGroupId[groupId] ?? EMPTY_SESSIONS : EMPTY_SESSIONS),
    [rawByGroupId, groupId, EMPTY_SESSIONS],
  );

  // 自动选中首条会话：当 groupId 存在、列表非空、且未选中任何会话时。
  useEffect(() => {
    if (!groupId || sessionViews.length === 0) return;
    if (useWorkspaceStore.getState().selectedSessionId) return;
    const first = sessionViews[0];
    if (first) selectSession(first.sessionId);
  }, [groupId, sessionViews, selectSession]);

  // 收藏状态来源于后端 sessions 列表返回的 collected 字段（映射为 SessionView.favorite）。
  // 收藏过滤已下沉到 GroupItem（每群独立 tab），这里仅暴露收藏 ID 给组件层。
  const favoriteSessionIds = useMemo(
    () => [
      ...new Set(
        Object.values(rawByGroupId)
          .flat()
          .filter((s) => s.favorite)
          .map((s) => s.sessionId),
      ),
    ],
    [rawByGroupId],
  );

  // 经 search 过滤后的各群会话（tab/收藏过滤由 GroupItem 按群独立处理）。
  const sessionsByGroupId = useMemo(() => {
    const out: Record<string, SessionView[]> = {};
    for (const [gid, list] of Object.entries(rawByGroupId)) {
      out[gid] = sessionService.getVisibleSessions(list, {
        tab: 'all',
        search: sessionSearchText,
        favorites: favoriteSessionIds,
      });
    }
    return out;
  }, [rawByGroupId, sessionSearchText, favoriteSessionIds]);

  const sessions = groupId ? sessionsByGroupId[groupId] ?? EMPTY_SESSIONS : EMPTY_SESSIONS;

  const selectedSession = useMemo(
    () => sessionViews.find((s) => s.sessionId === selectedSessionId) ?? null,
    [sessionViews, selectedSessionId],
  );

  const { updateMemberMode, applySessionUpdate } = useSessionMemberSync(
    selectedSessionId,
    applyMapUpdate,
    selectedSession?.participants.length ?? 0,
  );

  const createSessionIn = useCallback(
    async (gid: string, title?: string, contextQuery?: string): Promise<SessionView | null> => {
      const res: DomainResult<SessionView> = await sessionService.createNewSession(gid, title, contextQuery);
      if (!res.ok) {
        notifyError(errOf(res));
        return null;
      }
      const created: SessionView = res.data;
      updateGroupSessions(gid, (list) => [created, ...list.filter((s) => s.sessionId !== created.sessionId)]);
      const store = useWorkspaceStore.getState();
      // 跨群新建：先切群再选会话（selectGroup 会重置 selectedSessionId，顺序不能换）。
      if (store.selectedGroupId !== gid) store.selectGroup(gid);
      useWorkspaceStore.getState().selectSession(created.sessionId);
      toast.success('会话已创建');
      return created;
    },
    [updateGroupSessions],
  );

  const createSession = useCallback(
    (title?: string, contextQuery?: string): Promise<SessionView | null> => {
      if (!groupId) return Promise.resolve(null);
      return createSessionIn(groupId, title, contextQuery);
    },
    [groupId, createSessionIn],
  );

  const openSession = useCallback((gid: string, sessionId: string) => {
    const store = useWorkspaceStore.getState();
    if (store.selectedGroupId !== gid) store.selectGroup(gid);
    useWorkspaceStore.getState().selectSession(sessionId);
    // 重复点击同一会话也递增 nonce,驱动 useGroupChat 重新拉取历史消息。
    useWorkspaceStore.getState().bumpHistoryRefresh();
  }, []);

  const renameSession = useCallback(
    async (sessionId: string, title: string): Promise<boolean> => {
      const res: DomainResult<null> = await sessionService.renameSession(sessionId, title);
      if (!res.ok) {
        notifyError(errOf(res));
        return false;
      }
      applyMapUpdate((cur) => sessionService.renameInMap(cur, sessionId, title));
      toast.success('会话已重命名');
      return true;
    },
    [applyMapUpdate],
  );

  const toggleFavorite = useCallback(
    async (sessionId: string): Promise<void> => {
      const identityId = useWorkspaceStore.getState().activeIdentityId;
      if (!identityId) return;
      // 从已加载的会话列表中查找当前收藏状态。
      const allSessions = Object.values(rawByGroupId).flat();
      const current = allSessions.find((s) => s.sessionId === sessionId);
      const shouldCollect = current ? !current.favorite : true;
      const res = await sessionService.setFavorite(identityId, sessionId, shouldCollect);
      if (!res.ok) {
        notifyError(errOf(res));
        return;
      }
      // 就地更新会话映射中的 favorite 字段，驱动星标即时刷新。
      applyMapUpdate((cur) => {
        const next: Record<string, SessionView[]> = {};
        for (const [gid, list] of Object.entries(cur)) {
          next[gid] = list.map((s) => (s.sessionId === sessionId ? { ...s, favorite: res.data } : s));
        }
        return next;
      });
    },
    [applyMapUpdate, rawByGroupId],
  );

  const reloadSessions = useCallback(async (): Promise<void> => {
    if (!groupId) return;
    await reloadGroup(groupId);
  }, [groupId, reloadGroup]);

  return {
    sessions,
    sessionsByGroupId,
    hasMoreSessionsByGroupId: hasMoreByGroupId,
    isLoadingMoreSessionsByGroupId: isLoadingMoreByGroupId,
    totalSessionsByGroupId: totalByGroupId,
    errorByGroupId,
    loadMoreErrorByGroupId,
    loadMoreSessions,
    isSessionsLoading: isLoading,
    selectedSessionId,
    selectedSession,
    favoriteSessionIds,
    sessionSearchText,
    setSessionSearchText,
    selectSession,
    openSession,
    createSession,
    createSessionIn,
    renameSession,
    deleteSession,
    leaveSession,
    toggleFavorite,
    updateMemberMode,
    applySessionUpdate,
    reloadSessions,
    reloadGroup,
  };
}
