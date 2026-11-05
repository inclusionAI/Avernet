// useGroupWorkspace 的成员/详情动作下沉层：群行头像成员补拉（useGroupParticipantHydration）、
// 选中群详情按需重载、解散群。头像补拉的缓存与请求代次由 helper Hook 持有；
// 本文件只保留选中群详情重载与解散（工程红线：Hook 单文件 ≤250 行）。
import type { GroupView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { toast } from 'sonner';
import { useCallback } from 'react';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useGroupParticipantHydration } from './useGroupParticipantHydration';

interface UseGroupWorkspaceMutationsParams {
  /** 主 Hook 的群列表 state（读取当前值：解散校验用）。 */
  sessionGroups: GroupView[];
  setSessionGroups: React.Dispatch<React.SetStateAction<GroupView[]>>;
  activeIdentityId: string | null;
  selectedGroupId: string | null;
}

export function useGroupWorkspaceMutations({
  sessionGroups,
  setSessionGroups,
  activeIdentityId,
  selectedGroupId,
}: UseGroupWorkspaceMutationsParams) {
  const hydration = useGroupParticipantHydration(setSessionGroups);
  // 解构稳定方法：不能把每次渲染新建的 hydration 对象放进依赖数组。
  const {
    beginListLoad,
    captureRequest,
    isCurrentRequest,
    seedFromCache,
    enrichParticipants,
    rememberParticipants,
    forgetParticipants,
  } = hydration;

  const reloadSelectedGroup = useCallback(
    async (groupId?: string) => {
      const gid = groupId ?? selectedGroupId;
      if (!gid) return;
      const request = captureRequest();
      // 防止旧身份闭包在身份已切换后发起请求。
      if (request.identityId !== activeIdentityId) return;
      const res = await groupService.loadGroupDetailOrBcs(gid, activeIdentityId ?? undefined);
      // 列表/身份已换代：迟到详情（含失败）不能写入当前上下文。
      if (!isCurrentRequest(request)) return;
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return;
      }
      // 成功响应包括空成员也必须记入缓存，避免后续列表播种用回旧名单。
      rememberParticipants(gid, res.data.participants);
      setSessionGroups((current) =>
        current.some((g) => g.groupId === gid)
          ? current.map((g) => (g.groupId === gid ? res.data : g))
          : [res.data, ...current],
      );
    },
    [
      activeIdentityId,
      selectedGroupId,
      setSessionGroups,
      captureRequest,
      isCurrentRequest,
      rememberParticipants,
    ],
  );

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
      forgetParticipants(groupId);
      setSessionGroups((current) => current.filter((g) => g.groupId !== groupId));
      if (useWorkspaceStore.getState().selectedGroupId === groupId) {
        useWorkspaceStore.getState().selectGroup(null);
      }
    },
    [sessionGroups, setSessionGroups, forgetParticipants],
  );

  return {
    beginListLoad,
    captureRequest,
    isCurrentRequest,
    seedFromCache,
    enrichParticipants,
    rememberParticipants,
    forgetParticipants,
    reloadSelectedGroup,
    dissolveGroup,
  };
}
