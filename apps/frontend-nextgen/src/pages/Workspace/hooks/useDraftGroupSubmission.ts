import { navigateToLaunchedGroup } from '@/services/workspace/collaborationGroupLaunchNavigation';
import { launchCollaborationGroup } from '@/services/workspace/collaborationGroupLaunchService';
import type { CreateGroupInput } from '@/services/workspace/groupCreateRequest';
import { useCollaborationGroupLaunchStore } from '@/stores/collaborationGroupLaunchStore';
import { useRef, useState } from 'react';

export function useDraftGroupSubmission() {
  const lock = useRef(false);
  const [creating, setCreating] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState<string>();
  const submit = async (input: CreateGroupInput) => {
    if (lock.current) return;
    lock.current = true;
    setCreating(true);
    setError(undefined);
    useCollaborationGroupLaunchStore.getState().setBusy(true);
    try {
      const result = await launchCollaborationGroup(input);
      if (result.ok) {
        useCollaborationGroupLaunchStore.getState().setBusy(false);
        useCollaborationGroupLaunchStore.getState().close();
        navigateToLaunchedGroup(result.data);
        return;
      }
      const unknown = result.error.code === 'GROUP_CREATE_OUTCOME_UNKNOWN';
      lock.current = unknown;
      setBlocked(unknown);
      setError(result.error.friendlyMessage);
    } catch {
      setBlocked(true);
      setError('建群结果暂未确认，请先到协作群列表查看');
    } finally {
      setCreating(false);
      useCollaborationGroupLaunchStore.getState().setBusy(false);
    }
  };
  return { creating, blocked, error, submit };
}
