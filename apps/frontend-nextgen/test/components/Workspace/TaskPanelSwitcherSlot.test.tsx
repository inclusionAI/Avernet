/** @jest-environment jsdom */
// I4 修复验收:切换条横条的「当前任务」必须以本 tab 的任务为准(currentTaskId 优先),
// 全局 activeTaskId 不得让非活跃任务面板显示别的任务为"当前"。
import { TaskPanelSwitcherSlot } from '@/components/Workspace/TaskPanelSwitcherSlot';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/hooks/useTaskSwitcherTasks', () => ({
  useTaskSwitcherTasks: () => ({
    items: [
      { taskId: 'task-current', title: '当前tab的任务', status: 'running', statusLabel: '执行中' },
      { taskId: 'task-other', title: '别的面板的任务', status: 'running', statusLabel: '执行中' },
    ],
    loading: false,
  }),
}));

describe('TaskPanelSwitcherSlot(I4)', () => {
  beforeEach(() => {
    useTaskSidePanelStore.getState().resetAll();
  });

  it('全局 activeTaskId 指向其它任务时,横条仍以本 tab 任务为当前(名称展示)', () => {
    act(() => {
      useTaskSidePanelStore.getState().setActiveTask('task-other');
    });
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    expect(screen.getByText('当前tab的任务')).toBeTruthy();
    expect(screen.queryByText('别的面板的任务')).toBeNull(); // 下拉未展开,其它任务不露脸
  });

  it('切走本 tab 任务后,由宿主以新 params 重挂(currentTaskId 跟随面板实例)', () => {
    const { rerender } = render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    rerender(<TaskPanelSwitcherSlot currentTaskId="task-switched" params={{ taskId: 'task-switched' }} />);
    // 横条跟随面板实例的 taskId,而非全局 activeTaskId
    expect(screen.queryByText('别的面板的任务')).toBeNull();
    expect(screen.getByText('任务')).toBeTruthy(); // 「任务」小标仍在
  });
});