/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { useGroupSessions } from '@/pages/Workspace/hooks/useGroupSessions';
import { groupService } from '@/services/workspace/groupService';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// auto-mock pattern (bare jest.mock): factory form with jest.fn() under
// @jest/globals triggers a TDZ bug, so we use auto-mock + per-test setup.
jest.mock('@/services/workspace/sessionService');
jest.mock('@/services/workspace/groupService');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ss = sessionService as unknown as Record<string, jest.Mock<any>>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const gs = groupService as unknown as Record<string, jest.Mock<any>>;

function session(sessionId: string, groupId: string, title: string, favorite = false): SessionView {
  return {
    sessionId,
    groupId,
    title,
    kind: 'chat',
    status: 'running',
    participants: [],
    lastMessageAt: 1,
    createdAt: 1,
    favorite,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({ activeIdentityId: 'me' });
  // Defaults: getVisibleSessions 透传；listFavorites 空；toggleFavorite 返回 true。
  ss.getVisibleSessions.mockImplementation((xs: unknown[]) => xs);
  ss.setFavorite.mockResolvedValue({ ok: true, data: true });
  ss.createNewSession.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  ss.renameSession.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  ss.deleteSession.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  ss.leaveSession.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  ss.getSessionDetail.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  ss.updateMemberMode.mockResolvedValue({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } });
  // map 工具是纯函数：绑真实实现，避免 auto-mock 返回 undefined。
  const { sessionService: realSessionService } = jest.requireActual('@/services/workspace/sessionService') as {
    sessionService: typeof sessionService;
  };
  ss.renameInMap.mockImplementation(realSessionService.renameInMap as never);
  ss.removeFromMap.mockImplementation(realSessionService.removeFromMap as never);
});

it('auto-selects first session after load', async () => {
  gs.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: [session('s1', 'g1', '一号'), session('s2', 'g1', '二号')],
  });
  renderHook(() => useGroupSessions('g1'));
  await waitFor(() => {
    const id = useWorkspaceStore.getState().selectedSessionId;
    expect(id === 's1' || id === 's2').toBe(true);
  });
});

it('waits for an active identity and loads selected sessions once', async () => {
  useWorkspaceStore.setState({ activeIdentityId: null });
  gs.loadGroupSessionsOrBcs.mockResolvedValue({ ok: true, data: [] });

  renderHook(() => useGroupSessions('g1'));
  await act(async () => Promise.resolve());
  expect(gs.loadGroupSessionsOrBcs).not.toHaveBeenCalled();

  act(() => {
    useWorkspaceStore.setState({ activeIdentityId: 'me' });
  });
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledTimes(1));
  expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g1', 'me');
});

it('loads the first 10 sessions and appends the next page without duplicates', async () => {
  const firstPage = Array.from({ length: 10 }, (_, i) => session(`s${i + 1}`, 'g1', `会话${i + 1}`));
  gs.loadGroupSessionsOrBcs.mockImplementation(
    async (_gid: string, _identityId?: string, opts?: { offset?: number; limit?: number }) =>
      opts
        ? {
            ok: true,
            data: {
              items: [session('s10', 'g1', '会话10'), session('s11', 'g1', '会话11'), session('s12', 'g1', '会话12')],
              offset: opts.offset ?? 0,
              limit: opts.limit ?? 10,
              total: 12,
              hasMore: false,
            },
          }
        : { ok: true, data: { items: firstPage, offset: 0, limit: 10, total: 12, hasMore: true } },
  );

  const { result } = renderHook(() => useGroupSessions('g1', ['g1']));
  await waitFor(() => expect(result.current.sessions).toHaveLength(10));
  expect(result.current.hasMoreSessionsByGroupId.g1).toBe(true);

  await act(async () => result.current.loadMoreSessions('g1'));

  expect(gs.loadGroupSessionsOrBcs).toHaveBeenLastCalledWith('g1', 'me', { offset: 10, limit: 10 });
  expect(result.current.sessions.map((item) => item.sessionId)).toEqual(
    Array.from({ length: 12 }, (_, i) => `s${i + 1}`),
  );
  expect(result.current.hasMoreSessionsByGroupId.g1).toBe(false);
});

it('createSession appends and selects new session', async () => {
  gs.loadGroupSessionsOrBcs.mockResolvedValue({ ok: true, data: [] });
  ss.createNewSession.mockResolvedValue({
    ok: true,
    data: {
      sessionId: 's9',
      groupId: 'g1',
      title: '新会话',
      kind: 'chat',
      status: 'running',
      participants: [],
      lastMessageAt: 9,
      createdAt: 9,
      favorite: false,
    },
  });
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalled());
  const created = await act(async () => result.current.createSession('新会话'));
  expect(created).toBeTruthy();
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s9');
});

