import type { SessionView } from '@/domain/collaboration';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useRef, type MutableRefObject } from 'react';

/**
 * 「已知退出/删除」登记：UI 退出（leaveSession）/删除（deleteSession）成功的会话 id。
 * 当前角色已不是参与者，任何以其为目标的详情反查都必然失败（协议层还会全局报错），
 * 故陈旧选中兜底与成员详情补齐对登记 id 跳过请求、直接轮换/放弃。
 * SPA 生命周期内存即可：整页刷新后登记清空，未知场景由「静默反查」兜底
 *（sessionService.getSessionDetail 失败时取消协议层默认提示）。
 */
const knownGoneSessionIds = new Set<string>();

export function markSessionGone(sessionId: string): void {
  knownGoneSessionIds.add(sessionId);
}

export function isSessionKnownGone(sessionId: string): boolean {
  return knownGoneSessionIds.has(sessionId);
}

/**
 * useStaleSessionFallback —— 陈旧选中兜底（从 useGroupSessions 拆出以控体积）：
 * 列表加载完成后，记忆的选中会话可能不在列表中（被删/成员视角变化/超出已加载分页）。
 * 先反查详情：仍存在 → 前置补入列表保持选中；不存在 → 回落首条或清空。
 * 已登记「已知退出/删除」的会话不反查（必然失败），直接回落。
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
    // 已知退出/删除：跳过必然失败的反查，直接回落首条或清空。
    if (knownGoneSessionIds.has(current)) {
      if (useWorkspaceStore.getState().selectedSessionId !== current) return;
      const latest = rawByGroupId[groupId] ?? [];
      selectSession(latest[0]?.sessionId ?? null);
      return;
    }
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
