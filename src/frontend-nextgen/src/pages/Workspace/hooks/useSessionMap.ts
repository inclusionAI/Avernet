import type { SessionView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import type { DomainError } from '@/services/workspace/identityService';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

function notifyError(err: DomainError): void {
  toast.error(err.friendlyMessage);
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
  const [isLoading, setIsLoading] = useState(false);
  const inFlightRef = useRef<Set<string>>(new Set());

  // 身份切换 → 清空缓存，避免跨身份串会话数据。
  useEffect(() => {
    setRawByGroupId({});
    inFlightRef.current.clear();
  }, [activeIdentityId]);

  // 选中群加载：groupId 变化必重拉（chat pane 数据面），失败弹 toast 并置空。
  useEffect(() => {
    if (!groupId) return;
    let cancelled = false;
    setIsLoading(true);
    inFlightRef.current.add(groupId);
    groupService.loadGroupDetailOrBcs(groupId, activeIdentityId ?? undefined).then((res) => {
      if (cancelled) return;
      inFlightRef.current.delete(groupId);
      setIsLoading(false);
      if (res.ok) {
        setRawByGroupId((cur) => ({ ...cur, [groupId]: res.data.sessions }));
      } else {
        notifyError(res.error);
        setRawByGroupId((cur) => ({ ...cur, [groupId]: [] }));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [groupId, activeIdentityId]);

  // 展开群加载：未缓存的展开群（undefined 视为未处理，含失败置空）静默各拉一次。
  // 选中群由上面的专用 effect 负责（带 toast/loading），此处跳过避免重复请求。
  useEffect(() => {
    for (const gid of expandedGroupIds) {
      if (gid === groupId) continue;
      if (rawByGroupId[gid] !== undefined) continue;
      if (inFlightRef.current.has(gid)) continue;
      inFlightRef.current.add(gid);
      groupService
        .loadGroupDetailOrBcs(gid, activeIdentityId ?? undefined)
        .then((res) => {
          setRawByGroupId((cur) => ({ ...cur, [gid]: res.ok ? res.data.sessions : [] }));
          if (!res.ok) notifyError(res.error);
        })
        .finally(() => {
          inFlightRef.current.delete(gid);
        });
    }
  }, [expandedGroupIds, groupId, rawByGroupId]);

  const applyMapUpdate = useCallback(
    (fn: (cur: Record<string, SessionView[]>) => Record<string, SessionView[]>) => setRawByGroupId(fn),
    [],
  );

  const updateGroupSessions = useCallback((gid: string, fn: (list: SessionView[]) => SessionView[]) => {
    setRawByGroupId((cur) => ({ ...cur, [gid]: fn(cur[gid] ?? []) }));
  }, []);

  const reloadGroup = useCallback(async (gid: string): Promise<void> => {
    const res = await groupService.loadGroupDetailOrBcs(gid, activeIdentityId ?? undefined);
    if (res.ok) {
      setRawByGroupId((cur) => ({ ...cur, [gid]: res.data.sessions }));
    } else {
      notifyError(res.error);
    }
  }, []);

  return { rawByGroupId, isLoading, applyMapUpdate, updateGroupSessions, reloadGroup };
}
