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
    let cancelled = false;
    groupService.loadGroupDetailOrBcs(selectedGroupId, activeIdentityId ?? undefined).then((res) => {
      if (cancelled || !res?.ok) return;
      setSelectedGroupFallback(res.data);
      // 不从群详情覆盖 membership：用户可能手动切换了「群成员/会话成员」视角，
      // 身份切换后也已从记忆中恢复。群详情的 membership 字段反映后端对该身份的
      // 归属判定，但不应覆盖用户的前端视角选择（下方 auto-correct effect 兜底）。
      setSessionGroups((current) =>
        current.some((group) => group.groupId === selectedGroupId)
          ? current.map((group) => (group.groupId === selectedGroupId ? res.data : group))
          : current,
      );
    });
    return () => {
      cancelled = true;
    };
  }, [selectedGroupId, activeIdentityId, setMembership, setSelectedGroupFallback, setSessionGroups]);

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
