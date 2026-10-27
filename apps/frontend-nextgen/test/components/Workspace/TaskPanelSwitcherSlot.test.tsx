/** @jest-environment jsdom */
// 聊天副屏多任务切换验收:首帧展示同会话历史任务,切换时同步请求任务时间窗会话导航。
import { TaskPanelSwitcherSlot } from '@/components/Workspace/TaskPanelSwitcherSlot';
import type { TaskListItem } from '@/domain/tasks/models';
import { chatBridge } from '@/services/workspace/chatBridge';
import { persistPanelWidthPercent } from '@/services/workspace/panelWidthMemory';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, render, screen } from '@testing-library/react';

const taskRecord = (taskId: string, title: string): TaskListItem =>
  ({
    id: 1,
    task_id: taskId,
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    execution_config: { task_type: 'dynamic', main_session_id: 'session-shared' },
    task_spec: {
      context: {
        title,
        background: '',
        extend_props: { teamclaw_context: { main_session_id: 'session-shared' } },
      },
      goal: { objective: '', acceptances: [] },
    },
    status: 'EXECUTING',
    gmt_create: '2026-10-27 10:00:00',
    gmt_modified: '2026-10-27 11:00:00',
  }) as TaskListItem;

const currentRecord = taskRecord('task-current', '当前tab的任务');
const otherRecord = taskRecord('task-other', '别的面板的任务');

// chatBridge.openPanelTab 用实例 spyOn(模块 mock factory 引用非全局 jest 会 TDZ,见既有备忘)。
const mockUseTaskSwitcherTasks = jest.fn(() => ({
  items: [
    { taskId: currentRecord.task_id, title: '当前tab的任务', status: 'running', statusLabel: '执行中' },
    { taskId: otherRecord.task_id, title: '别的面板的任务', status: 'running', statusLabel: '执行中' },
  ],
  records: [currentRecord, otherRecord],
  loading: false,
}));
jest.mock('@/hooks/useTaskSwitcherTasks', () => ({
  useTaskSwitcherTasks: (...args: unknown[]) => mockUseTaskSwitcherTasks(...args),
  buildTaskConversationNavigationTarget: (record: TaskListItem) => ({
    taskId: record.task_id,
    sessionId: 'session-shared',
    kind: 'bot' as const,
    groupId: null,
    botId: 'b1:u1',
    startTime: Date.parse('2026-10-27T10:00:00'),
    endTime: Date.parse('2026-10-27T11:00:00'),
  }),
}));

const openPanelTabMock = jest.spyOn(chatBridge, 'openPanelTab').mockImplementation(() => {});

describe('TaskPanelSwitcherSlot(聊天宿主)', () => {
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

  it('多任务会话首帧展示切换条和历史任务列表', () => {
    act(() => {
      useTaskSidePanelStore.getState().setActiveTask('task-other');
    });
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);

    expect(screen.getAllByText('当前tab的任务').length).toBeGreaterThan(0);
    expect(screen.getByText('别的面板的任务')).toBeTruthy();
    expect(screen.getByRole('button', { name: '隐藏任务切换' })).toBeTruthy();
  });

  it('再次点击开关后隐藏任务列表', () => {
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);

    fireEvent.click(screen.getByRole('button', { name: '隐藏任务切换' }));

    expect(screen.queryByText('当前tab的任务')).toBeNull();
    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeTruthy();
  });

  it('切走本 tab 任务后,由宿主以新 params 重挂', () => {
    const { rerender } = render(
      <TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />,
    );
    rerender(<TaskPanelSwitcherSlot currentTaskId="task-switched" params={{ taskId: 'task-switched' }} />);

    expect(screen.getByRole('button', { name: '隐藏任务切换' })).toBeTruthy();
  });

  it('同会话只有一个任务时不渲染切换条', () => {
    mockUseTaskSwitcherTasks.mockReturnValueOnce({
      items: [{ taskId: 'task-current', title: '当前任务', status: 'running', statusLabel: '执行中' }],
      records: [currentRecord],
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

  it('切换任务时请求主会话导航并携带任务时间窗', () => {
    render(<TaskPanelSwitcherSlot currentTaskId="task-current" params={{ taskId: 'task-current' }} />);
    switchToOtherTask();

    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-other');
    expect(useTaskSidePanelStore.getState().pendingNavigation).toMatchObject({
      taskId: 'task-other',
      sessionId: 'session-shared',
      kind: 'bot',
      botId: 'b1:u1',
      startTime: Date.parse('2026-10-27T10:00:00'),
      endTime: Date.parse('2026-10-27T11:00:00'),
    });
    expect(openPanelTabMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'task-task-other', title: '别的面板的任务' }),
    );
  });
});