it('deleteSession rotates selection to next', async () => {
  gs.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: [session('s1', 'g1', '一号'), session('s2', 'g1', '二号')],
  });
  // 删除发生在选中群的数据面：store 需先有 selectedGroupId（selectGroup 会清空 selectedSessionId，先调）。
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('s1');
  ss.deleteSession.mockResolvedValue({ ok: true, data: null });
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalled());
  await act(async () => {
    await result.current.deleteSession('s1');
  });
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s2');
});

it('toggleFavorite calls backend service and updates session map', async () => {
  gs.loadGroupSessionsOrBcs.mockResolvedValue({ ok: true, data: [session('s1', 'g1', 'A')] });
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalled());
  await act(async () => {
    await result.current.toggleFavorite('s1');
  });
  expect(ss.setFavorite).toHaveBeenCalledWith('me', 's1', true);
  expect(result.current.favoriteSessionIds).toEqual(['s1']);
});

it('exposes favoriteSessionIds from session collected state and does not tab-filter sessions', async () => {
  // getVisibleSessions 透传：若仍按 tab 过滤会丢会话，这里验证不再 tab 过滤。
  gs.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: [session('s1', 'g1', '一号', true), session('s2', 'g1', '二号', false)],
  });
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalled());
  // favoriteSessionIds 来源于后端 collected 字段
  await waitFor(() => expect(result.current.favoriteSessionIds).toEqual(['s1']));
  // sessions 不再按 tab 过滤：非收藏 s2 仍出现在 hook 输出中（search 为空）
  const ids = result.current.sessions.map((s) => s.sessionId);
  expect(ids).toContain('s1');
  expect(ids).toContain('s2');
  // 不再暴露 sessionTab / setSessionTab / favoritesByIdentity
  const current = result.current as unknown as Record<string, unknown>;
  expect(current.sessionTab).toBeUndefined();
  expect(current.setSessionTab).toBeUndefined();
  expect(current.favoritesByIdentity).toBeUndefined();
});

it('leaveSession removes session from list and clears selection', async () => {
  gs.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: [session('s1', 'g1', 'A'), session('s2', 'g1', 'B')],
  });
  ss.leaveSession.mockResolvedValue({
    ok: true,
    data: session('s1', 'g1', 'A'),
  });
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('s1');
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalled());
  await act(async () => {
    await result.current.leaveSession('s1', 'me');
  });
  expect(ss.leaveSession).toHaveBeenCalledWith('s1', 'me');
  // s1 should be removed from the list
  const ids = result.current.sessions.map((s) => s.sessionId);
  expect(ids).not.toContain('s1');
  expect(ids).toContain('s2');
  // Selection should rotate to the next session
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s2');
});

it('discards an in-flight load-more response after reload and clears loading state', async () => {
  const firstPage = Array.from({ length: 10 }, (_, i) => session(`s${i + 1}`, 'g1', `旧会话${i + 1}`));
  let resolveMore:
    | ((value: {
        ok: true;
        data: { items: SessionView[]; offset: number; limit: number; total: number; hasMore: boolean };
      }) => void)
    | undefined;
  let initialLoad = true;
  gs.loadGroupSessionsOrBcs.mockImplementation(
    async (_gid: string, _identityId?: string, opts?: { offset?: number; limit?: number }) => {
      if (!opts && initialLoad) {
        initialLoad = false;
        return { ok: true, data: { items: firstPage, offset: 0, limit: 10, total: 20, hasMore: true } };
      }
      if (opts) {
        return new Promise((resolve) => {
          resolveMore = resolve;
        });
      }
      return {
        ok: true,
        data: {
          items: [session('fresh-1', 'g1', '刷新后的会话')],
          offset: 0,
          limit: 10,
          total: 1,
          hasMore: false,
        },
      };
    },
  );

  const { result } = renderHook(() => useGroupSessions('g1', ['g1']));
  await waitFor(() => expect(result.current.sessions).toHaveLength(10));

  let morePromise: Promise<void> | undefined;
  act(() => {
    morePromise = result.current.loadMoreSessions('g1');
  });
  await waitFor(() =>
    expect(gs.loadGroupSessionsOrBcs).toHaveBeenLastCalledWith('g1', 'me', { offset: 10, limit: 10 }),
  );

  await act(async () => {
    await result.current.reloadSessions();
  });
  expect(result.current.sessions.map((item) => item.sessionId)).toEqual(['fresh-1']);
  expect(result.current.isLoadingMoreSessionsByGroupId.g1).toBe(false);
  resolveMore?.({
    ok: true,
    data: {
      items: [session('stale-11', 'g1', '过期追加会话')],
      offset: 10,
      limit: 10,
      total: 20,
      hasMore: false,
    },
  });
  await act(async () => {
    await morePromise;
  });
  expect(result.current.sessions.map((item) => item.sessionId)).toEqual(['fresh-1']);
  expect(result.current.isLoadingMoreSessionsByGroupId.g1).toBe(false);
});

