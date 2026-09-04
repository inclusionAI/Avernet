import type { GroupSessionPage, SessionView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import type { DomainError } from '@/services/workspace/identityService';
import { shouldMuteNonAuthedToast } from '@/utils/loginToastGate';
import { useCallback, useEffect, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { toast } from 'sonner';
import { useExpandedGroupSessionRequests } from './useExpandedGroupSessionRequests';

const SESSION_PAGE_SIZE = 10;

interface SessionPageMeta {
  total: number;
  hasMore: boolean;
  /** 后端分页游标，避免本地新建/删除导致 offset 与服务端列表错位。 */
  nextOffset: number;
  isLoadingMore: boolean;
}

type SessionMap = Record<string, SessionView[]>;
type SessionMapRef = MutableRefObject<SessionMap>;
type SessionMetaMapRef = MutableRefObject<Record<string, SessionPageMeta>>;

interface UseSessionMapRequestsOptions {
  groupId: string | null;
  expandedGroupIds: string[];
  activeIdentityId: string | null;
  rawByGroupId: SessionMap;
  rawByGroupIdRef: SessionMapRef;
  pageMetaByGroupIdRef: SessionMetaMapRef;
  inFlightRef: MutableRefObject<Map<string, number>>;
  loadingMoreRef: MutableRefObject<Set<string>>;
  requestVersionRef: MutableRefObject<Map<string, number>>;
  identityEpochRef: MutableRefObject<number>;
  setIsLoading: Dispatch<SetStateAction<boolean>>;
  setRawByGroupId: Dispatch<SetStateAction<SessionMap>>;
  setPageMetaByGroupId: Dispatch<SetStateAction<Record<string, SessionPageMeta>>>;
  beginGroupRequest: (groupId: string) => number;
  isCurrentRequest: (groupId: string, version: number, epoch: number) => boolean;
  replaceGroupPage: (groupId: string, data: GroupSessionPage | SessionView[]) => void;
}

function notifyError(err: DomainError): void {
  // 未登录（oauth-provider + 非 authenticated）静默：会话失效后的 sessions 加载失败 toast
  // 统一由 ExternalLoginPromptModal 承担（见 loginToastGate）；已登录 / ace-gateway 照常提示。
  if (shouldMuteNonAuthedToast()) return;
  toast.error(err.friendlyMessage);
}

function normalizePage(data: GroupSessionPage | SessionView[], fallbackOffset = 0): GroupSessionPage {
  if (Array.isArray(data)) {
    return {
      items: data,
      offset: fallbackOffset,
      limit: SESSION_PAGE_SIZE,
      total: fallbackOffset + data.length,
      hasMore: data.length >= SESSION_PAGE_SIZE,
    };
  }
  return data;
}

export function useSessionMapRequests({
  groupId,
  expandedGroupIds,
  activeIdentityId,
  rawByGroupId,
  rawByGroupIdRef,
  pageMetaByGroupIdRef,
  inFlightRef,
  loadingMoreRef,
  requestVersionRef,
  identityEpochRef,
  setIsLoading,
  setRawByGroupId,
  setPageMetaByGroupId,
  beginGroupRequest,
  isCurrentRequest,
  replaceGroupPage,
}: UseSessionMapRequestsOptions) {
  // 用 epoch+gid 做已拉标记：epoch 变化（身份切换清空缓存）时旧标记失效，重新拉。
  const lastLoadedKeyRef = useRef<string>('');
  useEffect(() => {
    if (!groupId || !activeIdentityId) return;
    const key = `${identityEpochRef.current}:${groupId}`;
    // 仅在 groupId 或 epoch 变化时拉取，避免 effect 因其他依赖变化重复拉同一群。
    if (key === lastLoadedKeyRef.current) return;
    if (rawByGroupIdRef.current[groupId] !== undefined || inFlightRef.current.has(groupId)) {
      lastLoadedKeyRef.current = key;
      return;
    }
    let cancelled = false;
    const requestEpoch = identityEpochRef.current;
    const requestVersion = beginGroupRequest(groupId);
    setIsLoading(true);
    inFlightRef.current.set(groupId, requestVersion);
    lastLoadedKeyRef.current = key;
    groupService
      .loadGroupSessionsOrBcs(groupId, activeIdentityId ?? undefined)
      .then((res) => {
        // 切群后同身份的旧请求仍可回填旧群；身份切换或更新请求后的旧响应必须丢弃。
        if (!isCurrentRequest(groupId, requestVersion, requestEpoch)) return;
        if (res.ok) replaceGroupPage(groupId, res.data);
        else if (cancelled) replaceGroupPage(groupId, []);
        else {
          notifyError(res.error);
          replaceGroupPage(groupId, []);
        }
      })
      .finally(() => {
        if (inFlightRef.current.get(groupId) === requestVersion) inFlightRef.current.delete(groupId);
        // 仅最新未取消的请求负责 isLoading 收尾，避免旧请求把新群的 loading 提前置空。
        if (!cancelled && isCurrentRequest(groupId, requestVersion, requestEpoch)) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    activeIdentityId,
    beginGroupRequest,
    groupId,
    identityEpochRef,
    inFlightRef,
    isCurrentRequest,
    replaceGroupPage,
    setIsLoading,
  ]);

  useExpandedGroupSessionRequests({
    activeIdentityId,
    expandedGroupIds,
    groupId,
    rawByGroupId,
    inFlightRef,
    identityEpochRef,
    beginGroupRequest,
    isCurrentRequest,
    replaceGroupPage,
  });

  const reloadGroup = useCallback(
    async (gid: string): Promise<void> => {
      const requestEpoch = identityEpochRef.current;
      const requestVersion = beginGroupRequest(gid);
      const res = await groupService.loadGroupSessionsOrBcs(gid, activeIdentityId ?? undefined);
      if (!isCurrentRequest(gid, requestVersion, requestEpoch)) return;
      if (res.ok) replaceGroupPage(gid, res.data);
      else {
        notifyError(res.error);
        const currentMeta = pageMetaByGroupIdRef.current[gid];
        if (currentMeta?.isLoadingMore) {
          const nextMeta = {
            ...pageMetaByGroupIdRef.current,
            [gid]: { ...currentMeta, isLoadingMore: false },
          };
          pageMetaByGroupIdRef.current = nextMeta;
          setPageMetaByGroupId(nextMeta);
        }
      }
    },
    [
      activeIdentityId,
      beginGroupRequest,
      identityEpochRef,
      isCurrentRequest,
      pageMetaByGroupIdRef,
      replaceGroupPage,
      setPageMetaByGroupId,
    ],
  );

  const loadMoreSessions = useCallback(
    async (gid: string): Promise<void> => {
      const meta = pageMetaByGroupIdRef.current[gid];
      const current = rawByGroupIdRef.current[gid];
      if (!meta || !current || !meta.hasMore || loadingMoreRef.current.has(gid)) return;
      const requestEpoch = identityEpochRef.current;
      const requestVersion = requestVersionRef.current.get(gid) ?? 0;
      loadingMoreRef.current.add(gid);
      const loadingMeta = {
        ...pageMetaByGroupIdRef.current,
        [gid]: { ...meta, isLoadingMore: true },
      };
      pageMetaByGroupIdRef.current = loadingMeta;
      setPageMetaByGroupId(loadingMeta);
      try {
        const res = await groupService.loadGroupSessionsOrBcs(gid, activeIdentityId ?? undefined, {
          offset: meta.nextOffset,
          limit: SESSION_PAGE_SIZE,
        });
        if (!isCurrentRequest(gid, requestVersion, requestEpoch)) {
          return;
        }
        if (!res.ok) {
          notifyError(res.error);
          return;
        }
        const page = normalizePage(res.data, meta.nextOffset);
        const existing = rawByGroupIdRef.current[gid] ?? [];
        const seen = new Set(existing.map((session) => session.sessionId));
        const appended = page.items.filter((session) => !seen.has(session.sessionId));
        const next = { ...rawByGroupIdRef.current, [gid]: [...existing, ...appended] };
        const nextMeta = {
          ...pageMetaByGroupIdRef.current,
          [gid]: {
            total: page.total,
            hasMore: page.hasMore,
            nextOffset: page.offset + page.limit,
            isLoadingMore: false,
          },
        };
        rawByGroupIdRef.current = next;
        pageMetaByGroupIdRef.current = nextMeta;
        setRawByGroupId(next);
        setPageMetaByGroupId(nextMeta);
      } finally {
        loadingMoreRef.current.delete(gid);
        if (isCurrentRequest(gid, requestVersion, requestEpoch)) {
          const currentMeta = pageMetaByGroupIdRef.current[gid];
          if (currentMeta?.isLoadingMore) {
            const nextMeta = {
              ...pageMetaByGroupIdRef.current,
              [gid]: { ...currentMeta, isLoadingMore: false },
            };
            pageMetaByGroupIdRef.current = nextMeta;
            setPageMetaByGroupId(nextMeta);
          }
        }
      }
    },
    [
      activeIdentityId,
      identityEpochRef,
      isCurrentRequest,
      loadingMoreRef,
      pageMetaByGroupIdRef,
      rawByGroupIdRef,
      requestVersionRef,
      setPageMetaByGroupId,
      setRawByGroupId,
    ],
  );

  return { reloadGroup, loadMoreSessions };
}
