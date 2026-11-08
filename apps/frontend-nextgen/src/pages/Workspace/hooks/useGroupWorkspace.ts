import type { GroupView, IdentityView } from '@/domain/collaboration';
import { loadScopedGroup } from '@/services/workspace/collaborationScopeService';
import { groupService, type PolicyResult } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { shouldMuteNonAuthedToast } from '@/utils/loginToastGate';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import type { UseGroupWorkspaceResult } from './useGroupWorkspace.types';
import { useGroupWorkspaceMutations } from './useGroupWorkspaceMutations';
import { useSelectedGroupDetail } from './useSelectedGroupDetail';

const SEARCH_DEBOUNCE_MS = 300;

export type { UseGroupWorkspaceResult } from './useGroupWorkspace.types';
/**
 * useGroupWorkspace 负责协作群列表的编排：拉取、防抖搜索、身份切换重载、
 * 选中群详情兜底、以及解散群的政策校验与 Toast 反馈。
 *
 * 层级约束：Hook 调用 Service（groupService），Service 写 Store / 调 Controller；
 * Hook 不直接读 DTO 字段，所有映射由 Service 拥有。
 */
export function useGroupWorkspace(pinnedGroup?: GroupView): UseGroupWorkspaceResult {
  const identities = useWorkspaceStore((s) => s.identities);
  const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const activeIdentity = useMemo(
    () => identities.find((i) => i.id === activeIdentityId) ?? null,
    [identities, activeIdentityId],
  );
  const selectedGroupId = useWorkspaceStore((s) => s.selectedGroupId);
  const groupSearchText = useWorkspaceStore((s) => s.groupSearchText);
  const kindFilter = useWorkspaceStore((s) => s.groupKindFilter);
  const sortMode = useWorkspaceStore((s) => s.groupSortMode);
  const membership = useWorkspaceStore((s) => s.membership);
  const expandedGroupIds = useWorkspaceStore((s) => s.expandedGroupIds);
  const isGroupsLoading = useWorkspaceStore((s) => s.isGroupsLoading);
  const [groupsError, setGroupsError] = useState<string | null>(null);

  const setGroupSearchText = useWorkspaceStore((s) => s.setGroupSearchText);
  const setKindFilter = useWorkspaceStore((s) => s.setGroupKindFilter);
  const setSortMode = useWorkspaceStore((s) => s.setGroupSortMode);
  const rawSetMembership = useWorkspaceStore((s) => s.setMembership);
  const toggleGroupExpanded = useWorkspaceStore((s) => s.toggleGroupExpanded);

  // 用户手动切换角色筛选时先清空当前选中群/会话，避免 useSelectedGroupDetail
  // 的自动纠正 effect 把 membership 强制改回与旧选中群匹配的值。
  const setMembership = useCallback(
    (m: 'direct' | 'session_only') => {
      const store = useWorkspaceStore.getState();
      if (store.membership !== m) store.selectGroup(null);
      rawSetMembership(m);
    },
    [rawSetMembership],
  );

  const [sessionGroups, setSessionGroups] = useState<GroupView[]>(pinnedGroup ? [pinnedGroup] : []);
  // 记录 sessionGroups 是为哪个身份加载完成的(loadGroups 成功回填时写入):
  // 供 useSelectedGroupDetail 判定“当前可见列表是否属于当前身份且已加载完成”,
  // 堵住新开页全新挂载时按初始空列表/上一身份陈旧列表误切 membership 的竞态。
  const [loadedListIdentity, setLoadedListIdentity] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSearchRef = useRef('');
  // 成员补拉缓存 / 选中群详情重载 / 解散群：下沉至 useGroupWorkspaceMutations（控本体体积）。
  const { seedFromCache, enrichParticipants, reloadSelectedGroup, dissolveGroup } = useGroupWorkspaceMutations({
    sessionGroups,
    setSessionGroups,
    activeIdentityId,
    selectedGroupId,
  });

  const resolveIdentity = useCallback(
    (identityId: string | null): IdentityView | null => {
      if (!identityId) return null;
      return (
        identities.find((i) => i.id === identityId) ?? {
          id: identityId,
          kind: 'bot' as const,
          displayName: '',
          online: false,
        }
      );
    },
    [identities],
  );

  // 群行头像堆成员补拉（fire-and-forget）：对列表未回传 participants 的群按详情逐个拉取，
  // 逐群合并进 sessionGroups（不整替整行，避免覆盖列表的 membership 等视图字段）。
  // 失败也写入缓存占位（空名单），防止逐字防抖搜索对同一失败群反复请求；页面重挂载即重置。
  const loadGroups = useCallback(
    async (identityId: string | null, q?: string, requestedMembership = membership) => {
      const identity = resolveIdentity(identityId);
      if (!identity) {
        setGroupsError(null);
        return;
      }
      useWorkspaceStore.getState().setIsGroupsLoading(true);
      setGroupsError(null);
      const isCurrentList = () => {
        const current = useWorkspaceStore.getState();
        return current.activeIdentityId === identityId && current.membership === requestedMembership
          && lastSearchRef.current === (q || '');
      };
      try {
        const detail = pinnedGroup ? await loadScopedGroup(pinnedGroup.groupId, identity.id) : null;
        const res = detail
          ? detail.ok
            ? { ok: true as const, data: [detail.data] }
            : detail
          : await groupService.loadGroups(identity, { q, membership: requestedMembership });
        if (!isCurrentList()) return;
        if (res.ok) {
          // 先用缓存过的成员名单给新列表播种（列表接口不回传 participants），再对仍未有
          // 成员的群异步补拉详情——防抖搜索/身份切换的列次同一群不再重复请求。
          const seeded = seedFromCache(res.data);
          setSessionGroups(seeded);
          // 仅当这次列表仍属于当前活动身份时才记成“已为该身份加载完成”,
          // 避免身份切换后旧身份的残留响应把 loadedListIdentity 写成旧值。
          if (useWorkspaceStore.getState().activeIdentityId === identityId) {
            setLoadedListIdentity(identityId);
          }
          void enrichParticipants(seeded, identity.kind === 'bot' ? identity.id : undefined);
        } else {
          if (pinnedGroup) setSessionGroups([]);
          setGroupsError(res.error.friendlyMessage);
          // 未登录（oauth-provider + 非 authenticated）静默：会话失效后「加载协作群失败」
          // 等业务 toast 噪音统一由 ExternalLoginPromptModal 承担（见 loginToastGate）。
          if (!shouldMuteNonAuthedToast()) toast.error(res.error.friendlyMessage);
        }
      } finally {
        if (isCurrentList()) useWorkspaceStore.getState().setIsGroupsLoading(false);
      }
    },
    [resolveIdentity, membership, pinnedGroup, seedFromCache, enrichParticipants],
  );

  // 身份变化或成员视角切换 → 重载列表并清空上轮搜索基线，避免防抖 effect 误判为新搜索。
  useEffect(() => {
    if (!pinnedGroup) void loadGroups(activeIdentityId);
    lastSearchRef.current = '';
  }, [activeIdentityId, loadGroups, pinnedGroup]);

  // 搜索防抖：仅在文本真正变化时才触发 300ms 后的重载（membership 经 loadGroups 闭包传递）。
  useEffect(() => {
    if (pinnedGroup || groupSearchText === lastSearchRef.current) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      lastSearchRef.current = groupSearchText;
      void loadGroups(activeIdentityId, groupSearchText || undefined);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [groupSearchText, activeIdentityId, loadGroups, pinnedGroup]);

  const groups = useMemo(
    () =>
      pinnedGroup
        ? sessionGroups.filter((group) => group.groupId === pinnedGroup.groupId)
        : groupService.getVisibleGroups(sessionGroups, { search: '', kind: kindFilter, sort: sortMode }),
    [sessionGroups, kindFilter, sortMode, pinnedGroup],
  );

  const selectedGroup = useMemo(
    () => sessionGroups.find((g) => g.groupId === selectedGroupId) ?? null,
    [selectedGroupId, sessionGroups],
  );

  const listReadyForCurrentIdentity = activeIdentityId !== null && loadedListIdentity === activeIdentityId;
  // 深链选中群的成员视角兜底已拆到 useSelectedGroupDetail(控 Hook 体积)。
  useSelectedGroupDetail(
    pinnedGroup ? null : selectedGroupId,
    sessionGroups,
    isGroupsLoading,
    listReadyForCurrentIdentity,
  );

  const canManageGroup = useMemo<PolicyResult>(
    () => groupService.canManageGroup(selectedGroup, activeIdentityId),
    [selectedGroup, activeIdentityId],
  );
  const canDissolveGroup = useMemo<PolicyResult>(
    () => groupService.canDissolveGroup(selectedGroup, activeIdentityId),
    [selectedGroup, activeIdentityId],
  );

  // 选中群：仅写入选中态，不拉群详情。会话列表由 useSessionMap 调 /groups/{id}/sessions 单独拉取；
  // 群详情（participants/owner/driver）仅在打开管理面板（查看/编辑）时由 handleManageGroup →
  // reloadSelectedGroup 按需拉取，避免每次选中群都触发 GET /groups/{id} 详情请求。
  const onSelectGroup = useCallback(
    (groupId: string) => {
      if (!pinnedGroup || groupId === pinnedGroup.groupId) useWorkspaceStore.getState().selectGroup(groupId);
    },
    [pinnedGroup],
  );

  const refreshGroups = useCallback(async () => {
    // 创建成功可能刚切换筛选/清空搜索，同步搜索基线并取消旧防抖，避免使用上一帧的条件。
    const current = useWorkspaceStore.getState();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    lastSearchRef.current = current.groupSearchText;
    await loadGroups(activeIdentityId, current.groupSearchText || undefined, current.membership);
  }, [loadGroups, activeIdentityId]);

  const retryGroups = useCallback(async () => {
    await loadGroups(activeIdentityId, lastSearchRef.current || undefined);
  }, [activeIdentityId, loadGroups, pinnedGroup]);

  return {
    identities,
    activeIdentityId,
    activeIdentity,
    groups,
    isLoadingGroups: isGroupsLoading,
    groupsError,
    selectedGroupId,
    selectedGroup,
    groupSearchText,
    setGroupSearchText,
    kindFilter,
    setKindFilter,
    sortMode,
    setSortMode,
    membership,
    setMembership,
    expandedGroupIds,
    toggleGroupExpanded,
    onSelectGroup,
    refreshGroups,
    retryGroups,
    reloadSelectedGroup,
    dissolveGroup,
    canManageGroup,
    canDissolveGroup,
  };
}
