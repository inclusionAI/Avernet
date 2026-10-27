/**
 * @jest-environment jsdom
 */
import type { SessionView } from '@/domain/collaboration';
import { useGroupChatHeaderPanels } from '@/pages/Workspace/hooks/useGroupChatHeaderPanels';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
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

/** 构造带头部副屏 handle 的 panelRef（openTab/closePanel 以独立 mock 暴露便于断言）。 */
function makePanelHandle() {
  const openTab = jest.fn();
  const closePanel = jest.fn();
  const handle = { openTab, closePanel, openPanel: jest.fn(), closePanelForce: jest.fn() } as unknown as PanelHandle;
  return { ref: { current: handle } as React.RefObject<PanelHandle | null>, openTab, closePanel };
}

beforeEach(() => {
  jest.clearAllMocks();
  useTaskConversationNavigationStore.getState().resetAll();
  window.history.pushState({}, '', '/workspace/collaboration?group=g1&session=sess-1');
});

it('收藏模型跟随会话：favorite 取自 SessionView，无会话禁用，onToggle 透传 sessionId', () => {
  const toggleFavorite = jest.fn().mockResolvedValue(undefined);
  const { ref: handle } = makePanelHandle();

  const { result } = renderHook(() =>
    useGroupChatHeaderPanels({
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

it('任务节点真实路由切群会话时保留 TaskPanel 的头部开合镜像态', () => {
  const { ref: handle } = makePanelHandle();
  const { result, rerender } = renderHook(
    ({ session }: { session: SessionView | null }) =>
      useGroupChatHeaderPanels({
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
