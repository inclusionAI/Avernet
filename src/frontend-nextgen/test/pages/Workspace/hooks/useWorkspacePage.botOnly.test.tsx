/** @jest-environment jsdom */
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';
import { useSearchParams } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: { replace: jest.fn() },
  useSearchParams: jest.fn(),
}));
jest.mock('@/services/workspace/sessionService', () => ({
  sessionService: { getSessionDetail: jest.fn() },
}));

const mockedUseSearchParams = useSearchParams as jest.MockedFunction<typeof useSearchParams>;

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('tab=chat&bot=bot-1%3A2088'),
    jest.fn(),
  ] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({
    identities: [
      { id: 'human_2088', kind: 'user', displayName: '我', online: true },
      { id: 'bot_old:2088', kind: 'bot', displayName: '旧 Bot', online: true },
    ],
    activeIdentityId: 'bot_old:2088',
    view: 'group',
    selectedBotSessionId: 'old-session',
  });
});

it('bot-only 单聊 URL 恢复用户身份并展开对应 Bot', async () => {
  renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  const state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBe('human_2088');
  expect(state.view).toBe('chat');
  expect(state.expandedBotIds).toEqual({ 'bot-1:2088': true });
  expect(state.expandedBotSectionKey['bot-1:2088']).toBe('mine');
  expect(state.selectedBotSessionId).toBeNull();
  expect(sessionService.getSessionDetail).not.toHaveBeenCalled();
});

it('协作群外链 session= 仍会把身份切回用户并选中群/会话（保留邀请/外链直达行为）', async () => {
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('tab=group&group=g1&session=s1'),
    jest.fn(),
  ] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({
    identities: [
      { id: 'human_2088', kind: 'user', displayName: '我', online: true },
      { id: 'bot_old:2088', kind: 'bot', displayName: '旧 Bot', online: true },
    ],
    activeIdentityId: 'bot_old:2088',
    view: 'group',
    selectedBotSessionId: null,
    selectedGroupId: null,
    selectedSessionId: null,
  });

  renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  const state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBe('human_2088');
  expect(state.view).toBe('group');
  expect(state.selectedGroupId).toBe('g1');
  expect(state.selectedSessionId).toBe('s1');
});

it('挂载无 session= 时，Bot 身份不因协作群视图被切回用户', async () => {
  mockedUseSearchParams.mockReturnValue([new URLSearchParams('tab=group'), jest.fn()] as unknown as ReturnType<
    typeof useSearchParams
  >);
  useWorkspaceStore.setState({
    identities: [
      { id: 'human_2088', kind: 'user', displayName: '我', online: true },
      { id: 'bot_old:2088', kind: 'bot', displayName: '旧 Bot', online: true },
    ],
    activeIdentityId: 'bot_old:2088',
    view: 'group',
    selectedBotSessionId: null,
    selectedGroupId: null,
    selectedSessionId: null,
  });

  renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  const state = useWorkspaceStore.getState();
  // 挂载时无外链 session=，不应触发身份切回用户；Bot 身份保持。
  expect(state.activeIdentityId).toBe('bot_old:2088');
  expect(sessionService.getSessionDetail).not.toHaveBeenCalled();
});

it('用户先选 Bot 身份再点击协作群（内部产生 session=）不应把身份切回用户', async () => {
  // 用 live URLSearchParams + 透传 setter 模拟 store→URL 往返：点击协作群后由 Store→URL effect 写回 group=/session=。
  const liveParams = new URLSearchParams('tab=group');
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
    identities: [
      { id: 'human_2088', kind: 'user', displayName: '我', online: true },
      { id: 'bot_old:2088', kind: 'bot', displayName: '旧 Bot', online: true },
    ],
    activeIdentityId: 'bot_old:2088',
    view: 'group',
    selectedBotSessionId: null,
    selectedGroupId: null,
    selectedSessionId: null,
  });

  const { rerender } = renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());
  // 挂载时无外链 session=，身份保持 Bot。
  expect(useWorkspaceStore.getState().activeIdentityId).toBe('bot_old:2088');

  // 模拟在 Bot 身份下点击协作群：选中群 + 自动首个会话（内部选中，非外链）。
  await act(async () => {
    useWorkspaceStore.getState().selectGroup('g1');
    useWorkspaceStore.getState().selectSession('s1');
  });
  // Store→URL effect 已把 group=/session= 写回 liveParams；触发一次重渲染让 URL→Store effect 读取新 URL。
  rerender();
  await act(async () => Promise.resolve());

  const state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBe('bot_old:2088');
  expect(state.selectedGroupId).toBe('g1');
  expect(state.selectedSessionId).toBe('s1');
});
