import type { GroupKind, GroupView, IdentityView } from '@/domain/collaboration';
import { groupService, type PolicyResult } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { useSelectedGroupDetail } from './useSelectedGroupDetail';

const SEARCH_DEBOUNCE_MS = 300;

export interface UseGroupWorkspaceResult {
  identities: IdentityView[];
  activeIdentityId: string | null;
  activeIdentity: IdentityView | null;
  groups: GroupView[];
  isLoadingGroups: boolean;
  selectedGroupId: string | null;
  selectedGroup: GroupView | null;
  groupSearchText: string;
  setGroupSearchText: (v: string) => void;
  kindFilter: 'all' | GroupKind;
  setKindFilter: (k: 'all' | GroupKind) => void;
  sortMode: 'lastActivity' | 'createdAt';
  setSortMode: (m: 'lastActivity' | 'createdAt') => void;
  membership: 'direct' | 'session_only';
  setMembership: (m: 'direct' | 'session_only') => void;
  expandedGroupIds: Record<string, true>;
  toggleGroupExpanded: (groupId: string) => void;
  onSelectGroup: (groupId: string) => Promise<void>;
  refreshGroups: () => Promise<void>;
  /** 重拉当前选中群详情并写回本地 groups（管理面板更新成员/公开性后就地刷新）。 */
  reloadSelectedGroup: () => Promise<void>;
  dissolveGroup: (groupId: string) => Promise<void>;
  /** Service 政策结果：当前选中群 + 当前身份是否有管理权限（policy 计算归 Hook，组件只消费）。 */
  canManageGroup: PolicyResult;
  /** Service 政策结果：当前选中群 + 当前身份是否可解散（policy 计算归 Hook，组件只消费）。 */
  canDissolveGroup: PolicyResult;
}

/**
 * useGroupWorkspace 负责协作群列表的编排：拉取、防抖搜索、身份切换重载、
 * 选中群详情兜底、以及解散群的政策校验与 Toast 反馈。
 *
 * 层级约束：Hook 调用 Service（groupService），Service 写 Store / 调 Controller；
 * Hook 不直接读 DTO 字段，所有映射由 Service 拥有。
 */
export function useGroupWorkspace(): UseGroupWorkspaceResult {
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

  const [sessionGroups, setSessionGroups] = useState<GroupView[]>([]);
  const [selectedGroupFallback, setSelectedGroupFallback] = useState<GroupView | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSearchRef = useRef('');

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

  const loadGroups = useCallback(
    async (identityId: string | null, q?: string) => {
      const identity = resolveIdentity(identityId);
      if (!identity) return;
      useWorkspaceStore.getState().setIsGroupsLoading(true);
      try {
        const res = await groupService.loadGroups(identity, { q, membership });
        if (res.ok) {
          setSessionGroups(res.data);
        } else {
          toast.error(res.error.friendlyMessage);
        }
      } finally {
        useWorkspaceStore.getState().setIsGroupsLoading(false);
      }
    },
    [resolveIdentity, membership],
  );

  // 身份变化或成员视角切换 → 重载列表并清空上轮搜索基线，避免防抖 effect 误判为新搜索。
  useEffect(() => {
    void loadGroups(activeIdentityId);
    lastSearchRef.current = '';
  }, [activeIdentityId, loadGroups]);

  // 搜索防抖：仅在文本真正变化时才触发 300ms 后的重载（membership 经 loadGroups 闭包传递）。
  useEffect(() => {
    if (groupSearchText === lastSearchRef.current) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      lastSearchRef.current = groupSearchText;
      void loadGroups(activeIdentityId, groupSearchText || undefined);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [groupSearchText, activeIdentityId, loadGroups]);

  const groups = useMemo(
    () => groupService.getVisibleGroups(sessionGroups, { search: '', kind: kindFilter, sort: sortMode }),
    [sessionGroups, kindFilter, sortMode],
  );

  const selectedGroup = useMemo(
    () =>
      sessionGroups.find((g) => g.groupId === selectedGroupId) ??
      (selectedGroupFallback?.groupId === selectedGroupId ? selectedGroupFallback : null),
    [selectedGroupFallback, selectedGroupId, sessionGroups],
  );

  // 选中群详情兜底与 membership 反推已拆到 useSelectedGroupDetail（控 Hook 体积）。
  useSelectedGroupDetail(
    selectedGroupId,
    activeIdentityId,
    selectedGroup,
    sessionGroups,
    isGroupsLoading,
    setSelectedGroupFallback,
    setSessionGroups,
  );

  const canManageGroup = useMemo<PolicyResult>(
    () => groupService.canManageGroup(selectedGroup, activeIdentityId),
    [selectedGroup, activeIdentityId],
  );
  const canDissolveGroup = useMemo<PolicyResult>(
    () => groupService.canDissolveGroup(selectedGroup, activeIdentityId),
    [selectedGroup, activeIdentityId],
  );

  const onSelectGroup = useCallback(
    async (groupId: string) => {
      useWorkspaceStore.getState().selectGroup(groupId);
      // 管理按钮需读取群详情里的 participants/owner/driver；列表项没有这些字段，选中后必须补齐详情。
      const detail = await groupService.loadGroupDetailOrBcs(groupId, activeIdentityId ?? undefined);
      if (!detail.ok) {
        toast.error(detail.error.code === 'GROUP_MISSING' ? '该协作群不存在或已被删除。' : '加载协作群失败。');
        return;
      }
      setSessionGroups((current) =>
        current.some((g) => g.groupId === detail.data.groupId)
          ? current.map((g) => (g.groupId === detail.data.groupId ? detail.data : g))
          : [detail.data, ...current],
      );
    },
    [activeIdentityId],
  );

  const refreshGroups = useCallback(async () => {
    await loadGroups(activeIdentityId, lastSearchRef.current || undefined);
  }, [loadGroups, activeIdentityId]);

  const reloadSelectedGroup = useCallback(async () => {
    if (!selectedGroupId) return;
    const res = await groupService.loadGroupDetailOrBcs(selectedGroupId, activeIdentityId ?? undefined);
    if (!res.ok) {
      toast.error(res.error.friendlyMessage);
      return;
    }
    setSessionGroups((current) => current.map((group) => (group.groupId === selectedGroupId ? res.data : group)));
  }, [activeIdentityId, selectedGroupId]);

  const dissolveGroup = useCallback(
    async (groupId: string) => {
      const group = sessionGroups.find((g) => g.groupId === groupId) ?? null;
      const permission = groupService.canDissolveGroup(group, useWorkspaceStore.getState().activeIdentityId);
      if (!permission.allowed) {
        toast.info(permission.disabledReason ?? '当前无权解散该协作群。');
        return;
      }
      const res = await groupService.dissolveGroup(groupId);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return;
      }
      toast.success('协作群已解散');
      setSessionGroups((current) => current.filter((g) => g.groupId !== groupId));
      if (useWorkspaceStore.getState().selectedGroupId === groupId) {
        useWorkspaceStore.getState().selectGroup(null);
      }
    },
    [sessionGroups],
  );

  return {
    identities,
    activeIdentityId,
    activeIdentity,
    groups,
    isLoadingGroups: isGroupsLoading,
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
    reloadSelectedGroup,
    dissolveGroup,
    canManageGroup,
    canDissolveGroup,
  };
}
