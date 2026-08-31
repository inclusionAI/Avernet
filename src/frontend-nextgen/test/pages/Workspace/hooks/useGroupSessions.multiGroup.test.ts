/** @jest-environment jsdom */
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

function groupFixture(groupId: string, sessionTitles: string[]) {
  return {
    groupId,
    name: groupId,
    kind: 'free_chat',
    status: 'active',
    participants: [],
    sessions: sessionTitles.map((title, i) => ({
      sessionId: `${groupId}-s${i + 1}`,
      groupId,
      title,
      kind: 'chat',
      status: 'running',
      participants: [],
      lastMessageAt: i + 1,
      createdAt: i + 1,
      favorite: false,
    })),
  };
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
  gs.loadGroupDetailOrBcs.mockImplementation(async (gid: string) => ({
    ok: true,
    data: groupFixture(gid, [`${gid}会话`]),
  }));
});

it('loads sessions for every expanded group, keyed by groupId (multi-group expansion)', async () => {
  gs.loadGroupDetailOrBcs.mockImplementation(async (gid: string) =>
    gid === 'g1'
      ? { ok: true, data: groupFixture('g1', ['g1一会话', 'g1二会话']) }
      : { ok: true, data: groupFixture('g2', ['g2会话']) },
  );
  const { result } = renderHook(() => useGroupSessions('g1', ['g1', 'g2']));
  await waitFor(() => {
    expect(result.current.sessionsByGroupId.g1).toHaveLength(2);
    expect(result.current.sessionsByGroupId.g2).toHaveLength(1);
  });
  expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledWith('g1', 'me');
  expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledWith('g2', 'me');
  expect(result.current.sessionsByGroupId.g1.map((s) => s.title)).toEqual(['g1一会话', 'g1二会话']);
  expect(result.current.sessionsByGroupId.g2.map((s) => s.title)).toEqual(['g2会话']);
});

it('expanding one more group later loads it too; re-expand reuses cache without refetch', async () => {
  const { result, rerender } = renderHook(({ expanded }: { expanded: string[] }) => useGroupSessions('g1', expanded), {
    initialProps: { expanded: ['g1'] },
  });
  await waitFor(() => expect(result.current.sessionsByGroupId.g1).toHaveLength(1));
  expect(gs.loadGroupDetailOrBcs).not.toHaveBeenCalledWith('g2', 'me');

  // 展开 g2 → 触发 g2 加载
  rerender({ expanded: ['g1', 'g2'] });
  await waitFor(() => expect(result.current.sessionsByGroupId.g2).toHaveLength(1));
  expect(gs.loadGroupDetailOrBcs).toHaveBeenCalledWith('g2', 'me');
  const callsAfterExpand = gs.loadGroupDetailOrBcs.mock.calls.length;

  // 收起再展开 → 命中缓存，不再请求
  rerender({ expanded: ['g1'] });
  rerender({ expanded: ['g1', 'g2'] });
  expect(gs.loadGroupDetailOrBcs.mock.calls.length).toBe(callsAfterExpand);
  expect(result.current.sessionsByGroupId.g2).toHaveLength(1);
});

it('openSession switches selected group first, then selects session (chat pane follows)', async () => {
  const { result } = renderHook(() => useGroupSessions('g1', ['g1', 'g2']));
  await waitFor(() => expect(result.current.sessionsByGroupId.g2).toHaveLength(1));

  act(() => result.current.openSession('g2', 'g2-s1'));
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g2');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('g2-s1');
});

it('createSessionIn creates in the given group and selects group + session', async () => {
  gs.loadGroupDetailOrBcs.mockImplementation(async (gid: string) => ({ ok: true, data: groupFixture(gid, []) }));
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
