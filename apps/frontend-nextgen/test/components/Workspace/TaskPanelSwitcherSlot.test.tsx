/** @jest-environment jsdom */
// I4 修复验收:切换条横条的「当前任务」必须以本 tab 的任务为准(currentTaskId 优先),
// 全局 activeTaskId 不得让非活跃任务面板显示别的任务为"当前"。
// 拖宽记忆验收:切任务 openTab 的 defaultWidth 恢复用户拖过的宽度,无记忆回落 420。
import { TaskPanelSwitcherSlot } from '@/components/Workspace/TaskPanelSwitcherSlot';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { persistPanelWidthPercent } from '@/services/workspace/panelWidthMemory';
import { chatBridge } from '@/services/workspace/chatBridge';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

// chatBridge.openPanelTab 用实例 spyOn(模块 mock factory 引用非全局 jest 会 TDZ,见既有备忘)。
jest.mock('@/hooks/useTaskSwitcherTasks', () => ({
  useTaskSwitcherTasks: () => ({
    items: [
      { taskId: 'task-current', title: '当前tab的任务', status: 'running', statusLabel: '执行中' },
      { taskId: 'task-other', title: '别的面板的任务', status: 'running', statusLabel: '执行中' },
    ],
    loading: false,
  }),
}));

const openPanelTabMock = jest.spyOn(chatBridge, 'openPanelTab').mockImplementation(() => {});

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

describe('TaskPanelSwitcherSlot 切任务 openTab 宽度(拖宽记忆)', () => {
  beforeEach(() => {
    useTaskSidePanelStore.getState().resetAll();
    localStorage.clear();
    openPanelTabMock.mockClear();
  });

  const switchToOtherTask = () => {
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

  it('用户拖宽至 560 后,切任务 openTab defaultWidth 恢复 560(不回落)', () => {
    persistPanelWidthPercent('56%', 1000);
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    switchToOtherTask();
    expect(openPanelTabMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-task-other', defaultWidth: 560 }),
    );
  });
});