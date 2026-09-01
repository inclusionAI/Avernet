import { useCallback, useState } from 'react';

export interface UseGroupCreateDialogResult {
  open: boolean;
  openModal: () => void;
  closeModal: () => void;
  handleCreated: (groupId: string) => Promise<void>;
}

interface UseGroupCreateDialogOptions {
  refreshGroups: () => Promise<void>;
  selectGroup: (groupId: string) => void;
  openSessionForGroup: (groupId: string) => Promise<void>;
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
    async (groupId: string) => {
      setOpen(false);
      await refreshGroups();
      selectGroup(groupId);
      await openSessionForGroup(groupId);
    },
    [openSessionForGroup, refreshGroups, selectGroup],
  );

  return { open, openModal, closeModal, handleCreated };
}
