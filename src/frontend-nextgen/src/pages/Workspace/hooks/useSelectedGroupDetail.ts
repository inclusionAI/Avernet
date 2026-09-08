import type { GroupView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useState } from 'react';

/**
 * useSelectedGroupDetail —— selectedGroupId 直接写入（URL/邀请/去发言）时的成员视角兜底：
 * 若群不在「固定协作成员」列表中，切换到「仅参与临时会话」列表，而不是补拉群详情。
 */
export function useSelectedGroupDetail(
  selectedGroupId: string | null,
  sessionGroups: GroupView[],
  isGroupsLoading: boolean,
) {
  const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const membership = useWorkspaceStore((s) => s.membership);
  const setMembership = useWorkspaceStore((s) => s.setMembership);

  // 身份切换/挂载后标记恢复中，loadGroups 完成并稳定一帧后解除。
  // 恢复期间跳过 membership 自动纠正，避免覆盖用户之前选择的视角。
  // 初值必须为 true：暖重挂载（store 已有 selectedGroupId、列表 hook 局部 state 为空）时，
  // 首个 effect flush 中 isGroupsLoading 闭包仍为 false（loadGroups 同步置位发生在同一 flush），
  // 若初值为 false 三个守卫全部失守，membership 会被误翻转为 session_only。
  const [isRestoringList, setIsRestoringList] = useState(true);
  useEffect(() => {
    setIsRestoringList(true);
    if (isGroupsLoading) return;
    // 列表已加载，下一帧解除恢复标记。
    const timer = setTimeout(() => {
      setIsRestoringList(false);
    }, 0);
    return () => clearTimeout(timer);
  }, [activeIdentityId, isGroupsLoading]);

  useEffect(() => {
    if (!selectedGroupId) return;
    // 群已在当前筛选列表中：列表项已含侧边栏与聊天头部展示所需字段。
    if (sessionGroups.some((group) => group.groupId === selectedGroupId)) {
      return;
    }
    // 列表加载/身份恢复期间等待，避免暂空列表被误判为群不属于当前视角。
    if (isGroupsLoading || isRestoringList) return;
    // 深链/会话邀请进入的群可能只出现在 session_only 列表；切换视角复用列表接口。
    // 群详情 participants/owner/driver 仍由管理面板打开时按需拉取。
    if (membership === 'direct') setMembership('session_only');
  }, [isGroupsLoading, isRestoringList, membership, selectedGroupId, sessionGroups, setMembership]);
}
