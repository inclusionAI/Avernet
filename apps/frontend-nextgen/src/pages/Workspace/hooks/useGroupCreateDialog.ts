import type { GroupView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useState } from 'react';

export interface UseGroupCreateDialogResult {
  open: boolean;
  openModal: () => void;
  closeModal: () => void;
  handleCreated: (group: GroupView) => Promise<void>;
}

interface UseGroupCreateDialogOptions {
  refreshGroups: () => Promise<void>;
  selectGroup: (groupId: string) => void;
  openSessionForGroup: (groupId: string, preferredSessionId?: string) => Promise<void>;
}

/** 发起协作弹窗的开合状态与创建成功后刷新/选中编排，避免写入页面组件。 */
export function useGroupCreateDialog({
  refreshGroups,
  selectGroup,
  openSessionForGroup,
}: UseGroupCreateDialogOptions): UseGroupCreateDialogResult {
  const [open, setOpen] = useState(false);
  const openModal = useCallback(() => setOpen(true), []);
  const closeModal = useCallback(() => setOpen(false), []);
  const handleCreated = useCallback(
    async (group: GroupView) => {
      setOpen(false);
      const { groupId, initialSessionId, initialRun } = group;
      if (initialSessionId && initialRun?.state === 'running') {
        useWorkspaceStore.getState().setPendingGroupBootstrap({
          groupId,
          sessionId: initialSessionId,
          run: initialRun,
        });
      }
      // 创建者是固定群成员，先切换视角并清掉旧临时群选中，避免兜底逻辑切回旧筛选。
      const store = useWorkspaceStore.getState();
      store.setGroupSearchText('');
      if (store.membership !== 'direct') {
        store.selectGroup(null);
        store.setMembership('direct');
      }
      // 先让新群进入固定成员列表，再写入选中态。若先选中新群，
      // useSelectedGroupDetail 会把“列表暂未包含新群”误判成 session_only 深链，
      // 导致刷新结果、URL 与侧栏选中态发生竞态。
      await refreshGroups();
      selectGroup(groupId);
      await openSessionForGroup(groupId, initialSessionId);
    },
    [openSessionForGroup, refreshGroups, selectGroup],
  );

  return { open, openModal, closeModal, handleCreated };
}
