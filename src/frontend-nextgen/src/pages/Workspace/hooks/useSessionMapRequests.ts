import { groupService } from '@/services/workspace/groupService';
import { useCallback, useEffect, useRef } from 'react';
import {
  type UseSessionMapRequestsOptions,
  normalizePage,
  notifyError,
  SESSION_PAGE_SIZE,
} from './sessionMapRequests.utils';
import { useExpandedGroupSessionRequests } from './useExpandedGroupSessionRequests';

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
  setErrorByGroupId,
  setLoadMoreErrorByGroupId,
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
        if (res.ok) {
          setErrorByGroupId((current) => {
            const next = { ...current };
            delete next[groupId];
            return next;
          });
          setLoadMoreErrorByGroupId((current) => {
            const next = { ...current };
            delete next[groupId];
            return next;
          });
          replaceGroupPage(groupId, res.data);
        }
        else if (cancelled) replaceGroupPage(groupId, []);
        else {
          notifyError(res.error);
          setErrorByGroupId((current) => ({ ...current, [groupId]: res.error.friendlyMessage }));
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
    setErrorByGroupId,
    setLoadMoreErrorByGroupId,
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
    setErrorByGroupId,
  });

  const reloadGroup = useCallback(
    async (gid: string): Promise<void> => {
      const requestEpoch = identityEpochRef.current;
      const requestVersion = beginGroupRequest(gid);
      const res = await groupService.loadGroupSessionsOrBcs(gid, activeIdentityId ?? undefined);
      if (!isCurrentRequest(gid, requestVersion, requestEpoch)) return;
      if (res.ok) {
        setErrorByGroupId((current) => {
          const next = { ...current };
          delete next[gid];
          return next;
        });
        setLoadMoreErrorByGroupId((current) => {
          const next = { ...current };
          delete next[gid];
          return next;
        });
        replaceGroupPage(gid, res.data);
      }
      else {
        notifyError(res.error);
        setErrorByGroupId((current) => ({ ...current, [gid]: res.error.friendlyMessage }));
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
      setErrorByGroupId,
      setLoadMoreErrorByGroupId,
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
          setLoadMoreErrorByGroupId((current) => ({ ...current, [gid]: res.error.friendlyMessage }));
          return;
        }
        setLoadMoreErrorByGroupId((current) => {
          const next = { ...current };
          delete next[gid];
          return next;
        });
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
      setLoadMoreErrorByGroupId,
    ],
  );

  return { reloadGroup, loadMoreSessions };
}
