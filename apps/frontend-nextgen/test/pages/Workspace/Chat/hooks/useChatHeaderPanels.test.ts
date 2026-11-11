/**
 * @jest-environment jsdom
 */
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { useTaskSessionAssociationStore } from '@/stores/taskSessionAssociationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { beforeEach, expect, it, jest } from '@jest/globals';

const mockGetTaskBySession = jest.fn();
const mockListMyTasks = jest.fn();

jest.mock('@/services/myTask', () => ({
  getTaskBySession: (...args: unknown[]) => mockGetTaskBySession(...args),
  isEnvelopeFailure: (result?: { code?: number }) => result?.code !== 0,
  listMyTasks: (...args: unknown[]) => mockListMyTasks(...args),
  normalizeMyTaskPage: (data: unknown) => ({ items: Array.isArray(data) ? data : [], total: 0, page: 1, pageSize: 10 }),
}));
import { sessionHasSidePaneContent, useChatHeaderPanels } from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import type { ChatMessage, PanelHandle } from '@tc-chat/core';
import { act, renderHook, waitFor } from '@testing-library/react';

function makeSession(overrides: Partial<BotChatSessionView> = {}): BotChatSessionView {
  return {
    sessionId: 'sess-1',
    botId: 'bot-1',
    title: '会话',
    messageCount: 1,
    gmtModified: '1',
    gmtCreate: '1',
    favorite: false,
    ...overrides,
  };
}

function makeMessages(): ChatMessage[] {
  return [{ id: 'm1', role: 'user', content: '今天发布排期', status: 'history' }] as never;
}

/** 含 <AixUI type="panel"> 副屏声明的消息（判空标记命中，openTab 挂载即建 tab）。 */
const PANEL_MESSAGE = {
  id: 'm-panel',
  role: 'assistant',
  content:
    '<AixUI type="panel" component="taskPanel.TaskLoopView" ' +
    `tab='{"id":"task-1","title":"任务","closable":true}' params='{}'></AixUI>`,
  status: 'history',
} as never;

function makePanelMessages(): ChatMessage[] {
  return [...makeMessages(), PANEL_MESSAGE] as never;
}

function makeOptions(overrides: { messages?: ChatMessage[]; panelRef?: React.RefObject<PanelHandle | null>; userId?: string | null }) {
  const openTab = jest.fn();
  const closePanel = jest.fn();
  const openPanel = jest.fn();
  const handle = { openTab, closePanel, openPanel, closePanelForce: jest.fn() } as unknown as PanelHandle;
  return {
    openTab,
    closePanel,
    openPanel,
    options: {
      session: makeSession(),
      messages: overrides.messages ?? makeMessages(),
      panelRef: overrides.panelRef ?? ({ current: handle } as React.RefObject<PanelHandle | null>),
      section: 'managed' as const,
      userId: overrides.userId ?? null,
      botId: 'bot-1',
      toggleFavorite: jest.fn().mockResolvedValue(true),
      favoritePending: jest.fn().mockReturnValue(false),
      openFileDrawer: jest.fn(),
      closeFileDrawer: jest.fn(),
      fileDrawerOpen: false,
    },
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  useTaskConversationNavigationStore.getState().resetAll();
  useTaskSidePanelStore.getState().resetAll();
  useTaskSessionAssociationStore.getState().reset();
  mockGetTaskBySession.mockResolvedValue({ ok: true, data: null });
  mockListMyTasks.mockResolvedValue({ code: 0, data: [] });
  window.history.pushState({}, '', '/workspace/chat?bot=bot-1&session=sess-1');
});

it('收藏状态未知时禁用星标，后台确认后解除', () => {
  const { options } = makeOptions({});
  const { result, rerender } = renderHook(({ session }) => useChatHeaderPanels({ ...options, session }), {
    initialProps: { session: makeSession({ favorite: undefined }) },
  });
  expect(result.current.favorites.disabled).toBe(true);
  rerender({ session: makeSession({ favorite: false }) });
  expect(result.current.favorites.disabled).toBe(false);
});

