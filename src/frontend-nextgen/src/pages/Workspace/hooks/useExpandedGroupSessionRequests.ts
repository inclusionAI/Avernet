import type { GroupSessionPage, SessionView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { shouldMuteNonAuthedToast } from '@/utils/loginToastGate';
import { useEffect, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { toast } from 'sonner';
import { dropGroupCache } from './sessionMapRequests.utils';

interface ExpandedGroupSessionRequestsOptions {
  activeIdentityId: string | null;
  expandedGroupIds: string[];
  groupId: string | null;
  rawByGroupId: Record<string, SessionView[]>;
  rawByGroupIdRef: MutableRefObject<Record<string, SessionView[]>>;
  inFlightRef: MutableRefObject<Map<string, number>>;
  identityEpochRef: MutableRefObject<number>;
  beginGroupRequest: (groupId: string) => number;
  isCurrentRequest: (groupId: string, version: number, epoch: number) => boolean;
  replaceGroupPage: (groupId: string, data: GroupSessionPage | SessionView[]) => void;
  setErrorByGroupId: Dispatch<SetStateAction<Record<string, string>>>;
  setRawByGroupId: Dispatch<SetStateAction<Record<string, SessionView[]>>>;
}

export function useExpandedGroupSessionRequests({
  activeIdentityId,
  expandedGroupIds,
  groupId,
  rawByGroupId,
  rawByGroupIdRef,
  inFlightRef,
  identityEpochRef,
  beginGroupRequest,
  isCurrentRequest,
  replaceGroupPage,
  setErrorByGroupId,
  setRawByGroupId,
}: ExpandedGroupSessionRequestsOptions) {
  const prevExpandedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!activeIdentityId) return;
    const prev = prevExpandedRef.current;
    const next = new Set(expandedGroupIds);
    prevExpandedRef.current = next;
    for (const gid of expandedGroupIds) {
      // 重新展开（点击群 tab）必重拉最新列表：缓存可能残留当前角色已离开的会话。
      // 非 transition 时维持原去重语义（选中群由选中路径负责、已缓存不重复拉）。
      const justExpanded = !prev.has(gid);
      if (!justExpanded && (gid === groupId || rawByGroupId[gid] !== undefined)) continue;
      if (inFlightRef.current.has(gid)) continue;
      // 选中群重拉前清掉陈旧缓存：自动选中/陈旧选中兜底在窗口期不消费旧数据。
      if (gid === groupId) dropGroupCache(rawByGroupIdRef, setRawByGroupId, gid);
      const requestEpoch = identityEpochRef.current;
      const requestVersion = beginGroupRequest(gid);
      inFlightRef.current.set(gid, requestVersion);
      groupService
        .loadGroupSessionsOrBcs(gid, activeIdentityId)
        .then((res) => {
          if (!isCurrentRequest(gid, requestVersion, requestEpoch)) return;
          if (res.ok) {
            setErrorByGroupId((current) => {
              const next = { ...current };
              delete next[gid];
              return next;
            });
            replaceGroupPage(gid, res.data);
          } else {
            replaceGroupPage(gid, []);
            setErrorByGroupId((current) => ({ ...current, [gid]: res.error.friendlyMessage }));
            // 未登录（oauth-provider + 非 authenticated）静默：会话失效后展开群 sessions 的
            // 失败 toast 统一由 ExternalLoginPromptModal 承担（见 loginToastGate）。
            if (!shouldMuteNonAuthedToast()) toast.error(res.error.friendlyMessage);
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
    rawByGroupIdRef,
    replaceGroupPage,
    setErrorByGroupId,
    setRawByGroupId,
  ]);
}
