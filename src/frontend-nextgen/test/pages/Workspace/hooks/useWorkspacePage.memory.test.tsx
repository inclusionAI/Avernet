/** @jest-environment jsdom */
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';
import { history, useSearchParams } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: { replace: jest.fn() },
  useSearchParams: jest.fn(),
}));
jest.mock('@/services/workspace/sessionService', () => ({
  sessionService: { getSessionDetail: jest.fn() },
}));

const mockedUseSearchParams = useSearchParams as jest.MockedFunction<typeof useSearchParams>;
const mockedReplace = history.replace as jest.MockedFunction<typeof history.replace>;

function mountWithParams(search: string) {
  mockedUseSearchParams.mockReturnValue([new URLSearchParams(search), jest.fn()] as unknown as ReturnType<
    typeof useSearchParams
  >);
  return renderHook(() => useWorkspacePage());
}

const userIdentity = { id: 'u1', kind: 'user' as const, displayName: '我', online: true };

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
});

it('裸 URL 重挂载：群视图记忆不被强制回 chat，并投影回 URL', async () => {
  // 模拟切走再切回的 warm store：此前停在群聊 g1/s1。
  useWorkspaceStore.setState({
    identities: [userIdentity],
    activeIdentityId: 'u1',
    view: 'group',
    selectedGroupId: 'g1',
    selectedSessionId: 's1',
    expandedGroupIds: { g1: true },
  });

  mountWithParams('');
  await act(async () => Promise.resolve());

  const s = useWorkspaceStore.getState();
  expect(s.view).toBe('group');
  expect(s.selectedGroupId).toBe('g1');
  expect(s.selectedSessionId).toBe('s1');
  // Store→URL 投影：把记忆的选中态写回可分享 URL。
  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('tab=group'));
  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('group=g1'));
  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('session=s1'));
});

it('裸 URL 冷启动：保持默认 chat 视图', async () => {
  mountWithParams('');
  await act(async () => Promise.resolve());
  expect(useWorkspaceStore.getState().view).toBe('chat');
});

it('URL 显式 tab=chat：URL 优先于 store 的 group 记忆', async () => {
  useWorkspaceStore.setState({
    identities: [userIdentity],
    activeIdentityId: 'u1',
    view: 'group',
    selectedGroupId: 'g1',
  });
  mountWithParams('tab=chat');
  await act(async () => Promise.resolve());
  expect(useWorkspaceStore.getState().view).toBe('chat');
});

it('chat→group 视图切换：记忆的群/会话选中不被误清（无 ping-pong）', async () => {
  const liveParams = new URLSearchParams('tab=chat');
  const setParams = jest.fn((next: unknown) => {
    const np = typeof next === 'string' ? new URLSearchParams(next) : new URLSearchParams(String(next));
    ['tab', 'group', 'session', 'bot'].forEach((k) => liveParams.delete(k));
    np.forEach((v, k) => liveParams.set(k, v));
  });
  mockedUseSearchParams.mockReturnValue([
    liveParams,
    setParams as unknown as ReturnType<typeof useSearchParams>[1],
  ] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({
    identities: [userIdentity],
    activeIdentityId: 'u1',
    view: 'chat',
    selectedGroupId: 'g1',
    selectedSessionId: 's1',
    expandedGroupIds: { g1: true },
  });

  const { result, rerender } = renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  // 用户在页内把视图切回协作群：URL 变为 tab=group（无 group 参数）。
  act(() => result.current.setView('group'));
  rerender();
  await act(async () => Promise.resolve());

  const s = useWorkspaceStore.getState();
  expect(s.view).toBe('group');
  // 旧代码在此命中「tab=group 且无 group 参数」分支 selectGroup(null)，选中被清掉。
  expect(s.selectedGroupId).toBe('g1');
  expect(s.selectedSessionId).toBe('s1');
});

it('冷启动外链 session=：identities 就绪后才执行一次性回填并切回用户身份', async () => {
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('tab=group&group=g1&session=s1'),
    jest.fn(),
  ] as unknown as ReturnType<typeof useSearchParams>);
  // 冷启动：identities 尚未由 initWorkspace（异步）填充。
  useWorkspaceStore.setState({ identities: [], activeIdentityId: null });

  const { rerender } = renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  // identities 未就绪：选中态由通用回填分支先行写入属预期。
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g1');

  // initWorkspace 完成：持久化默认身份是 bot（模拟被外链要求切回用户的场景）。
  act(() => {
    useWorkspaceStore.setState({
      identities: [userIdentity, { id: 'b1', kind: 'bot' as const, displayName: 'B', online: true }],
      activeIdentityId: 'b1',
    });
  });
  rerender();
  await act(async () => Promise.resolve());

  const s = useWorkspaceStore.getState();
  // 旧代码在 identities 为空时就消耗了 isFirstUrlSyncRef，此处仍停留在 bot 身份。
  expect(s.activeIdentityId).toBe('u1');
  expect(s.view).toBe('group');
  expect(s.selectedSessionId).toBe('s1');
});
