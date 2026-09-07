import type { SessionView } from '@/domain/collaboration';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useRef, type MutableRefObject } from 'react';

/**
 * useStaleSessionFallback —— 陈旧选中兜底（从 useGroupSessions 拆出以控体积）：
 * 列表加载完成后，记忆的选中会话可能不在列表中（被删/成员视角变化/超出已加载分页）。
 * 先反查详情：仍存在 → 前置补入列表保持选中；不存在 → 回落首条或清空。
 * 每个 sessionId 只兜底一次；响应返回时校验身份代际与选中态，避免竞态劫持。
 */
export function useStaleSessionFallback(
  groupId: string | null,
  isLoading: boolean,
  rawByGroupId: Record<string, SessionView[]>,
  applyMapUpdate: (fn: (cur: Record<string, SessionView[]>) => Record<string, SessionView[]>) => void,
  selectSession: (sessionId: string | null) => void,
  identityEpochRef: MutableRefObject<number>,
): void {
  const triedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!groupId || isLoading || rawByGroupId[groupId] === undefined) return;
    // hook 参数群与 store 选中群不一致（跨群新建等过渡帧）：选中归属他群，不做兜底。
    if (useWorkspaceStore.getState().selectedGroupId !== groupId) return;
    const current = useWorkspaceStore.getState().selectedSessionId;
    if (!current || triedRef.current.has(current)) return;
    if ((rawByGroupId[groupId] ?? []).some((s) => s.sessionId === current)) return;
    triedRef.current.add(current);
    const epoch = identityEpochRef.current;
    void sessionService.getSessionDetail(current).then((res) => {
      // 身份已切换：旧身份的响应不得回填新列表，也不得改动选中。
      if (identityEpochRef.current !== epoch) return;
      if (res.ok) {
        applyMapUpdate((cur) => ({
          ...cur,
          [groupId]: [res.data, ...(cur[groupId] ?? []).filter((s) => s.sessionId !== res.data.sessionId)],
        }));
        return;
      }
      // 请求在途时用户可能已点选其他会话/切群：仅当选中仍是陈旧项时才回落。
      if (useWorkspaceStore.getState().selectedSessionId !== current) return;
      const latest = rawByGroupId[groupId] ?? [];
      selectSession(latest[0]?.sessionId ?? null);
    });
  }, [applyMapUpdate, groupId, identityEpochRef, isLoading, rawByGroupId, selectSession]);
}
