/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { useGroupSessions } from '@/pages/Workspace/hooks/useGroupSessions';
import { groupService } from '@/services/workspace/groupService';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// 多群展开场景（TC-G005：从 useGroupSessions.test.ts 拆出）。
jest.mock('@/services/workspace/sessionService');
jest.mock('@/services/workspace/groupService');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ss = sessionService as unknown as Record<string, jest.Mock<any>>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const gs = groupService as unknown as Record<string, jest.Mock<any>>;

function sessionsFixture(groupId: string, titles: string[]): SessionView[] {
  return titles.map((title, i) => ({
    sessionId: `${groupId}-s${i + 1}`,
    groupId,
    title,
    kind: 'chat',
    status: 'running',
    participants: [],
    lastMessageAt: i + 1,
    createdAt: i + 1,
    favorite: false,
  }));
}

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({ activeIdentityId: 'me' });
  ss.getVisibleSessions.mockImplementation((xs: unknown[]) => xs);
  ss.setFavorite.mockResolvedValue({ ok: true, data: true });
  ss.createNewSession.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  ss.getSessionDetail.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  ss.updateMemberMode.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  // 选中/展开群只拉会话列表（/groups/{id}/sessions），不拉群详情。
  gs.loadGroupSessionsOrBcs.mockImplementation(async (gid: string) => ({
    ok: true,
    data: sessionsFixture(gid, [`${gid}会话`]),
  }));
});

it('loads sessions for every expanded group, keyed by groupId (multi-group expansion)', async () => {
  gs.loadGroupSessionsOrBcs.mockImplementation(async (gid: string) =>
    gid === 'g1'
      ? { ok: true, data: sessionsFixture('g1', ['g1一会话', 'g1二会话']) }
      : { ok: true, data: sessionsFixture('g2', ['g2会话']) },
  );
  const { result } = renderHook(() => useGroupSessions('g1', ['g1', 'g2']));
  await waitFor(() => {
    expect(result.current.sessionsByGroupId.g1).toHaveLength(2);
    expect(result.current.sessionsByGroupId.g2).toHaveLength(1);
  });
  expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g1', 'me');
  expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g2', 'me');
  expect(result.current.sessionsByGroupId.g1.map((s) => s.title)).toEqual(['g1一会话', 'g1二会话']);
  expect(result.current.sessionsByGroupId.g2.map((s) => s.title)).toEqual(['g2会话']);
});

it('expanding one more group later loads it too; re-expand reuses cache without refetch', async () => {
  const { result, rerender } = renderHook(({ expanded }: { expanded: string[] }) => useGroupSessions('g1', expanded), {
    initialProps: { expanded: ['g1'] },
  });
  await waitFor(() => expect(result.current.sessionsByGroupId.g1).toHaveLength(1));
  expect(gs.loadGroupSessionsOrBcs).not.toHaveBeenCalledWith('g2', 'me');

  // 展开 g2 → 触发 g2 加载
  rerender({ expanded: ['g1', 'g2'] });
  await waitFor(() => expect(result.current.sessionsByGroupId.g2).toHaveLength(1));
  expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g2', 'me');
  const callsAfterExpand = gs.loadGroupSessionsOrBcs.mock.calls.length;

  // 收起再展开 → 命中缓存，不再请求
  rerender({ expanded: ['g1'] });
  rerender({ expanded: ['g1', 'g2'] });
  expect(gs.loadGroupSessionsOrBcs.mock.calls.length).toBe(callsAfterExpand);
  expect(result.current.sessionsByGroupId.g2).toHaveLength(1);
});

