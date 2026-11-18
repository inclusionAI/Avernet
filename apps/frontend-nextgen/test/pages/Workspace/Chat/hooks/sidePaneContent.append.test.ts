// reopenSidePaneTabsFromMessages 的 panel-append 接线单测：不双开、legacy replace 不受影响、
// fallbackTab 仅在无任何副屏时使用。
import type { ChatMessage, PanelHandle, PanelTab } from '@tc-chat/core';
import { buildPanelAppendAixUI } from '@/services/tasks/panelAppendMessage';

const mockOpenBusinessPanel = jest.fn();
jest.mock('@tc-chat/ui/es/MarkdownRender/plugins/components/businessUtils', () => ({
  openBusinessPanel: (...args: unknown[]) => mockOpenBusinessPanel(...args),
}));

import { reopenSidePaneTabsFromMessages } from '@/pages/Workspace/Chat/hooks/sidePaneContent';

function mkMsg(content: string): ChatMessage {
  return { messageId: Math.random().toString(36).slice(2), content, role: 'assistant' } as unknown as ChatMessage;
}

function makePanel(): { panel: PanelHandle; openTab: jest.Mock } {
  const openTab = jest.fn();
  return {
    openTab,
    panel: {
      openTab,
      focusTab: jest.fn(),
      closeTab: jest.fn(),
      updateTab: jest.fn(),
      openPanel: jest.fn(),
      closePanel: jest.fn(),
      closePanelForce: jest.fn(),
      flushContext: jest.fn(),
      flushInteractions: jest.fn(),
    } as unknown as PanelHandle,
  };
}

function lastAppendCall(): Record<string, any> {
  const calls = mockOpenBusinessPanel.mock.calls.filter((call) => (call[1] as any)?.component === 'panel-append');
  return calls[calls.length - 1]?.[1] as Record<string, any>;
}

beforeEach(() => {
  jest.clearAllMocks();
});

it('panel-append 只经重建分支开一次 tab，不走 panel-component 即时分支', () => {
  const msgs = [mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }, { content: 'B' }]))];
  const { panel, openTab } = makePanel();

  expect(reopenSidePaneTabsFromMessages(msgs, panel)).toBe(true);
  expect(mockOpenBusinessPanel).toHaveBeenCalledTimes(1);
  const arg = lastAppendCall();
  expect(arg.component).toBe('panel-append');
  expect(arg.params.mode).toBe('append');
  expect(arg.params.blocks).toHaveLength(2);
  expect(arg.tab.id).toBe('task-app');
  expect(openTab).not.toHaveBeenCalled();
});

it('append 与 legacy panel-component 共存时各开一次，append 不双开', () => {
  const legacy =
    `<AixUI type="panel" component="taskPanel.TaskLoopView" ` +
    `tab='{"id":"task-1","title":"任务","closable":true}' params='{"taskId":"1"}'></AixUI>`;
  const msgs = [mkMsg(legacy), mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }]))];
  const { panel } = makePanel();

  expect(reopenSidePaneTabsFromMessages(msgs, panel)).toBe(true);
  expect(mockOpenBusinessPanel).toHaveBeenCalledTimes(2);
  const components = mockOpenBusinessPanel.mock.calls.map((call) => (call[1] as any)?.component);
  expect(components).toEqual(['taskPanel.TaskLoopView', 'panel-append']);
});

it('同一 tab 的多条 append 只重建一次并取最新完整序列', () => {
  const msgs = [
    mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }])),
    mkMsg(buildPanelAppendAixUI('task-app', '副屏', [{ content: 'A' }, { content: 'B' }])),
  ];
  const { panel } = makePanel();

  reopenSidePaneTabsFromMessages(msgs, panel);
  expect(mockOpenBusinessPanel).toHaveBeenCalledTimes(1);
  const arg = lastAppendCall();
  expect(arg.params.blocks.map((b: { content: string }) => b.content)).toEqual(['A', 'B']);
});

it('无副屏消息时回落 fallbackTab，且不调用 openBusinessPanel', () => {
  const fallbackTab = { id: 'fallback', type: 'taskPanel.TaskLoopView', title: '任务', closable: true } as PanelTab;
  const { panel, openTab } = makePanel();

  expect(reopenSidePaneTabsFromMessages([mkMsg('hello')], panel, fallbackTab)).toBe(true);
  expect(openTab).toHaveBeenCalledWith(fallbackTab);
  expect(mockOpenBusinessPanel).not.toHaveBeenCalled();
});

it('legacy component 的 replace 路径保持不变（无 panel-append 时只走原分支）', () => {
  const legacy =
    `<AixUI type="panel" component="taskPanel.TaskLoopView" ` +
    `tab='{"id":"task-1","title":"任务","closable":true}' params='{"taskId":"1"}'></AixUI>`;
  const { panel, openTab } = makePanel();

  expect(reopenSidePaneTabsFromMessages([mkMsg(legacy)], panel)).toBe(true);
  expect(mockOpenBusinessPanel).toHaveBeenCalledTimes(1);
  expect((mockOpenBusinessPanel.mock.calls[0][1] as any).component).toBe('taskPanel.TaskLoopView');
  expect(openTab).not.toHaveBeenCalled();
});
