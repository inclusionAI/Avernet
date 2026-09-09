import type { GroupView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useState } from 'react';

/**
 * useSelectedGroupDetail —— selectedGroupId 直接写入（URL/邀请/去发言）时的成员视角兜底：
 * 若群不在「固定协作成员」列表中，切换到「仅参与临时会话」列表，而不是补拉群详情。
 *
 * 跨身份/初始空列表竞态修正（新开页 target=_blank 全新挂载）：
 * 全新挂载、身份切到人类 me 后,首个 commit 内 sessionGroups 可能仍是初始空 [] 或上一身份
 * 陈旧列表,若此刻放任“漏选即纠正”会把 membership 从 direct 错切到 session_only;
 * 而单向纠正(仅 direct→session_only)此后再也回不去,群被 session_only 视角筛掉 → 右栏空态。
 *
 * 守卫:仅当 listReadyForCurrentIdentity(由调用方 useGroupWorkspace 在 loadGroups 成功回填
 * sessionGroups、且列表仍属于当前活动身份时置 true)为真时,才按漏选纠正 membership。
 * 这保证当前可见列表确实是“为当前身份加载完成”的列表(非初始 [] 也非上一身份陈旧列表),
 * 堵住同提交竞态 cursor 抢跑。
 */
export function useSelectedGroupDetail(
  selectedGroupId: string | null,
  sessionGroups: GroupView[],
  isGroupsLoading: boolean,
  listReadyForCurrentIdentity: boolean,
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
    // 当前可见列表尚未为当前身份加载完成(初始空 [] 或上一身份陈旧列表)——
    // 不能据此判漏选,待当前身份列表加载完(listReadyForCurrentIdentity 翻真)再纠正。
    if (!listReadyForCurrentIdentity) return;
    // 列表加载/身份恢复期间等待，避免暂空列表被误判为群不属于当前视角。
    if (isGroupsLoading || isRestoringList) return;
    // 深链/会话邀请进入的群可能只出现在 session_only 列表；切换视角复用列表接口。
    // 群详情 participants/owner/driver 仍由管理面板打开时按需拉取。
    if (membership === 'direct') setMembership('session_only');
  }, [
    isGroupsLoading,
    isRestoringList,
    listReadyForCurrentIdentity,
    membership,
    selectedGroupId,
    sessionGroups,
    setMembership,
  ]);
}
