/** @jest-environment jsdom */
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import { sessionService } from '@/services/workspace/sessionService';
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
const mockedGetSessionDetail = sessionService.getSessionDetail as jest.MockedFunction<
  typeof sessionService.getSessionDetail
>;

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
  mockedGetSessionDetail.mockResolvedValue({
    ok: false,
    error: { code: 'NOT_FOUND', friendlyMessage: 'not found', canRetry: false },
  });
});

it('裸 URL 重挂载：群视图记忆不丢，并投影回规范 URL', async () => {
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
  expect(s.selectedGroupId).toBe('g1');
  expect(s.selectedSessionId).toBe('s1');
  // Store→URL 投影：把记忆的选中态写回可分享 URL（恒为协作群群投影，无身份时省略 current=）。
  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('group=g1'));
  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('session=s1'));
});

it('Bot 身份的群视图记忆投影到 URL 时携带 current 身份参数', async () => {
  useWorkspaceStore.setState({
    identities: [userIdentity, { id: 'b1', kind: 'bot' as const, displayName: 'Bot 1', online: true }],
    activeIdentityId: 'b1',
    view: 'group',
    selectedGroupId: 'g1',
    selectedSessionId: 's1',
    expandedGroupIds: { g1: true },
  });

  mountWithParams('');
  await act(async () => Promise.resolve());

  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('current=b1'));
  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('group=g1'));
  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('session=s1'));
});

it('同一协作群页内 URL 变化时同步身份、选中态和 membership', async () => {
  let params = new URLSearchParams('current=u1&group=g0&session=s0');
  mockedUseSearchParams.mockImplementation(() => [params, jest.fn()] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({
    identities: [userIdentity, { id: 'b1', kind: 'bot' as const, displayName: 'Bot 1', online: true }],
    activeIdentityId: 'u1',
    view: 'group',
  });

  const { rerender } = renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  params = new URLSearchParams('current=b1&group=g1&session=gs1&membership=session_only');
  rerender();
  await act(async () => Promise.resolve());

  expect(useWorkspaceStore.getState()).toMatchObject({
    activeIdentityId: 'b1',
    view: 'group',
    selectedGroupId: 'g1',
    selectedSessionId: 'gs1',
    membership: 'session_only',
  });
});

it('群聊 URL 投影保留 membership 视角', async () => {
  useWorkspaceStore.setState({
    identities: [userIdentity],
    activeIdentityId: 'u1',
    view: 'group',
    selectedGroupId: 'g1',
    selectedSessionId: 's1',
    membership: 'session_only',
  });

  mountWithParams('');
  await act(async () => Promise.resolve());

  expect(mockedReplace).toHaveBeenCalledWith(expect.stringContaining('membership=session_only'));
});

it('冷启动外链 session=：identities 就绪后才执行一次性回填并切回用户身份', async () => {
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('group=g1&session=s1'),
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
  expect(s.selectedSessionId).toBe('s1');
});
