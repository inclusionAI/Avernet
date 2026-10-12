import { notifyError } from '@/components/ui/notify';
import type { PublicGroup, SquareResource } from '@/domain/collaborationSquare/types';
import { CollaborationSquareError, collaborationSquareGroupService } from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { getCollaborationSquareErrorMessage } from '@/utils/collaborationSquare';
import { useCallback, useEffect, useRef } from 'react';

/** 同一次详情请求回填成员及 Owner；切群、关闭、身份变化或卸载后忽略过期结果。 */
export function useSquareGroupMembers(
  onTargetInvalid: (resource: SquareResource, id: string) => void,
  identityKey: string,
) {
  const { setSelectedGroupId, setDetailLoading, setGroupMembersDetail } = useCollaborationSquareStore();
  const epoch = useRef(0);
  useEffect(
    () => () => {
      epoch.current += 1;
    },
    [identityKey],
  );

  return useCallback(
    async (group: PublicGroup) => {
      const request = ++epoch.current;
      const isCurrent = () =>
        request === epoch.current && useCollaborationSquareStore.getState().selectedGroupId === group.id;
      setSelectedGroupId(group.id);
      setDetailLoading(true);
      try {
        const detail = await collaborationSquareGroupService.listGroupMembers(group.id);
        if (isCurrent()) setGroupMembersDetail(detail);
      } catch (error) {
        if (!isCurrent()) return;
        if (error instanceof CollaborationSquareError && error.code === 'target_invalid') {
          onTargetInvalid('group', group.id);
        } else notifyError(getCollaborationSquareErrorMessage(error));
      } finally {
        if (isCurrent()) setDetailLoading(false);
      }
    },
    [onTargetInvalid, setDetailLoading, setGroupMembersDetail, setSelectedGroupId],
  );
}
