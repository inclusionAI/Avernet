/**
 * @jest-environment jsdom
 */
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { sessionHasSidePaneContent, useChatHeaderPanels } from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';
import type { ChatMessage, PanelHandle } from '@tc-chat/core';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

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

function makeOptions(overrides: { messages?: ChatMessage[]; panelRef?: React.RefObject<PanelHandle | null> }) {
  const openTab = jest.fn();
  const closePanel = jest.fn();
  const handle = { openTab, closePanel, openPanel: jest.fn(), closePanelForce: jest.fn() } as unknown as PanelHandle;
  return {
    openTab,
    closePanel,
    options: {
      session: makeSession(),
      messages: overrides.messages ?? makeMessages(),
      panelRef: overrides.panelRef ?? ({ current: handle } as React.RefObject<PanelHandle | null>),
      section: 'managed' as const,
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