it('does not let a previous identity request repopulate the new identity session map', async () => {
  let resolveOld: ((value: { ok: true; data: SessionView[] }) => void) | undefined;
  let resolveNew: ((value: { ok: true; data: SessionView[] }) => void) | undefined;
  gs.loadGroupSessionsOrBcs.mockImplementation(async (_gid: string, identityId?: string) => {
    if (identityId === 'me') {
      return new Promise((resolve) => {
        resolveOld = resolve;
      });
    }
    return new Promise((resolve) => {
      resolveNew = resolve;
    });
  });

  const { result, rerender } = renderHook(() => useGroupSessions('g1', ['g1']));
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g1', 'me'));
  act(() => {
    useWorkspaceStore.setState({ activeIdentityId: 'bot-1' });
  });
  rerender();
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalledWith('g1', 'bot-1'));

  await act(async () => {
    resolveOld?.({ ok: true, data: [session('old-1', 'g1', '旧身份会话')] });
    await Promise.resolve();
  });
  expect(result.current.sessionsByGroupId.g1).toBeUndefined();
  await act(async () => {
    resolveNew?.({ ok: true, data: [session('new-1', 'g1', '新身份会话')] });
    await Promise.resolve();
  });
  await waitFor(() => expect(result.current.sessions.map((item) => item.sessionId)).toEqual(['new-1']));
  expect(result.current.sessions.map((item) => item.sessionId)).not.toContain('old-1');
});

it('keeps the server offset after creating a session before loading the next page', async () => {
  const firstPage = Array.from({ length: 10 }, (_, i) => session(`s${i + 1}`, 'g1', `会话${i + 1}`));
  gs.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: { items: firstPage, offset: 0, limit: 10, total: 11, hasMore: true },
  });
  ss.createNewSession.mockResolvedValue({ ok: true, data: session('created', 'g1', '新建会话') });
  const { result } = renderHook(() => useGroupSessions('g1', ['g1']));
  await waitFor(() => expect(result.current.sessions).toHaveLength(10));

  await act(async () => {
    await result.current.createSession('新建会话');
  });
  await act(async () => {
    await result.current.loadMoreSessions('g1');
  });
  expect(gs.loadGroupSessionsOrBcs).toHaveBeenLastCalledWith('g1', 'me', { offset: 10, limit: 10 });
});

it('clears the load-more state when a concurrent reload fails', async () => {
  const firstPage = Array.from({ length: 10 }, (_, i) => session(`s${i + 1}`, 'g1', `会话${i + 1}`));
  let resolveMore: ((value: { ok: true; data: SessionView[] }) => void) | undefined;
  let initialLoad = true;
  gs.loadGroupSessionsOrBcs.mockImplementation(
    async (_gid: string, _identityId?: string, opts?: { offset?: number; limit?: number }) => {
      if (!opts && initialLoad) {
        initialLoad = false;
        return { ok: true, data: { items: firstPage, offset: 0, limit: 10, total: 20, hasMore: true } };
      }
      if (opts) {
        return new Promise((resolve) => {
          resolveMore = resolve;
        });
      }
      return { ok: false, error: { code: 'RELOAD_FAILED', friendlyMessage: '刷新失败', canRetry: true } };
    },
  );

  const { result } = renderHook(() => useGroupSessions('g1', ['g1']));
  await waitFor(() => expect(result.current.sessions).toHaveLength(10));
  let morePromise: Promise<void> | undefined;
  act(() => {
    morePromise = result.current.loadMoreSessions('g1');
  });
  await waitFor(() => expect(result.current.isLoadingMoreSessionsByGroupId.g1).toBe(true));

  await act(async () => {
    await result.current.reloadSessions();
  });
  expect(result.current.isLoadingMoreSessionsByGroupId.g1).toBe(false);
  resolveMore?.({ ok: true, data: [session('stale-11', 'g1', '过期会话')] });
  await act(async () => {
    await morePromise;
  });
  expect(result.current.isLoadingMoreSessionsByGroupId.g1).toBe(false);
});

