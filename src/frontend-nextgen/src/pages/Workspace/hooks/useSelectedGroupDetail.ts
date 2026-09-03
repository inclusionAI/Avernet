import type { GroupView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useRef } from 'react';

/**
 * useSelectedGroupDetail —— 从 useGroupWorkspace 拆出的选中群详情兜底逻辑：
 * - selectedGroupId 直接写入（URL/邀请/去发言）但未出现在当前身份筛选列表时，补拉详情；
 * - 用户首次选择 direct 群但后端判定为 session_only 时，自动纠正到「会话成员」。
 *   身份切换恢复期间不纠正（membership 已从记忆恢复）。
 */
export function useSelectedGroupDetail(
  selectedGroupId: string | null,
  activeIdentityId: string | null,
  selectedGroup: GroupView | null,
  sessionGroups: GroupView[],
  isGroupsLoading: boolean,
  setSelectedGroupFallback: (g: GroupView | null) => void,
  setSessionGroups: React.Dispatch<React.SetStateAction<GroupView[]>>,
) {
  const membership = useWorkspaceStore((s) => s.membership);
  const setMembership = useWorkspaceStore((s) => s.setMembership);

  // 身份切换后标记恢复中，loadGroups 完成并稳定一帧后解除。
  // 恢复期间跳过 membership 自动纠正，避免覆盖用户之前选择的视角。
  const restoringRef = useRef(false);
  useEffect(() => {
    restoringRef.current = true;
    if (isGroupsLoading) return;
    // 列表已加载，下一帧解除恢复标记。
    const timer = setTimeout(() => {
      restoringRef.current = false;
    }, 0);
    return () => clearTimeout(timer);
  }, [activeIdentityId, isGroupsLoading]);

  useEffect(() => {
    if (!selectedGroupId) {
      setSelectedGroupFallback(null);
      return;
    }
    // 群已在当前筛选列表中：列表项已含展示所需字段，无需补拉详情，避免选中即触发
    // GET /groups/{id} 详情请求。群详情仅在打开管理面板（查看/编辑）时按需拉取。
    if (sessionGroups.some((group) => group.groupId === selectedGroupId)) {
      setSelectedGroupFallback(null);
      return;
    }
    // 列表尚未加载完成时等待，避免 sessionGroups 暂空时误判为"不在列表"而提前补拉。
    if (isGroupsLoading) return;
    // 仅当选中群不在当前筛选列表（URL 深链/邀请直达/被过滤）时，才补拉详情兜底展示。
    let cancelled = false;
    groupService.loadGroupDetailOrBcs(selectedGroupId, activeIdentityId ?? undefined).then((res) => {
      if (cancelled || !res?.ok) return;
      setSelectedGroupFallback(res.data);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedGroupId, activeIdentityId, sessionGroups, isGroupsLoading, setSelectedGroupFallback, setSessionGroups]);

  // 自动纠正：用户首次选了 direct 群但后端判定为 session_only 时自动切视角。
  // 恢复期间（restoringRef）跳过，避免覆盖记忆恢复的 membership。
  useEffect(() => {
    if (restoringRef.current) return;
    if (!selectedGroupId || !selectedGroup || isGroupsLoading) return;
    if (selectedGroup.membership === 'direct') return;
    if (membership !== 'direct') return;
    if (sessionGroups.some((group) => group.groupId === selectedGroupId)) return;
    setMembership('session_only');
  }, [isGroupsLoading, membership, selectedGroup, selectedGroupId, sessionGroups, setMembership]);
}
