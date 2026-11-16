/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useGroupChatHeaderPanels } from '@/pages/Workspace/hooks/useGroupChatHeaderPanels';
import { resetTaskPanelRuntime, peekTaskPanelController } from '@/services/tasks/panel/taskPanelRuntime';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { listSessionMessages } from '@/services/backendApi/collaboration/sessionController';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { createGroupTaskHistoryReader } from '@/services/tasks/panel/taskPanelHistoryAdapter';
import type { SessionView } from '@/domain/collaboration';
import type { PanelHandle } from '@tc-chat/core';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/capabilities', () => ({ getCapabilities: () => ({
  ...require('@/capabilities/defaultCapabilities').defaultCapabilities,
  getHumanIdentity: () => ({ status: 'available', value: { userId: 'viewer' } }),
}) }));
const mockLoadUI = jest.fn();
jest.mock('@/services/tasks/panel/taskPanelAssetLoader', () => ({ loadTaskPanelAsset: () => mockLoadUI() }));
jest.mock('@/services/tasks/panel/taskPanelRecoveryLoader', () => ({
  loadTaskPanelRecoveryAsset: async () => require('@task-panel-test/controller/createTaskPanelController'),
}));
jest.mock('@/services/tasks/panel/taskPanelArtifactAdapter', () => ({ taskPanelArtifacts: {} }));
jest.mock('@/services/workspace/chatBridge', () => ({ chatBridge: { openPanelTab: jest.fn() } }));

const sessionId = 'bcs_grp_bot_only';
const originalFetch = global.fetch;
const historyRequests: URL[] = [];
const historySignals: (AbortSignal | null | undefined)[] = [];
let allowedActors: Set<string>;
function response(data: unknown, status = 200): Response {
  return { ok: status < 400, status, headers: { get: () => 'application/json' },
    json: async () => data, text: async () => JSON.stringify(data) } as unknown as Response;
}
function options() {
  const handle: PanelHandle = {
    openTab: jest.fn(), closeTab: jest.fn(), updateTab: jest.fn(), focusTab: jest.fn(),
    openPanel: jest.fn(), closePanel: jest.fn(), closePanelForce: jest.fn(),
    flushContext: jest.fn(() => ({ interactions: [], panelState: { tabs: [], activeTabId: null } })),
    flushInteractions: jest.fn(() => []),
  };
  return { session: { sessionId } as SessionView, userId: 'viewer', messages: [],
    panelRef: { current: handle }, toggleFavorite: async () => {} };
}
beforeEach(() => {
  mockLoadUI.mockClear();
  resetTaskPanelRuntime(); useErrorNotifyStore.getState().reset(); historyRequests.length = 0; historySignals.length = 0;
  allowedActors = new Set(['bot-owned']);
  useWorkspaceStore.setState({ activeIdentityId: 'bot-owned' });
  global.fetch = jest.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/messages')) {
      historyRequests.push(url); historySignals.push(init?.signal);
      if (!allowedActors.has(url.searchParams.get('view_bot_id') ?? '')) return response({
        message: 'The selected View Actor is not a Session Participant',
      }, 403);
      return response({ code: 200000, data: [] });
    }
    return response({ code: 200000, data: { items: [] } });
  }) as typeof fetch;
});
afterEach(() => { resetTaskPanelRuntime(); global.fetch = originalFetch; });
it('Bot 视角恢复普通纯 Bot 群时沿用查看身份，不要求 Human 加入会话', async () => {
  const props = options();
  renderHook(() => useGroupChatHeaderPanels(props));
  await waitFor(() => expect(peekTaskPanelController()?.getSnapshot().recovery.status).toBe('not-found'));
  expect(mockLoadUI).not.toHaveBeenCalled();
  expect(historyRequests).toHaveLength(1);
  expect(historyRequests[0].searchParams.get('view_bot_id')).toBe('bot-owned');
  expect(useErrorNotifyStore.getState().queue.filter(item => !item.cancelled)).toEqual([]);
});

it('同一会话切换 Bot/Human 视角后重新恢复，不复用上个视角的完成状态', async () => {
  const props = options();
  const { rerender } = renderHook(() => useGroupChatHeaderPanels(props));
  await waitFor(() => expect(peekTaskPanelController()?.getSnapshot().recovery.status).toBe('not-found'));
  act(() => useWorkspaceStore.setState({ activeIdentityId: 'human_viewer' }));
  rerender();
  await waitFor(() => expect(historyRequests).toHaveLength(2));
  expect(historyRequests[1].searchParams.get('view_bot_id')).toBe('human_viewer');
  await waitFor(() => expect(peekTaskPanelController()?.getSnapshot().recovery.status).toBe('error'));
  expect(useErrorNotifyStore.getState().queue.filter(item => !item.cancelled)).toEqual([]);
});


it('Human 是参与者时使用 Human 视角，未选中查看身份时不发扫描请求', async () => {
  allowedActors = new Set(['human_viewer']);
  useWorkspaceStore.setState({ activeIdentityId: null });
  const props = options();
  renderHook(() => useGroupChatHeaderPanels(props));
  await act(async () => { await Promise.resolve(); });
  expect(global.fetch).not.toHaveBeenCalled();
  act(() => useWorkspaceStore.setState({ activeIdentityId: 'human_viewer' }));
  await waitFor(() => expect(peekTaskPanelController()?.getSnapshot().recovery.status).toBe('not-found'));
  expect(historyRequests[0].searchParams.get('view_bot_id')).toBe('human_viewer');
});

