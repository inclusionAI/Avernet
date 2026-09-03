import type { GroupSessionPage, SessionView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { useEffect, type MutableRefObject } from 'react';
import { toast } from 'sonner';

interface ExpandedGroupSessionRequestsOptions {
  activeIdentityId: string | null;
  expandedGroupIds: string[];
  groupId: string | null;
  rawByGroupId: Record<string, SessionView[]>;
  inFlightRef: MutableRefObject<Map<string, number>>;
  identityEpochRef: MutableRefObject<number>;
  beginGroupRequest: (groupId: string) => number;
  isCurrentRequest: (groupId: string, version: number, epoch: number) => boolean;
  replaceGroupPage: (groupId: string, data: GroupSessionPage | SessionView[]) => void;
}

export function useExpandedGroupSessionRequests({
  activeIdentityId,
  expandedGroupIds,
  groupId,
  rawByGroupId,
  inFlightRef,
  identityEpochRef,
  beginGroupRequest,
  isCurrentRequest,
  replaceGroupPage,
}: ExpandedGroupSessionRequestsOptions) {
  useEffect(() => {
    if (!activeIdentityId) return;
    for (const gid of expandedGroupIds) {
      if (gid === groupId || rawByGroupId[gid] !== undefined || inFlightRef.current.has(gid)) continue;
      const requestEpoch = identityEpochRef.current;
      const requestVersion = beginGroupRequest(gid);
      inFlightRef.current.set(gid, requestVersion);
      groupService
        .loadGroupSessionsOrBcs(gid, activeIdentityId)
        .then((res) => {
          if (!isCurrentRequest(gid, requestVersion, requestEpoch)) return;
          if (res.ok) replaceGroupPage(gid, res.data);
          else {
            replaceGroupPage(gid, []);
            toast.error(res.error.friendlyMessage);
          }
        })
        .finally(() => {
          if (inFlightRef.current.get(gid) === requestVersion) inFlightRef.current.delete(gid);
        });
    }
  }, [
    activeIdentityId,
    beginGroupRequest,
    expandedGroupIds,
    groupId,
    identityEpochRef,
    inFlightRef,
    isCurrentRequest,
    rawByGroupId,
    replaceGroupPage,
  ]);
}
