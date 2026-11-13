/**
 * @jest-environment jsdom
 */
// 槽位真实装配必须先于 hook 模块求值（useGroupChatHeaderPanels 在模块作用域解析槽位）：import 按声明序求值。
import '../../../helpers/registerOpenTaskPanelBootstrapSlot';
import type { SessionView } from '@/domain/collaboration';
import { useGroupChatHeaderPanels } from '@/pages/Workspace/hooks/useGroupChatHeaderPanels';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSessionAssociationStore } from '@/stores/taskSessionAssociationStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import type { ChatMessage, PanelHandle } from '@tc-chat/core';
import { act, renderHook } from '@testing-library/react';

function makeSession(overrides: Partial<SessionView> = {}): SessionView {
  return {
    sessionId: 'sess-1',
    groupId: 'g1',
    title: '会话',
    kind: 'chat',
    status: 'running',
    participants: [],
    lastMessageAt: 1,
    createdAt: 1,
    favorite: false,
    ...overrides,
  };
}

function makeMessages(): ChatMessage[] {
  return [
    { id: 'm1', role: 'user', content: '今天发布排期 确定', status: 'history' },
    { id: 'm2', role: 'assistant', content: '排期已同步给 Bot', status: 'history' },
    { id: 'm3', role: 'system', content: '系统通知', status: 'history' },
  ] as never;
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

/** 构造带头部副屏 handle 的 panelRef（openTab/closePanel/openPanel 以独立 mock 暴露便于断言）。 */
function makePanelHandle() {
  const openTab = jest.fn();
  const closePanel = jest.fn();
  const handle = {
    openTab,
    closePanel,
    openPanel: jest.fn(),
    closePanelForce: jest.fn(),
  } as unknown as PanelHandle;
  return { ref: { current: handle } as React.RefObject<PanelHandle | null>, openTab, closePanel, openPanel: handle.openPanel as jest.Mock };
}

beforeEach(() => {
  jest.clearAllMocks();
  useTaskConversationNavigationStore.getState().resetAll();
  useTaskSessionAssociationStore.getState().reset();
  window.history.pushState({}, '', '/workspace/collaboration?group=g1&session=sess-1');
});

it('收藏模型跟随会话：favorite 取自 SessionView，无会话禁用，onToggle 透传 sessionId', () => {
  const toggleFavorite = jest.fn().mockResolvedValue(undefined);
  const { ref: handle } = makePanelHandle();

  const { result } = renderHook(() =>
    useGroupChatHeaderPanels({
      userId: null,
      session: makeSession({ favorite: true }),
      messages: makeMessages(),
      panelRef: handle,
      toggleFavorite,
    }),
  );
  expect(result.current.favorites.favorite).toBe(true);
  expect(result.current.favorites.disabled).toBe(false);
  act(() => result.current.favorites.onToggle());
  expect(toggleFavorite).toHaveBeenCalledWith('sess-1');

  const { result: noSession } = renderHook(() =>
    useGroupChatHeaderPanels({
      userId: null,
      session: null,
      messages: makeMessages(),
      panelRef: handle,
      toggleFavorite,
    }),
  );
  expect(noSession.current.favorites.favorite).toBe(false);
  expect(noSession.current.favorites.disabled).toBe(true);
  expect(() => noSession.current.favorites.onToggle()).not.toThrow();
  expect(toggleFavorite).toHaveBeenCalledTimes(1);
});

it('historySearch 前端过滤：关键词/发送方筛选命中', () => {
  const { ref: handle } = makePanelHandle();
  const { result } = renderHook(() =>
    useGroupChatHeaderPanels({
      userId: null,
      session: makeSession(),
      messages: makeMessages(),
      panelRef: handle,
      toggleFavorite: jest.fn(),
    }),
  );
  expect(result.current.historySearch.results).toHaveLength(3);

  act(() => result.current.historySearch.setKeyword('排期'));
  expect(result.current.historySearch.results.map((r) => r.messageId)).toEqual(['m2', 'm1']);

  act(() => result.current.historySearch.setSender('assistant'));
  expect(result.current.historySearch.results.map((r) => r.messageId)).toEqual(['m2']);
});

it('locateMessage 高亮 3s 后自动清除', () => {
  jest.useFakeTimers();
  try {
    const { ref: handle } = makePanelHandle();
    const { result } = renderHook(() =>
      useGroupChatHeaderPanels({
      userId: null,
        session: makeSession(),
        messages: makeMessages(),
        panelRef: handle,
        toggleFavorite: jest.fn(),
      }),
    );
    act(() => result.current.locateMessage('m2'));
    expect(result.current.highlightMessageId).toBe('m2');
    act(() => jest.advanceTimersByTime(3000));
    expect(result.current.highlightMessageId).toBe(null);
  } finally {
    jest.useRealTimers();
  }
});

it('toggleSidePane 打开按消息幂等重建 tabs、关闭走 closePanel；切会话后镜像态复位（GroupChatPane 切换即 closePanelForce）', () => {
  const { ref: handle, openTab, closePanel } = makePanelHandle();
  const { result, rerender } = renderHook(
    ({ session }: { session: SessionView | null }) =>
      useGroupChatHeaderPanels({
      userId: null,
        session,
        messages: makePanelMessages(),
        panelRef: handle,
        toggleFavorite: jest.fn(),
      }),
    { initialProps: { session: makeSession() as SessionView | null } },
  );

  expect(result.current.sidePaneDisabled).toBe(false);
  expect(result.current.sidePaneOpen).toBe(false);
  act(() => result.current.toggleSidePane());
  // 打开 = openTab 幂等重建（PanelHandle 无 openPanel 直开：空 tabs 会被 autoCloseWhenEmpty 弹回）。
  expect(openTab).toHaveBeenCalledTimes(1);
  expect(result.current.sidePaneOpen).toBe(true);

  act(() => result.current.locateMessage('m1'));
  expect(result.current.highlightMessageId).toBe('m1');

  // 再次点击 → 关闭副屏。
  act(() => result.current.toggleSidePane());
  expect(closePanel).toHaveBeenCalledTimes(1);
  expect(result.current.sidePaneOpen).toBe(false);

  // 重新打开后切会话：高亮与副屏镜像态一并复位（副屏本体被 GroupChatPane closePanelForce 强关）。
  act(() => result.current.toggleSidePane());
  act(() => result.current.locateMessage('m1'));
  rerender({ session: makeSession({ sessionId: 'sess-2' }) });
  expect(result.current.sidePaneOpen).toBe(false);
  expect(result.current.highlightMessageId).toBe(null);
  // 复位后再次开合仍正常。
  act(() => result.current.toggleSidePane());
  expect(openTab).toHaveBeenCalledTimes(3);
});

it('TaskPanel-only 群会话在关闭后 Monitor 仍可点，并用 task tab 快照重开', () => {
  const { ref: handle, openTab, closePanel } = makePanelHandle();
  useTaskSessionAssociationStore.getState().setActiveSession('sess-1');
  useTaskSessionAssociationStore.getState().setActiveSessionTaskIds('sess-1', ['task-1']);
  useTaskConversationNavigationStore.getState().rememberTaskTab('task-1', {
    id: 'task-task-1',
    title: '当前任务',
    type: 'taskPanel.TaskLoopView',
    closable: true,
    params: { taskId: 'task-1' },
  });

  const { result } = renderHook(() =>
    useGroupChatHeaderPanels({
      userId: null,
      session: makeSession(),
      messages: makeMessages(),
      panelRef: handle,
      toggleFavorite: jest.fn(),
    }),
  );

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

it('任务节点真实路由切群会话时保留 TaskPanel 的头部开合镜像态', () => {
  const { ref: handle } = makePanelHandle();
  const { result, rerender } = renderHook(
    ({ session }: { session: SessionView | null }) =>
      useGroupChatHeaderPanels({
      userId: null,
        session,
        messages: makePanelMessages(),
        panelRef: handle,
        toggleFavorite: jest.fn(),
      }),
    { initialProps: { session: makeSession() as SessionView | null } },
  );
  act(() => result.current.onSidePaneIsOpenChange(true));

  window.history.pushState({}, '', '/workspace/collaboration?group=g2&session=sess-2');
  const store = useTaskConversationNavigationStore.getState();
  store.rememberTaskTab('t1', {
    id: 'task-t1',
    title: 'Task',
    type: 'taskPanel.TaskLoopView',
    params: { taskId: 't1' },
  });
  const navigationId = store.beginNavigation('t1', '/workspace/collaboration', 'sess-2');
  store.markRouteCommitted(navigationId);

  rerender({ session: makeSession({ sessionId: 'sess-2', groupId: 'g2' }) });

  expect(result.current.sidePaneOpen).toBe(true);
  expect(result.current.highlightMessageId).toBe(null);
  expect(useTaskConversationNavigationStore.getState().navigation?.routeHydrated).toBe(true);
});

it('副屏判空：无 <AixUI type="panel"> 时 Monitor 禁用且 toggle 空转，出现副屏消息后解除', () => {
  const { ref: handle, openTab } = makePanelHandle();
  const { result, rerender } = renderHook(
    ({ messages }: { messages: ChatMessage[] }) =>
      useGroupChatHeaderPanels({
      userId: null,
        session: makeSession(),
        messages,
        panelRef: handle,
        toggleFavorite: jest.fn(),
      }),
    { initialProps: { messages: makeMessages() } },
  );

  // 无副屏内容：禁用 + toggle 空转（无可重建的 tab，不翻镜像态）。
  expect(result.current.sidePaneDisabled).toBe(true);
  act(() => result.current.toggleSidePane());
  expect(openTab).not.toHaveBeenCalled();
  expect(result.current.sidePaneOpen).toBe(false);

  // 副屏消息进入历史（任务执行注入/loadHistory 拉回）：解除禁用，开合恢复正常。
  rerender({ messages: makePanelMessages() });
  expect(result.current.sidePaneDisabled).toBe(false);
  act(() => result.current.toggleSidePane());
  expect(openTab).toHaveBeenCalledTimes(1);
  expect(result.current.sidePaneOpen).toBe(true);
});

it('真实态同步：渲染器挂载默认展开（onIsOpenChange(true)）即点亮 sidePaneOpen，Monitor 首点直接关闭', () => {
  // 场景回放：消息渲染器挂载即 openTab 自动展开副屏（无任何用户点按）。
  // 修复前 sidePaneOpen 盲镜像恒 false —— 收起钮不出现、Monitor 首点仍走「打开」空转。
  const { ref: handle, closePanel } = makePanelHandle();
  const { result } = renderHook(() =>
    useGroupChatHeaderPanels({
      userId: null,
      session: makeSession(),
      messages: makePanelMessages(),
      panelRef: handle,
      toggleFavorite: jest.fn(),
    }),
  );

  act(() => result.current.onSidePaneIsOpenChange(true));
  expect(result.current.sidePaneOpen).toBe(true);

  // 真实态一致后，Monitor 首点语义即「关闭」。
  act(() => result.current.toggleSidePane());
  expect(closePanel).toHaveBeenCalledTimes(1);
  expect(result.current.sidePaneOpen).toBe(false);
});

// ---- 右缘域面板 × 副屏互斥（GroupWorkspaceArea 注入 panelsOpen/closePanels 装配）----

/**
 * 渲染群侧头部编排 Hook，并按 GroupWorkspaceArea 的真实时序驱动 panelsOpen：
 * openPanelKind 打开面板前置让位（dismissSidePaneForPanels）→ activePanel 置位
 * （panelsOpen=true）→ 关面板（panelsOpen=false，让位回开窗口）。
 */
function renderGroupMutexHarness(panelRef: React.RefObject<PanelHandle | null>, messages: ChatMessage[]) {
  const closePanels = jest.fn();
  const { result, rerender } = renderHook(
    ({ panelsOpen, session }: { panelsOpen: boolean; session: SessionView }) =>
      useGroupChatHeaderPanels({
        userId: null,
        session,
        messages,
        panelRef,
        toggleFavorite: jest.fn().mockResolvedValue(undefined),
        panelsOpen,
        closePanels,
      }),
    { initialProps: { panelsOpen: false, session: makeSession() } },
  );
  return { result, rerender, closePanels };
}

it('副屏让位互斥：面板展开时收副屏（保留 tabs 可回开），面板全收后自动回开副屏', () => {
  // 场景回放：群侧面板改内联右缘 aside 后与 chat 同构——副屏（AppShell 悬浮层）展开时
  // 整条盖住面板让位区；打开面板（侧栏「…」菜单/头部图标）先让位收副屏，全收后回开。
  const { ref: panelRef, closePanel, openPanel: handleOpenPanel } = makePanelHandle();
  const { result, rerender } = renderGroupMutexHarness(panelRef, makePanelMessages());

  // 副屏展开（真实态同步）→ 面板打开（openPanelKind 让位 + activePanel 置位）。
  act(() => result.current.onSidePaneIsOpenChange(true));
  act(() => result.current.dismissSidePaneForPanels());
  rerender({ panelsOpen: true, session: makeSession() });
  expect(closePanel).toHaveBeenCalledTimes(1);
  expect(handleOpenPanel).not.toHaveBeenCalled();

  // 副屏收起真实态事件同步（closePanel 的 SDK 回声）→ 面板全收：自动回开副屏。
  act(() => result.current.onSidePaneIsOpenChange(false));
  rerender({ panelsOpen: false, session: makeSession() });
  expect(handleOpenPanel).toHaveBeenCalledTimes(1);
});

it('副屏真实展开时收右缘域面板（GroupWorkspaceArea closePanels 注入 setActivePanel(none)）', () => {
  const { ref: panelRef } = makePanelHandle();
  const { result, closePanels } = renderGroupMutexHarness(panelRef, makeMessages());

  act(() => result.current.onSidePaneIsOpenChange(true));
  expect(result.current.sidePaneOpen).toBe(true);
  expect(closePanels).toHaveBeenCalledTimes(1);

  // 关态回声不误触收面板。
  act(() => result.current.onSidePaneIsOpenChange(false));
  expect(closePanels).toHaveBeenCalledTimes(1);
});

it('副屏未开时打开面板不触发让位：closePanel 无空调用', () => {
  const { ref: panelRef, closePanel } = makePanelHandle();
  const { result } = renderGroupMutexHarness(panelRef, makeMessages());

  act(() => result.current.dismissSidePaneForPanels());
  expect(closePanel).not.toHaveBeenCalled();
});

it('让位后切会话不回开副屏：副屏已 closePanelForce 清 tabs，回开会空弹', () => {
  const { ref: panelRef, openPanel: handleOpenPanel } = makePanelHandle();
  const { result, rerender } = renderGroupMutexHarness(panelRef, makePanelMessages());

  // 副屏开 → 面板让位收起 → 副屏关态回声；随后未关面板即切会话。
  act(() => result.current.onSidePaneIsOpenChange(true));
  act(() => result.current.dismissSidePaneForPanels());
  rerender({ panelsOpen: true, session: makeSession() });
  act(() => result.current.onSidePaneIsOpenChange(false));

  // 切会话（让位标记同边界作废）→ 面板随后全收：不回开（回开会空弹 autoCloseWhenEmpty）。
  rerender({ panelsOpen: true, session: makeSession({ sessionId: 'sess-2', groupId: 'g2' }) });
  rerender({ panelsOpen: false, session: makeSession({ sessionId: 'sess-2', groupId: 'g2' }) });
  expect(handleOpenPanel).not.toHaveBeenCalled();
});
