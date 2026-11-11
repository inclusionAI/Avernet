import type { GroupView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useState } from 'react';

interface SelectedGroupDetail {
  identityId: string;
  groupId: string;
  group: GroupView | null;
  error: string | null;
}

/** A sidebar page/search miss is not a membership decision: resolve the selected group by id. */
export function useSelectedGroupDetail(
  selectedGroupId: string | null,
  sessionGroups: GroupView[],
  isGroupsLoading: boolean,
  listReadyForCurrentIdentity: boolean,
) {
  const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const identities = useWorkspaceStore((s) => s.identities);
  const viewBotId = identities.find((identity) => identity.id === activeIdentityId)?.kind === 'bot'
    ? activeIdentityId ?? undefined
    : undefined;
  const listed = listReadyForCurrentIdentity && sessionGroups.some((group) => group.groupId === selectedGroupId);
  const [detail, setDetail] = useState<SelectedGroupDetail | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const retry = useCallback(() => setRetryCount((count) => count + 1), []);
  const enabled = Boolean(selectedGroupId && activeIdentityId && !listed);

  useEffect(() => {
    // Do not use the previous identity's list, or infer permissions from a partial page.
    if (!enabled || !listReadyForCurrentIdentity || isGroupsLoading) {
      setDetail(null);
      return;
    }
    const groupId = selectedGroupId!;
    const identityId = activeIdentityId!;
    let cancelled = false;
    setDetail(null);
    const finish = (group: GroupView | null, error: string | null) => {
      if (!cancelled) setDetail({ identityId, groupId, group, error });
    };
    void groupService.loadGroupDetailOrBcs(groupId, viewBotId).then((result) => {
      if (!result.ok) {
        finish(null, result.error.friendlyMessage);
      } else if (result.data.groupId !== groupId || result.data.status === 'dissolved') {
        finish(null, '指定协作群不存在、已解散或无权访问。');
      } else {
        finish(result.data, null);
      }
    }).catch(() => finish(null, '加载指定协作群失败，请稍后重试。'));
    // Selection/identity changes, list refreshes and unmount invalidate every late response.
    return () => { cancelled = true; };
  }, [activeIdentityId, enabled, isGroupsLoading, listReadyForCurrentIdentity, retryCount, selectedGroupId, viewBotId]);

  const current = enabled && detail?.identityId === activeIdentityId && detail.groupId === selectedGroupId
    ? detail : null;
  return {
    group: current?.group ?? null,
    error: current?.error ?? null,
    loading: enabled && !current,
    retry,
  };
}
