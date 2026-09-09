// 空间成员增删改 Hook：refreshMembers / addMember / addMembers(批量) /
// removeMember / updateRole 编排 adminService + toast。批量添加：Promise.allSettled 聚合在
// service 层完成（design D2 按 envelope {error} 归属）；本 Hook 只做 toast/refresh/加载态编排：
//   - 文案 design D6（全成功 notifySuccess；混合/全失败 notifyError 聚合）
//   - 单次 refreshMembers
//   - in-flight 期间以 addingMembersRef 同步阻断重入（防双击/重复提交）；addMembersLoading 暴露给 Component
// 被 useAdmin 组合，避免 useAdmin 超文件体积阈值（Hook ≤ 250 行）。
import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { SearchedUser } from '@/capabilities';
import type { Space, SpaceMember } from '@/domain/admin/models';
import { adminService } from '@/services/admin';
import { useCallback, useRef, useState } from 'react';

export interface UseSpaceMemberActionsArgs {
  /** 当前打开的空间；其 spaceId 驱动成员增删改请求。 */
  currentSpace: Space | null;
  /** store 写入成员列表（refreshMembers 后同步成功计入项）。 */
  setMembers: (members: SpaceMember[]) => void;
}

export interface SpaceMemberActions {
  refreshMembers: () => Promise<void>;
  addMember: (userId: string, role?: 'ADMIN' | 'MEMBER', userName?: string) => Promise<void>;
  addMembers: (users: SearchedUser[], role?: 'ADMIN' | 'MEMBER') => Promise<
    { succeeded: SpaceMember[]; failed: { userId: string; userName?: string; reason: string }[] } | undefined
  >;
  removeMember: (userId: string) => Promise<void>;
  updateRole: (userId: string, role: 'ADMIN' | 'MEMBER') => Promise<void>;
  addMembersLoading: boolean;
  addMembersDisabledReason: string | undefined;
}

export function useSpaceMemberActions({ currentSpace, setMembers }: UseSpaceMemberActionsArgs): SpaceMemberActions {
  // 批量添加 in-flight 同步重入阻断用 ref（useState 异步，无法挡同一 tick 内的重入/双击）。
  const addingMembersRef = useRef(false);
  const [addingMembers, setAddingMembers] = useState(false);

  const refreshMembers = useCallback(async () => {
    if (!currentSpace?.spaceId) return;
    const r = await adminService.listMembers(currentSpace.spaceId);
    if (!r.error) setMembers(r.data?.items ?? []);
  }, [currentSpace, setMembers]);

  const addMember = useCallback(
    async (userId: string, role: 'ADMIN' | 'MEMBER' = 'MEMBER', userName?: string) => {
      if (!currentSpace?.spaceId) return;
      const r = await adminService.addMember(currentSpace.spaceId, userId, role, userName);
      if (r.error) {
        notifyError(r.error.message, { title: '添加成员失败', requestId: r.error.requestId });
        return;
      }
      notifySuccess('成员已添加');
      void refreshMembers();
    },
    [currentSpace, refreshMembers],
  );

  const addMembers = useCallback(
    async (users: SearchedUser[], role: 'ADMIN' | 'MEMBER' = 'MEMBER') => {
      if (!currentSpace?.spaceId) return;
      if (users.length === 0 || addingMembersRef.current) return;
      addingMembersRef.current = true;
      setAddingMembers(true);
      try {
        const items = users.map((u) => ({
          userId: u.userId,
          // 花名优先 nickName；其次 displayName（非工号时）；其余不传，避免把工号误写成花名。
          userName: u.nickName || (u.displayName !== u.userId ? u.displayName : undefined),
        }));
        const r = await adminService.addMembersBatch(currentSpace.spaceId, items, role);
        const ok = r.succeeded.length;
        const fail = r.failed.length;
        if (fail === 0) {
          notifySuccess(`已添加 ${ok} 名成员`);
        } else {
          const detail = r.failed.map((f) => `${f.userName ?? f.userId}(${f.reason})`).join('、');
          notifyError(`${ok}/${ok + fail} 添加成功；${fail} 名失败：${detail}`, { title: '添加成员部分失败' });
        }
        // 成员表需同步成功计入项 → 一次 refresh 把成功者拉进列表。
        void refreshMembers();
        return r;
      } finally {
        addingMembersRef.current = false;
        setAddingMembers(false);
      }
    },
    [currentSpace, refreshMembers],
  );

  const removeMember = useCallback(
    async (userId: string) => {
      if (!currentSpace?.spaceId) return;
      const r = await adminService.removeMember(currentSpace.spaceId, userId);
      if (r.error) {
        notifyError(r.error.message, { title: '移除成员失败', requestId: r.error.requestId });
        return;
      }
      notifySuccess('成员已移除');
      void refreshMembers();
    },
    [currentSpace, refreshMembers],
  );

  const updateRole = useCallback(
    async (userId: string, role: 'ADMIN' | 'MEMBER') => {
      if (!currentSpace?.spaceId) return;
      const r = await adminService.updateRole(currentSpace.spaceId, userId, role);
      if (r.error) {
        notifyError(r.error.message, { title: '修改角色失败', requestId: r.error.requestId });
        return;
      }
      notifySuccess('角色已更新');
      void refreshMembers();
    },
    [currentSpace, refreshMembers],
  );

  return {
    refreshMembers,
    addMember,
    addMembers,
    removeMember,
    updateRole,
    addMembersLoading: addingMembers,
    addMembersDisabledReason: addingMembers ? '正在添加成员…' : undefined,
  };
}