it('记忆的选中会话已不存在：反查失败后回落到首条', async () => {
  gs.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: [session('s1', 'g1', '一号'), session('s2', 'g1', '二号')],
  });
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('gone');
  renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalled());
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledWith('gone'));
  await waitFor(() => expect(useWorkspaceStore.getState().selectedSessionId).toBe('s1'));
});

it('记忆的选中会话超出已加载分页：反查成功则前置补入并保持选中', async () => {
  gs.loadGroupSessionsOrBcs.mockResolvedValue({ ok: true, data: [session('s1', 'g1', '一号')] });
  ss.getSessionDetail.mockResolvedValue({ ok: true, data: session('s-deep', 'g1', '深处会话') });
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('s-deep');
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledWith('s-deep'));
  await waitFor(() => expect(useWorkspaceStore.getState().selectedSessionId).toBe('s-deep'));
  await waitFor(() => expect(result.current.sessions.map((s) => s.sessionId)).toContain('s-deep'));
});

it('兜底反查在途时用户已切换选中：失败回调不得劫持新选中', async () => {
  const detailResolvers: Array<
    (value: { ok: false; error: { code: string; friendlyMessage: string; canRetry: boolean } }) => void
  > = [];
  gs.loadGroupSessionsOrBcs.mockResolvedValue({
    ok: true,
    data: [session('s1', 'g1', '一号'), session('s2', 'g1', '二号')],
  });
  ss.getSessionDetail.mockImplementation(
    () =>
      new Promise((resolve) => {
        detailResolvers.push(resolve);
      }),
  );
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('gone');
  renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledWith('gone'));

  // 反查在途期间用户点选了 s2（useSessionMemberSync 也会对 s2 发起 detail 反查，一并入队）。
  act(() => {
    useWorkspaceStore.getState().selectSession('s2');
  });
  await act(async () => {
    // 排空所有在途 detail 请求：memberSync 的失败回调是 no-op，兜底的失败回调触发劫持路径。
    detailResolvers
      .splice(0)
      .forEach((resolve) => resolve({ ok: false, error: { code: 'X', friendlyMessage: 'fail', canRetry: false } }));
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s2');
});

it('身份切换后过期的兜底反查响应不得回填新身份列表', async () => {
  const detailResolvers: Array<(value: { ok: true; data: SessionView }) => void> = [];
  gs.loadGroupSessionsOrBcs.mockResolvedValue({ ok: true, data: [session('s1', 'g1', '一号')] });
  ss.getSessionDetail.mockImplementation(
    () =>
      new Promise((resolve) => {
        detailResolvers.push(resolve);
      }),
  );
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('gone');
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(ss.getSessionDetail).toHaveBeenCalledWith('gone'));

  // 反查在途时切换身份：useSessionMap 同步清缓存并递增 epoch。
  act(() => {
    useWorkspaceStore.setState({ activeIdentityId: 'other' });
  });
  await act(async () => {
    // 排空所有在途 detail 请求（memberSync 的成功回调因 participants 为空是 no-op）。
    detailResolvers
      .splice(0)
      .forEach((resolve) => resolve({ ok: true, data: session('gone', 'g1', '旧身份深处会话') }));
    await Promise.resolve();
    await Promise.resolve();
  });
  // 旧身份的会话不得出现在新身份的缓存里。
  expect(result.current.sessionsByGroupId.g1 ?? []).not.toEqual(
    expect.arrayContaining([expect.objectContaining({ sessionId: 'gone' })]),
  );
});

it('选中归属其他群（参数群滞后）时兜底不得误清选中', async () => {
  gs.loadGroupSessionsOrBcs.mockResolvedValue({ ok: true, data: [session('s1', 'g1', '一号')] });
  ss.createNewSession.mockResolvedValue({ ok: true, data: session('g2-s9', 'g2', '新会话') });
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupSessionsOrBcs).toHaveBeenCalled());
  // 跨群新建过渡帧（同 multiGroup 回归场景）：createSessionIn 更新 map 触发兜底 effect 重跑，
  // store 已选中 g2/g2-s9，但 hook 参数群仍是 g1 —— 兜底不得反查/回落清掉他群选中。
  await act(async () => {
    await result.current.createSessionIn('g2', '新会话');
  });
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('g2-s9');
});
