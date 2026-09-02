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
      if (initialSessionId) {
        // Start selection/session loading immediately from the create response;
        // the group-list refresh can finish in parallel with Driver/Manager startup.
        selectGroup(groupId);
        await openSessionForGroup(groupId, initialSessionId);
        await refreshGroups();
        return;
      }
      await refreshGroups();
      selectGroup(groupId);
      await openSessionForGroup(groupId, initialSessionId);
    },
    [openSessionForGroup, refreshGroups, selectGroup],
  );

  return { open, openModal, closeModal, handleCreated };
}
