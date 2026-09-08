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

it('已展开的好友 bot：URL 回写触发回填时不得把分区归属覆盖成 mine（否则好友行折叠、会话列表不可见）', async () => {
  // 复现链路：点击好友 bot（分区 'friend'）→ 自动选中首会话 → useChatUrlSync 写回 ?bot=&session=
  // → URL→Store effect 因 botParam 变化重跑。旧实现无条件 setBotExpandedSection(bot,'mine')
  // → 好友分区 expanded 判定（需 ==='friend'）失败 → 行折叠、已加载的会话列表消失。
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('tab=chat&bot=fr:9&session=s1'),
    jest.fn(),
  ] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({
    identities: [{ id: 'human_2088', kind: 'user', displayName: '我', online: true }],
    activeIdentityId: 'human_2088',
    view: 'chat',
    expandedBotIds: { 'fr:9': true },
    expandedBotSectionKey: { 'fr:9': 'friend' },
  });

  const { rerender } = renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());
  // 模拟 URL 回写后的重渲染（searchParams 变化 → effect 重跑）。
  rerender();
  await act(async () => Promise.resolve());

  const state = useWorkspaceStore.getState();
  expect(state.expandedBotIds['fr:9']).toBe(true);
  expect(state.expandedBotSectionKey['fr:9']).toBe('friend');
  expect(state.selectedBotSessionId).toBe('s1');
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

it('target=_blank 新开页面:身份滞后加载时,协作群外链仍能切回人类身份并选中群/会话', async () => {
  // 模拟全新标签页挂载:身份尚未加载(initWorkspace 未返回),activeIdentityId 为空。
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('tab=group&group=g1&session=s1'),
    jest.fn(),
  ] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({
    identities: [],
    activeIdentityId: null,
    view: 'group',
    selectedGroupId: null,
    selectedSessionId: null,
  });

  const { rerender } = renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  // 挂载首轮:身份未就绪 → 不应消费首次同步、也不应切到 Bot 身份。
  let state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBeNull();
  // 群/会话选中已由 URL→Store 回填(groupParam/sessionParam)先行落地。
  expect(state.selectedGroupId).toBe('g1');
  expect(state.selectedSessionId).toBe('s1');

  // 模拟 initWorkspace 回填:identities 到位,且持久化默认身份是上次用的 Bot。
  await act(async () => {
    useWorkspaceStore.setState({
      identities: [
        { id: 'human_2088', kind: 'user', displayName: '我', online: true },
        { id: 'bot_old:2088', kind: 'bot', displayName: '旧 Bot', online: true },
      ],
      activeIdentityId: 'bot_old:2088',
    });
  });
  // store 变更触发重渲染,使 URL→Store effect 读取新 identities 后重跑首次同步。
  rerender();
  await act(async () => Promise.resolve());

  // 首次同步恢复:身份切回人类(非持久化的 Bot),群/会话保持选中。
  state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBe('human_2088');
  expect(state.view).toBe('group');
  expect(state.selectedGroupId).toBe('g1');
  expect(state.selectedSessionId).toBe('s1');
});

it('群深链带 bot= 且命中 Bot 身份：以该 Bot 身份打开群/会话（不强制切回用户）', async () => {
  // BCN 外链落地透传 bot={bot_uuid}：指定视角身份。持久化身份是人类时也应切到该 Bot。
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('tab=group&group=g1&session=s1&bot=bot_old:2088&membership=session_only'),
    jest.fn(),
  ] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({ activeIdentityId: 'human_2088', view: 'chat' });

  renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  const state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBe('bot_old:2088');
  expect(state.view).toBe('group');
  expect(state.selectedGroupId).toBe('g1');
  expect(state.selectedSessionId).toBe('s1');
  expect(state.membership).toBe('session_only');
  expect(sessionService.getSessionDetail).not.toHaveBeenCalled();
});

it('群深链带 bot= 但未命中任何身份：退回用户身份打开（等价旧行为）', async () => {
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('tab=group&group=g1&session=s1&bot=stranger_bot&membership=direct'),
    jest.fn(),
  ] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({ activeIdentityId: 'bot_old:2088', view: 'chat' });

  renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  const state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBe('human_2088');
  expect(state.view).toBe('group');
  expect(state.selectedGroupId).toBe('g1');
  expect(state.selectedSessionId).toBe('s1');
});