it('打开已存在的单 bot 会话时 by_session 命中即恢复最新任务副屏', async () => {
  const { options, openTab } = makeOptions({ messages: makeMessages(), userId: 'u1' });
  mockGetTaskBySession.mockResolvedValue({
    ok: true,
    data: [
      { task_id: 'task-root', owner_user_id: 'u1', node_id: 'node-root', updated_at: null },
    ],
  });

  renderHook(() => useChatHeaderPanels(options));

  await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
  expect(mockGetTaskBySession).toHaveBeenCalledWith('sess-1');
  expect(mockListMyTasks).toHaveBeenCalledWith({ user_id: 'u1' });
  expect(openTab.mock.calls[0][0]).toMatchObject({
    id: 'task-task-root',
    type: 'taskPanel.TaskLoopView',
  });
  expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-root');
});

it('定位历史消息时收起覆盖面板，露出会话中的高亮消息', () => {
  const { options } = makeOptions({});
  const { result } = renderHook(() => useChatHeaderPanels(options));
  act(() => result.current.togglePanel('history'));
  act(() => result.current.locateMessage('m1'));
  expect(result.current.openPanel).toBeNull();
  expect(result.current.highlightMessageId).toBe('m1');
});

it('sessionHasSidePaneContent 引号变体均命中（引擎/BCS 输出风格不一），无标记不命中', () => {
  // 单引号（BCS 业务面板的真实输出形态：cardId 在前、type='panel' 靠后）。
  const singleQuoted =
    '<AixUI cardId=\'card_63c96b74\' type=\'panel\' tab=\'{"id":"2026-08-19","title":"2026-08-19杭州天气","closable":true}\'>'
      + '\n{"data":{"weather":"多云"}}\n</AixUI>';
  // 无引号 + 开标签跨行属性。
  const unquoted = "<AixUI\n  component='taskPanel.TaskLoopView'\n  type=panel\n  tab='{...}'>\n</AixUI>";
  // 旧版 panel= 属性（无显式 type 时的位置轴兼容位）。
  const legacyAttr = "<AixUI component='lib.X' panel='{\"tabId\":\"t1\"}'></AixUI>";
  // 非副屏标记不应命中：内联 card / 属性值含 panel 字样的 component 名。
  const inlineCard = "<AixUI type='card' component='weather.panel'></AixUI>";

  expect(sessionHasSidePaneContent([{ id: 'a', role: 'assistant', content: singleQuoted } as never])).toBe(true);
  expect(sessionHasSidePaneContent([{ id: 'b', role: 'assistant', content: unquoted } as never])).toBe(true);
  expect(sessionHasSidePaneContent([{ id: 'c', role: 'assistant', content: legacyAttr } as never])).toBe(true);
  expect(sessionHasSidePaneContent([{ id: 'd', role: 'assistant', content: inlineCard } as never])).toBe(false);
  expect(sessionHasSidePaneContent([{ id: 'e', role: 'assistant', content: '普通消息' } as never])).toBe(false);
});

it('切会话镜像态复位：副屏被 useBotChat closePanelForce 强关，Monitor 激活态同步回 false', () => {
  const { options, openTab } = makeOptions({ messages: makePanelMessages() });
  const { result, rerender } = renderHook(({ session }) => useChatHeaderPanels({ ...options, session }), {
    initialProps: { session: makeSession() as BotChatSessionView | null },
  });

  act(() => result.current.toggleSidePane());
  expect(openTab).toHaveBeenCalledTimes(1);
  expect(result.current.sidePaneOpen).toBe(true);

  // 切会话：副屏镜像态复位（此前 chat 侧未复位，图标点亮但面板已闭合，单击无反应）。
  rerender({ session: makeSession({ sessionId: 'sess-2' }) });
  expect(result.current.sidePaneOpen).toBe(false);
  expect(result.current.openPanel).toBeNull();
});

it('任务节点真实路由切会话时保留 TaskPanel 的头部开合镜像态', () => {
  const { options } = makeOptions({ messages: makePanelMessages() });
  const { result, rerender } = renderHook(({ session }) => useChatHeaderPanels({ ...options, session }), {
    initialProps: { session: makeSession() as BotChatSessionView | null },
  });
  act(() => result.current.onSidePaneIsOpenChange(true));

  window.history.pushState({}, '', '/workspace/chat?bot=bot-2&session=sess-2');
  const store = useTaskConversationNavigationStore.getState();
  store.rememberTaskTab('t1', {
    id: 'task-t1',
    title: 'Task',
    type: 'taskPanel.TaskLoopView',
    params: { taskId: 't1' },
  });
  const navigationId = store.beginNavigation('t1', '/workspace/chat', 'sess-2');
  store.markRouteCommitted(navigationId);

  rerender({ session: makeSession({ sessionId: 'sess-2', botId: 'bot-2' }) });

  expect(result.current.sidePaneOpen).toBe(true);
  expect(result.current.openPanel).toBeNull();
  expect(useTaskConversationNavigationStore.getState().navigation?.routeHydrated).toBe(true);
});

