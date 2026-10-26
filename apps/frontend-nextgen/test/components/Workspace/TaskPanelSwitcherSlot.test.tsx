/** @jest-environment jsdom */
// I4 修复验收:切换条横条的「当前任务」必须以本 tab 的任务为准(currentTaskId 优先),
// 全局 activeTaskId 不得让非活跃任务面板显示别的任务为"当前"。
// 拖宽记忆验收:切任务 openTab 的 defaultWidth 恢复用户拖过的宽度,无记忆回落 420。
import { TaskPanelSwitcherSlot } from '@/components/Workspace/TaskPanelSwitcherSlot';
import { chatBridge } from '@/services/workspace/chatBridge';
import { persistPanelWidthPercent } from '@/services/workspace/panelWidthMemory';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, render, screen } from '@testing-library/react';

// chatBridge.openPanelTab 用实例 spyOn(模块 mock factory 引用非全局 jest 会 TDZ,见既有备忘)。
const mockUseTaskSwitcherTasks = jest.fn(() => ({
  items: [
    { taskId: 'task-current', title: '当前tab的任务', status: 'running', statusLabel: '执行中' },
    { taskId: 'task-other', title: '别的面板的任务', status: 'running', statusLabel: '执行中' },
  ],
  loading: false,
}));
jest.mock('@/hooks/useTaskSwitcherTasks', () => ({
  useTaskSwitcherTasks: (...args: unknown[]) => mockUseTaskSwitcherTasks(...args),
}));

const openPanelTabMock = jest.spyOn(chatBridge, 'openPanelTab').mockImplementation(() => {});

describe('TaskPanelSwitcherSlot(I4)', () => {
  beforeEach(() => {
    useTaskSidePanelStore.getState().resetAll();
    useTaskConversationNavigationStore.getState().resetAll();
    mockUseTaskSwitcherTasks.mockClear();
  });

  it('按当前面板 taskId 查询同会话任务', () => {
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    expect(mockUseTaskSwitcherTasks).toHaveBeenCalledWith(
      undefined,
      expect.objectContaining({ currentTaskId: 'task-current' }),
    );
  });

  it('多任务会话默认隐藏横条,只展示任务切换开关', () => {
    act(() => {
      useTaskSidePanelStore.getState().setActiveTask('task-other');
    });
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);

    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeTruthy();
    expect(screen.queryByText('当前tab的任务')).toBeNull();
    expect(screen.queryByText('别的面板的任务')).toBeNull();
  });

  it('点击开关后,横条仍以本 tab 任务为当前(名称展示)', () => {
    act(() => {
      useTaskSidePanelStore.getState().setActiveTask('task-other');
    });
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    fireEvent.click(screen.getByRole('button', { name: '显示任务切换' }));

    expect(screen.getByText('当前tab的任务')).toBeTruthy();
    expect(screen.queryByText('别的面板的任务')).toBeNull(); // 下拉未展开,其它任务不露脸
  });

  it('再次点击开关后隐藏横条', () => {
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    fireEvent.click(screen.getByRole('button', { name: '显示任务切换' }));
    expect(screen.getByText('当前tab的任务')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '隐藏任务切换' }));
    expect(screen.queryByText('当前tab的任务')).toBeNull();
  });

  it('切走本 tab 任务后,由宿主以新 params 重挂(currentTaskId 跟随面板实例)', () => {
    const { rerender } = render(
      <TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />,
    );
    rerender(<TaskPanelSwitcherSlot currentTaskId="task-switched" params={{ taskId: 'task-switched' }} />);
    // 开关跟随面板实例保留；横条仍默认隐藏。
    expect(screen.queryByText('别的面板的任务')).toBeNull();
    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeTruthy();
  });

  it('同会话只有一个任务时不渲染切换条', () => {
    mockUseTaskSwitcherTasks.mockReturnValueOnce({
      items: [{ taskId: 'task-current', title: '当前任务', status: 'running', statusLabel: '执行中' }],
      loading: false,
    });
    const { container } = render(
      <TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe('TaskPanelSwitcherSlot 切任务 openTab 宽度(拖宽记忆)', () => {
  beforeEach(() => {
    useTaskSidePanelStore.getState().resetAll();
    useTaskConversationNavigationStore.getState().resetAll();
    localStorage.clear();
    openPanelTabMock.mockClear();
    mockUseTaskSwitcherTasks.mockClear();
  });

  const switchToOtherTask = () => {
    fireEvent.click(screen.getByRole('button', { name: '显示任务切换' }));
    fireEvent.click(screen.getByTitle('展开任务切换'));
    fireEvent.click(screen.getByText('别的面板的任务'));
  };

  it('无拖宽记忆时,openTab defaultWidth 回落 PRD 初始宽 420', () => {
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    switchToOtherTask();
    expect(openPanelTabMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-task-other', defaultWidth: 420 }),
    );
  });

  it('共享右侧面板曾收窄至 350 时,任务 tab 仍按 PRD 默认宽 420 打开', () => {
    persistPanelWidthPercent('35%', 1000);
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    switchToOtherTask();
    expect(openPanelTabMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-task-other', defaultWidth: 420 }),
    );
  });

  it('用户拖宽至 560 后,切任务 openTab defaultWidth 恢复 560(不回落)', () => {
    persistPanelWidthPercent('56%', 1000);
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    switchToOtherTask();
    expect(openPanelTabMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-task-other', defaultWidth: 560 }),
    );
  });

  it('切换同会话任务时继承当前任务记录的原始宿主会话', () => {
    const origin = {
      routeUrl: '/workspace/chat?botId=bot-1&sessionId=session-origin',
      sessionId: 'session-origin',
      kind: 'bot' as const,
      botId: 'bot-1',
    };
    useTaskConversationNavigationStore.getState().rememberOrigin('task-current', origin);

    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    switchToOtherTask();

    expect(useTaskConversationNavigationStore.getState().originsByTask['task-other']).toEqual(origin);
  });
});