it('loads more sessions independently for each expanded group', async () => {
  const firstPage = (gid: string) =>
    Array.from({ length: 10 }, (_, i) => ({
      sessionId: `${gid}-s${i + 1}`,
      groupId: gid,
      title: `${gid}会话${i + 1}`,
      kind: 'chat' as const,
      status: 'running' as const,
      participants: [],
      lastMessageAt: i + 1,
      createdAt: i + 1,
      favorite: false,
    }));
  gs.loadGroupSessionsOrBcs.mockImplementation(
    async (gid: string, _identityId?: string, opts?: { offset?: number; limit?: number }) => {
      if (!opts) return { ok: true, data: { items: firstPage(gid), offset: 0, limit: 10, total: 11, hasMore: true } };
      return {
        ok: true,
        data: {
          items: [
            {
              ...firstPage(gid)[9],
              sessionId: `${gid}-s10`,
            },
            {
              ...firstPage(gid)[0],
              sessionId: `${gid}-s11`,
              title: `${gid}追加会话`,
            },
          ],
          offset: opts.offset ?? 0,
          limit: opts.limit ?? 10,
          total: 11,
          hasMore: false,
        },
      };
    },
  );

  const { result } = renderHook(() => useGroupSessions('g1', ['g1', 'g2']));
  await waitFor(() => {
    expect(result.current.sessionsByGroupId.g1).toHaveLength(10);
    expect(result.current.sessionsByGroupId.g2).toHaveLength(10);
  });

  await act(async () => {
    await result.current.loadMoreSessions('g1');
    await result.current.loadMoreSessions('g2');
  });

  expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g1', 'me', { offset: 10, limit: 10 });
  expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g2', 'me', { offset: 10, limit: 10 });
  expect(result.current.sessionsByGroupId.g1).toHaveLength(11);
  expect(result.current.sessionsByGroupId.g2).toHaveLength(11);
  expect(result.current.hasMoreSessionsByGroupId).toEqual({ g1: false, g2: false });
});

it('openSession switches selected group first, then selects session (chat pane follows)', async () => {
  const { result } = renderHook(() => useGroupSessions('g1', ['g1', 'g2']));
  await waitFor(() => expect(result.current.sessionsByGroupId.g2).toHaveLength(1));

  act(() => result.current.openSession('g2', 'g2-s1'));
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g2');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('g2-s1');
});

it('createSessionIn creates in the given group and selects group + session', async () => {
  gs.loadGroupSessionsOrBcs.mockImplementation(async (gid: string) => ({ ok: true, data: sessionsFixture(gid, []) }));
  ss.createNewSession.mockResolvedValue({
    ok: true,
    data: {
      sessionId: 'g2-s9',
      groupId: 'g2',
      title: '跨群新建',
      kind: 'chat',
      status: 'running',
      participants: [],
      lastMessageAt: 9,
      createdAt: 9,
      favorite: false,
    },
  });
  const { result } = renderHook(() => useGroupSessions('g1', ['g1', 'g2']));
  await waitFor(() => expect(result.current.sessionsByGroupId.g2).toHaveLength(0));

  const created = await act(async () => result.current.createSessionIn('g2', '跨群新建'));
  expect(ss.createNewSession).toHaveBeenCalledWith('g2', '跨群新建', undefined);
  expect(created?.sessionId).toBe('g2-s9');
  expect(result.current.sessionsByGroupId.g2.map((s) => s.sessionId)).toContain('g2-s9');
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g2');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('g2-s9');
});

// 回归：选中群的会话请求在途时切换到另一群，旧群请求被 cancel；旧群仍展开时其会话列表
// 必须最终回填，不能因 inFlight 残留或 cancelled 早退而常驻骨架、且无请求在途。
it('cancelled selected-group fetch still populates the (expanded) group sessions', async () => {
  let resolveFirstG1: ((v: { ok: true; data: SessionView[] }) => void) | undefined;
  let firstG1Requested = false;
  gs.loadGroupSessionsOrBcs.mockImplementation(async (gid: string) => {
    if (gid === 'g1' && !firstG1Requested) {
      firstG1Requested = true;
      return new Promise<{ ok: true; data: SessionView[] }>((resolve) => {
        resolveFirstG1 = resolve;
      });
    }
    return { ok: true, data: sessionsFixture(gid, [`${gid}会话`]) };
  });

  const { result, rerender } = renderHook(({ gid }: { gid: string }) => useGroupSessions(gid, ['g1', 'g2']), {
    initialProps: { gid: 'g1' },
  });
  // g1 的首次（选中）请求在途，未 resolve。
  await waitFor(() => expect(firstG1Requested).toBe(true));
  // 切到 g2：g1 的选中请求被 cancel，g2 正常加载。
  rerender({ gid: 'g2' });
  await waitFor(() => expect(result.current.sessionsByGroupId.g2).toHaveLength(1));
  // 此时 g1 会话尚未回填。
  expect(result.current.sessionsByGroupId.g1).toBeUndefined();
  // g1 的首次请求终于 settle——即便已被 cancel，也应把数据回填到 g1（旧群仍展开）。
  await act(async () => {
    resolveFirstG1?.({ ok: true, data: sessionsFixture('g1', ['g1会话']) });
    await Promise.resolve();
  });
  await waitFor(() => expect(result.current.sessionsByGroupId.g1).toHaveLength(1));
  expect(result.current.sessionsByGroupId.g1.map((s) => s.title)).toEqual(['g1会话']);
});
