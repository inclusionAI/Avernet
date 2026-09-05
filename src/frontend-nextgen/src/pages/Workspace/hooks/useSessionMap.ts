import type { GroupSessionPage, SessionView } from '@/domain/collaboration';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSessionMapRequests } from './useSessionMapRequests';

interface SessionPageMeta {
  total: number;
  hasMore: boolean;
  nextOffset: number;
  isLoadingMore: boolean;
}

function normalizePage(data: GroupSessionPage | SessionView[], fallbackOffset = 0): GroupSessionPage {
  if (Array.isArray(data)) {
    return {
      items: data,
      offset: fallbackOffset,
      limit: 10,
      total: fallbackOffset + data.length,
      hasMore: data.length >= 10,
    };
  }
  return data;
}

export interface UseSessionMapResult {
  /** 原始（未过滤）会话 map：groupId → sessions；过滤副本由调用方推导。 */
  rawByGroupId: Record<string, SessionView[]>;
  /** 选中群详情加载中（chat pane 骨架用）。 */
  isLoading: boolean;
  /** 以函数式更新整个 map（rename/remove 等多群遍历委托 sessionService）。 */
  applyMapUpdate: (fn: (cur: Record<string, SessionView[]>) => Record<string, SessionView[]>) => void;
  /** 更新单群会话列表（create 等）。 */
  updateGroupSessions: (gid: string, fn: (list: SessionView[]) => SessionView[]) => void;
  /** 重拉单群会话（失败弹 toast）。 */
  reloadGroup: (gid: string) => Promise<void>;
  /** 是否还有下一页会话。 */
  hasMoreByGroupId: Record<string, boolean>;
  /** 下一页加载状态。 */
  isLoadingMoreByGroupId: Record<string, boolean>;
  errorByGroupId: Record<string, string>;
  loadMoreErrorByGroupId: Record<string, string>;
  /** 后端返回的会话总数，按群维护；未加载过的群不包含在 map 中。 */
  totalByGroupId: Record<string, number>;
  /** 按 10 条追加指定群的下一页会话。 */
  loadMoreSessions: (gid: string) => Promise<void>;
}

/**
 * useSessionMap——useGroupSessions 的内部子 Hook（文件体积受控拆分点）：
 * 以 groupId 键控缓存各群会话。选中群切换必重拉（带 toast/loading）；
 * 展开的群未缓存时各自静默加载一次，之后复用缓存；身份切换清空缓存。
 */
export function useSessionMap(
  groupId: string | null,
  expandedGroupIds: string[],
  activeIdentityId: string | null,
): UseSessionMapResult {
  const [rawByGroupId, setRawByGroupId] = useState<Record<string, SessionView[]>>({});
  const [pageMetaByGroupId, setPageMetaByGroupId] = useState<Record<string, SessionPageMeta>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [errorByGroupId, setErrorByGroupId] = useState<Record<string, string>>({});
  const [loadMoreErrorByGroupId, setLoadMoreErrorByGroupId] = useState<Record<string, string>>({});
  const inFlightRef = useRef<Map<string, number>>(new Map());
  const loadingMoreRef = useRef<Set<string>>(new Set());
  const requestVersionRef = useRef<Map<string, number>>(new Map());
  const identityEpochRef = useRef(0);
  const rawByGroupIdRef = useRef(rawByGroupId);
  const pageMetaByGroupIdRef = useRef(pageMetaByGroupId);
  rawByGroupIdRef.current = rawByGroupId;
  pageMetaByGroupIdRef.current = pageMetaByGroupId;

  // 身份切换 → 清空缓存，避免跨身份串会话数据；旧身份请求不得回填当前列表。
  useEffect(() => {
    identityEpochRef.current += 1;
    requestVersionRef.current.clear();
    setRawByGroupId({});
    setPageMetaByGroupId({});
    setErrorByGroupId({});
    setLoadMoreErrorByGroupId({});
    rawByGroupIdRef.current = {};
    pageMetaByGroupIdRef.current = {};
    inFlightRef.current.clear();
    loadingMoreRef.current.clear();
  }, [activeIdentityId]);

  const beginGroupRequest = useCallback((gid: string): number => {
    const version = (requestVersionRef.current.get(gid) ?? 0) + 1;
    requestVersionRef.current.set(gid, version);
    return version;
  }, []);

  const isCurrentRequest = useCallback((gid: string, version: number, epoch: number): boolean => {
    return identityEpochRef.current === epoch && requestVersionRef.current.get(gid) === version;
  }, []);

  const replaceGroupPage = useCallback((gid: string, data: GroupSessionPage | SessionView[]) => {
    const page = normalizePage(data);
    const next = { ...rawByGroupIdRef.current, [gid]: page.items };
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
  }, []);

  const updateMetaForMapChange = useCallback(
    (prev: Record<string, SessionView[]>, next: Record<string, SessionView[]>): Record<string, SessionPageMeta> => {
      const nextMeta = { ...pageMetaByGroupIdRef.current };
      const groupIds = new Set([...Object.keys(prev), ...Object.keys(next)]);
      for (const gid of groupIds) {
        const previousLength = prev[gid]?.length ?? 0;
        const nextLength = next[gid]?.length ?? 0;
        if (previousLength === nextLength && nextMeta[gid]) continue;
        const previousMeta = nextMeta[gid];
        const total = Math.max(nextLength, (previousMeta?.total ?? previousLength) + nextLength - previousLength);
        nextMeta[gid] = {
          total,
          hasMore: nextLength < total,
          nextOffset: previousMeta?.nextOffset ?? previousLength,
          isLoadingMore: previousMeta?.isLoadingMore ?? false,
        };
      }
      return nextMeta;
    },
    [],
  );

  const { reloadGroup, loadMoreSessions } = useSessionMapRequests({
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
  });

  const applyMapUpdate = useCallback(
    (fn: (cur: Record<string, SessionView[]>) => Record<string, SessionView[]>) => {
      const prev = rawByGroupIdRef.current;
      const next = fn(prev);
      const nextMeta = updateMetaForMapChange(prev, next);
      rawByGroupIdRef.current = next;
      pageMetaByGroupIdRef.current = nextMeta;
      setRawByGroupId(next);
      setPageMetaByGroupId(nextMeta);
    },
    [updateMetaForMapChange],
  );

  const updateGroupSessions = useCallback(
    (gid: string, fn: (list: SessionView[]) => SessionView[]) => {
      const prev = rawByGroupIdRef.current;
      const next = { ...prev, [gid]: fn(prev[gid] ?? []) };
      const nextMeta = updateMetaForMapChange(prev, next);
      rawByGroupIdRef.current = next;
      pageMetaByGroupIdRef.current = nextMeta;
      setRawByGroupId(next);
      setPageMetaByGroupId(nextMeta);
    },
    [updateMetaForMapChange],
  );

  const hasMoreByGroupId: Record<string, boolean> = {};
  const isLoadingMoreByGroupId: Record<string, boolean> = {};
  const totalByGroupId: Record<string, number> = {};
  for (const [gid, meta] of Object.entries(pageMetaByGroupId)) {
    hasMoreByGroupId[gid] = meta.hasMore;
    isLoadingMoreByGroupId[gid] = meta.isLoadingMore;
    totalByGroupId[gid] = meta.total;
  }

  return {
    rawByGroupId,
    isLoading,
    applyMapUpdate,
    updateGroupSessions,
    reloadGroup,
    hasMoreByGroupId,
    isLoadingMoreByGroupId,
    totalByGroupId,
    errorByGroupId,
    loadMoreErrorByGroupId,
    loadMoreSessions,
  };
}
