/** @jest-environment jsdom */
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
  gs.loadGroupDetailOrBcs.mockResolvedValue({
    ok: true,
    data: {
      groupId: 'g1',
      name: 'X',
      kind: 'free_chat',
      status: 'active',
      participants: [],
      sessions: [
        {
          sessionId: 's1',
          groupId: 'g1',
          title: '一号',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 1,
          createdAt: 1,
          favorite: false,
        },
        {
          sessionId: 's2',
          groupId: 'g1',
          title: '二号',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 2,
          createdAt: 2,
          favorite: false,
        },
      ],
    },
  });
  renderHook(() => useGroupSessions('g1'));
  await waitFor(() => {
    const id = useWorkspaceStore.getState().selectedSessionId;
    expect(id === 's1' || id === 's2').toBe(true);
  });
});

it('createSession appends and selects new session', async () => {
  gs.loadGroupDetailOrBcs.mockResolvedValue({
    ok: true,
    data: {
      groupId: 'g1',
      name: 'X',
      kind: 'free_chat',
      status: 'active',
      participants: [],
      sessions: [],
    },
  });
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
  await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalled());
  const created = await act(async () => result.current.createSession('新会话'));
  expect(created).toBeTruthy();
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s9');
});

it('deleteSession rotates selection to next', async () => {
  gs.loadGroupDetailOrBcs.mockResolvedValue({
    ok: true,
    data: {
      groupId: 'g1',
      name: 'X',
      kind: 'free_chat',
      status: 'active',
      participants: [],
      sessions: [
        {
          sessionId: 's1',
          groupId: 'g1',
          title: '一号',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 1,
          createdAt: 1,
          favorite: false,
        },
        {
          sessionId: 's2',
          groupId: 'g1',
          title: '二号',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 2,
          createdAt: 2,
          favorite: false,
        },
      ],
    },
  });
  // 删除发生在选中群的数据面：store 需先有 selectedGroupId（selectGroup 会清空 selectedSessionId，先调）。
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('s1');
  ss.deleteSession.mockResolvedValue({ ok: true, data: null });
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalled());
  await act(async () => {
    await result.current.deleteSession('s1');
  });
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s2');
});

it('toggleFavorite calls backend service and updates session map', async () => {
  gs.loadGroupDetailOrBcs.mockResolvedValue({
    ok: true,
    data: {
      groupId: 'g1',
      name: 'X',
      kind: 'free_chat',
      status: 'active',
      participants: [],
      sessions: [
        {
          sessionId: 's1',
          groupId: 'g1',
          title: 'A',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 1,
          createdAt: 1,
          favorite: false,
        },
      ],
    },
  });
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalled());
  await act(async () => {
    await result.current.toggleFavorite('s1');
  });
  expect(ss.setFavorite).toHaveBeenCalledWith('me', 's1', true);
  expect(result.current.favoriteSessionIds).toEqual(['s1']);
});

it('exposes favoriteSessionIds from session collected state and does not tab-filter sessions', async () => {
  // getVisibleSessions 透传：若仍按 tab 过滤会丢会话，这里验证不再 tab 过滤。
  gs.loadGroupDetailOrBcs.mockResolvedValue({
    ok: true,
    data: {
      groupId: 'g1',
      name: 'X',
      kind: 'free_chat',
      status: 'active',
      participants: [],
      sessions: [
        {
          sessionId: 's1',
          groupId: 'g1',
          title: '一号',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 1,
          createdAt: 1,
          favorite: true,
        },
        {
          sessionId: 's2',
          groupId: 'g1',
          title: '二号',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 2,
          createdAt: 2,
          favorite: false,
        },
      ],
    },
  });
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalled());
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
  gs.loadGroupDetailOrBcs.mockResolvedValue({
    ok: true,
    data: {
      groupId: 'g1',
      name: 'X',
      kind: 'free_chat',
      status: 'active',
      participants: [],
      sessions: [
        {
          sessionId: 's1',
          groupId: 'g1',
          title: 'A',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 1,
          createdAt: 1,
          favorite: false,
        },
        {
          sessionId: 's2',
          groupId: 'g1',
          title: 'B',
          kind: 'chat',
          status: 'running',
          participants: [],
          lastMessageAt: 2,
          createdAt: 2,
          favorite: false,
        },
      ],
    },
  });
  ss.leaveSession.mockResolvedValue({
    ok: true,
    data: {
      sessionId: 's1',
      groupId: 'g1',
      title: 'A',
      kind: 'chat',
      status: 'running',
      participants: [],
      lastMessageAt: 1,
      createdAt: 1,
      favorite: false,
    },
  });
  useWorkspaceStore.getState().selectGroup('g1');
  useWorkspaceStore.getState().selectSession('s1');
  const { result } = renderHook(() => useGroupSessions('g1'));
  await waitFor(() => expect(gs.loadGroupDetailOrBcs).toHaveBeenCalled());
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