it('副屏判空：无 <AixUI type="panel"> 时 Monitor 禁用且 toggle 空转，出现副屏消息后解除', () => {
  const { options, openTab } = makeOptions({ messages: makeMessages() });
  const { result, rerender } = renderHook(({ messages }) => useChatHeaderPanels({ ...options, messages }), {
    initialProps: { messages: makeMessages() as ChatMessage[] },
  });

  // 无副屏内容：禁用 + 开启空转（无可重建的 tab，不翻镜像态）。
  expect(result.current.sidePaneDisabled).toBe(true);
  act(() => result.current.toggleSidePane());
  expect(openTab).not.toHaveBeenCalled();
  expect(result.current.sidePaneOpen).toBe(false);

  // 副屏消息进入历史：解除禁用，开合恢复正常。
  rerender({ messages: makePanelMessages() });
  expect(result.current.sidePaneDisabled).toBe(false);
  act(() => result.current.toggleSidePane());
  expect(openTab).toHaveBeenCalledTimes(1);
  expect(result.current.sidePaneOpen).toBe(true);
});

it('真实态同步：渲染器挂载默认展开（onIsOpenChange(true)）即点亮 sidePaneOpen，Monitor 首点直接关闭', () => {
  // 场景回放：消息渲染器挂载即 openTab 自动展开副屏（无任何用户点按）。
  // 修复前 sidePaneOpen 盲镜像恒 false —— 收起钮不出现、Monitor 首点仍走「打开」空转。
  const { options, closePanel } = makeOptions({ messages: makePanelMessages() });
  const { result } = renderHook(() => useChatHeaderPanels(options));

  act(() => result.current.onSidePaneIsOpenChange(true));
  expect(result.current.sidePaneOpen).toBe(true);

  // Mirror/真实态一致后，Monitor 首点语义即「关闭」。
  act(() => result.current.toggleSidePane());
  expect(closePanel).toHaveBeenCalledTimes(1);
  expect(result.current.sidePaneOpen).toBe(false);

  // autoCloseWhenEmpty 弹回等真实关态事件同样同步。
  act(() => result.current.onSidePaneIsOpenChange(false));
  expect(result.current.sidePaneOpen).toBe(false);
});

it('TaskPanel-only 会话在关闭后 Monitor 仍可点，并用 task tab 快照重开', () => {
  const { options, openTab, closePanel } = makeOptions({ messages: makeMessages() });
  useTaskSessionAssociationStore.getState().setActiveSession('sess-1');
  useTaskSessionAssociationStore.getState().setActiveSessionTaskIds('sess-1', ['task-1']);
  useTaskConversationNavigationStore.getState().rememberTaskTab('task-1', {
    id: 'task-task-1',
    title: '当前任务',
    type: 'taskPanel.TaskLoopView',
    closable: true,
    params: { taskId: 'task-1' },
  });

  const { result } = renderHook(() => useChatHeaderPanels(options));
  expect(result.current.sidePaneDisabled).toBe(false);

  act(() => result.current.toggleSidePane());
  expect(openTab).toHaveBeenCalledTimes(1);
  expect(openTab.mock.calls[0][0]).toMatchObject({
    id: 'task-task-1',
    title: '当前任务',
    type: 'taskPanel.TaskLoopView',
  });

  act(() => result.current.toggleSidePane());
  expect(closePanel).toHaveBeenCalledTimes(1);
  expect(result.current.sidePaneDisabled).toBe(false);

  act(() => result.current.toggleSidePane());
  expect(openTab).toHaveBeenCalledTimes(2);
});