it('切换视角会中止旧扫描，旧任务卡片迟到时不能创建 Tab', async () => {
  const fetchPage = global.fetch;
  let finish!: (response: Response) => void;
  let oldSignal: AbortSignal | null | undefined;
  global.fetch = jest.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/messages') && url.searchParams.get('view_bot_id') === 'bot-owned') {
      oldSignal = init?.signal;
      return new Promise<Response>(resolve => { finish = resolve; });
    }
    return fetchPage(input, init);
  }) as typeof fetch;
  const props = options();
  renderHook(() => useGroupChatHeaderPanels(props));
  await waitFor(() => expect(finish).toEqual(expect.any(Function)));
  allowedActors.add('bot-second');
  act(() => useWorkspaceStore.setState({ activeIdentityId: 'bot-second' }));
  await waitFor(() => expect(peekTaskPanelController()?.getSnapshot().recovery.status).toBe('not-found'));
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => { finish(response({ code: 200000, data: [{ id: 'old', timestamp: 1,
    content: `<AixUI type="panel" component="taskPanel.TaskLoopView" params='{"taskId":"old-view-task"}'/>`,
  }] })); });
  expect(props.panelRef.current.openTab).not.toHaveBeenCalled();
  expect(peekTaskPanelController()?.getSnapshot().activeTaskId).toBeNull();
});

it('Bot 视角的历史卡片仍可恢复多个任务 Tab，顺序和默认焦点不变', async () => {
  const fetchPage = global.fetch;
  global.fetch = jest.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/messages')) return response({ code: 200000, data: [
      { id: 'new', timestamp: 2, content: `<AixUI type="panel" component="taskPanel.TaskLoopView" params='{"taskId":"task-new"}'/>` },
      { id: 'old', timestamp: 1, content: `<AixUI type="panel" component="taskPanel.TaskLoopView" params='{"taskId":"task-old"}'/>` },
    ] });
    return fetchPage(input, init);
  }) as typeof fetch;
  const props = options();
  renderHook(() => useGroupChatHeaderPanels(props));
  await waitFor(() => expect(props.panelRef.current.openTab).toHaveBeenCalledTimes(2));
  expect(jest.mocked(props.panelRef.current.openTab).mock.calls.map(([tab]) => tab.params?.taskId)).toEqual(['task-old', 'task-new']);
  expect(props.panelRef.current.focusTab).toHaveBeenLastCalledWith('task-task-new');
});

it('清空查看身份也中止扫描，不能默认退回 Human 视角', async () => {
  const fetchPage = global.fetch;
  let signal: AbortSignal | null | undefined;
  global.fetch = jest.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/messages')) {
      signal = init?.signal;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener('abort', () => reject(signal?.reason), { once: true });
      });
    }
    return fetchPage(input, init);
  }) as typeof fetch;
  const props = options();
  renderHook(() => useGroupChatHeaderPanels(props));
  await waitFor(() => expect(signal).toBeDefined());
  act(() => useWorkspaceStore.setState({ activeIdentityId: null }));
  expect(signal?.aborted).toBe(true);
  await act(async () => { await Promise.resolve(); });
  expect(peekTaskPanelController()?.getSnapshot().conversation).toBeNull();
  expect(props.panelRef.current.openTab).not.toHaveBeenCalled();
});

it('任务扫描的局部错误处理不屏蔽主群聊消息请求的权限错误', async () => {
  useWorkspaceStore.setState({ activeIdentityId: 'human_viewer' });
  const props = options();
  renderHook(() => useGroupChatHeaderPanels(props));
  await waitFor(() => expect(peekTaskPanelController()?.getSnapshot().recovery.status).toBe('error'));
  expect(useErrorNotifyStore.getState().queue.filter(item => !item.cancelled)).toEqual([]);
  await expect(listSessionMessages(sessionId, { limit: 50, view_bot_id: 'human_viewer', include_pending: false }))
    .rejects.toMatchObject({ status: 403 });
  expect(useErrorNotifyStore.getState().queue.filter(item => !item.cancelled)).toHaveLength(1);
});

it('任务历史扫描遇到真实登录失效时仍登记统一重登录信号', async () => {
  useLoginRedirectStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
  global.fetch = jest.fn(async () => response({ actionType: 'LOGIN', buserviceErrorCode: 'USER_NOT_LOGIN',
    decisionBy: 'ACE', buserviceErrorMsg: 'https://login.example.com/pubLogin',
  })) as typeof fetch;
  try {
    await expect(createGroupTaskHistoryReader('bot-owned').readPage(
      { sessionId, kind: 'group' }, null, new AbortController().signal,
    )).rejects.toMatchObject({ name: 'AceLoginRedirectError' });
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'redirect', url: 'https://login.example.com/pubLogin' });
  } finally { useLoginRedirectStore.getState().reset(); }
});

it('HTTP 200 中的权限失败信封不能当作无任务的空历史', async () => {
  global.fetch = jest.fn(async () => response({ code: 40300, message: 'The selected View Actor is not a Session Participant' })) as typeof fetch;
  await expect(createGroupTaskHistoryReader('bot-owned').readPage(
    { sessionId, kind: 'group' }, null, new AbortController().signal,
  )).rejects.toThrow('The selected View Actor is not a Session Participant');
});