it('副屏让位互斥：面板展开时收副屏（保留 tabs 可回开），面板全收后自动回开副屏', () => {
  // 场景回放：副屏展开（覆盖层盖住右缘面板让位区）时打开历史消息——修复前面板让宽
  // 却被悬浮副屏整条掩盖（表现即「侧边栏空白」）；修复后面板展开前先收副屏，关面板自动回开。
  const { options, closePanel, openPanel: handleOpenPanel } = makeOptions({ messages: makePanelMessages() });
  const { result } = renderHook(() => useChatHeaderPanels(options));

  // 副屏展开（真实态同步）→ 打开右缘面板：副屏让位收起（closePanel 保留 tabs）。
  act(() => result.current.onSidePaneIsOpenChange(true));
  act(() => result.current.togglePanel('history'));
  expect(result.current.openPanel).toBe('history');
  expect(closePanel).toHaveBeenCalledTimes(1);
  expect(handleOpenPanel).not.toHaveBeenCalled();

  // 副屏收起真实态事件同步（closePanel 的 SDK 回声）→ 面板关闭：全收后自动回开副屏。
  act(() => result.current.onSidePaneIsOpenChange(false));
  act(() => result.current.closePanel());
  expect(result.current.openPanel).toBeNull();
  expect(handleOpenPanel).toHaveBeenCalledTimes(1);
  // 资源抽屉同域互斥（togglePanel 打开分支 + 副屏展开收面板共用收抽屉语义）。
  expect(options.closeFileDrawer).toHaveBeenCalled();
});

it('副屏真实展开时收右缘域面板（含消息渲染器挂载自动展开路径）', () => {
  const { options } = makeOptions({});
  const { result } = renderHook(() => useChatHeaderPanels(options));

  act(() => result.current.togglePanel('history'));
  expect(result.current.openPanel).toBe('history');

  // 副屏展开（含自动展开）：右缘面板与资源抽屉一并收起，防让位区被覆盖成空条。
  act(() => result.current.onSidePaneIsOpenChange(true));
  expect(result.current.openPanel).toBeNull();
  // 1=togglePanel 打开分支收抽屉；2=副屏展开时右缘域互斥再收一次（幂等）。
  expect(options.closeFileDrawer).toHaveBeenCalledTimes(2);
});

it('副屏未开时打开面板不触发让位：closePanel 无空调用', () => {
  const { options, closePanel } = makeOptions({});
  const { result } = renderHook(() => useChatHeaderPanels(options));

  act(() => result.current.togglePanel('history'));
  act(() => result.current.closePanel());
  expect(closePanel).not.toHaveBeenCalled();
});

it('让位后切会话不回开副屏：副屏已 closePanelForce 清 tabs，回开会空弹', () => {
  const { options, openPanel: handleOpenPanel } = makeOptions({ messages: makePanelMessages() });
  const { result, rerender } = renderHook(({ session }) => useChatHeaderPanels({ ...options, session }), {
    initialProps: { session: makeSession() as BotChatSessionView | null },
  });

  // 副屏开 → 面板让位收起 → 副屏关态回声；随后未关面板即切会话。
  act(() => result.current.onSidePaneIsOpenChange(true));
  act(() => result.current.togglePanel('history'));
  act(() => result.current.onSidePaneIsOpenChange(false));

  rerender({ session: makeSession({ sessionId: 'sess-2' }) });
  expect(result.current.openPanel).toBeNull();
  expect(handleOpenPanel).not.toHaveBeenCalled();
});

it('打开按消息标记幂等重建 tabs：BCS 天气卡（单引号 cardId/type/tab）原样重建 openTab 载荷', () => {
  // 真实线上形态：单引号属性 + 标签体 JSON（PanelCardTrigger 同款 payload 语义）。
  const weatherMessage = {
    id: 'w1',
    role: 'assistant',
    content:
      "<AixUI cardId='card_63c96b74' type='panel' tab='{\"id\":\"2026-08-19\",\"title\":\"2026-08-19杭州天气\",\"closable\":true}'>\n"
      + '{\n  "data": { "weather": "多云" }\n}\n'
      + '</AixUI>',
    status: 'history',
  } as never;
  const { options, openTab } = makeOptions({ messages: [weatherMessage] as ChatMessage[] });
  const { result } = renderHook(() => useChatHeaderPanels(options));

  act(() => result.current.toggleSidePane());
  expect(result.current.sidePaneOpen).toBe(true);
  expect(openTab).toHaveBeenCalledTimes(1);
  const spec = openTab.mock.calls[0][0] as Record<string, unknown>;
  expect(spec.id).toBe('2026-08-19');
  expect(spec.type).toBe('aix-card-panel');
  expect(spec.title).toBe('2026-08-19杭州天气');
  expect(spec.closable).toBe(true);
  expect(spec.params).toMatchObject({
    cardId: 'card_63c96b74',
    data: { data: { weather: '多云' } },
  });
  expect(spec.payload).toBe(spec.params);
});
