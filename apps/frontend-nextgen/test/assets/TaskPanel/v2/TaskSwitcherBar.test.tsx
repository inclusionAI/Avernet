/** @jest-environment jsdom */
// TaskSwitcherBar:多任务切换横条(数据源 listTasks;下拉切换;无 TaskSwitcherFloat 演示脚手架)。
import {
  TaskSwitcherBar,
  type TaskSwitcherItem,
  type TaskSwitcherProps,
} from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, jest } from '@jest/globals';

const items: TaskSwitcherItem[] = [
  { taskId: 't-1', title: '竞品分析', status: 'running', statusLabel: '执行中' },
  { taskId: 't-2', title: '周报生成', status: 'pending', statusLabel: '待开始' },
  { taskId: 't-3', title: '旧任务', status: 'done', statusLabel: '已完成' },
];

const props = (overrides?: Partial<TaskSwitcherProps>): TaskSwitcherProps => ({
  items,
  activeTaskId: 't-1',
  onSelectTask: jest.fn(),
  ...overrides,
});

describe('TaskSwitcherBar', () => {
  it('横条渲染:当前任务名 + 状态 tag + 任务总数徽章', () => {
    render(<TaskSwitcherBar {...props()} />);
    expect(screen.getByText('任务')).toBeTruthy();
    expect(screen.getByText('竞品分析')).toBeTruthy();
    expect(screen.getByText('执行中')).toBeTruthy();
    expect(screen.getByTitle('任务总数').textContent).toBe('3');
  });

  it('点击横条展开下拉;点条目回调 onSelectTask 并收起', () => {
    const onSelectTask = jest.fn();
    render(<TaskSwitcherBar {...props({ onSelectTask })} />);
    // 下拉默认收起,其它任务不可见
    expect(screen.queryByText('周报生成')).toBeNull();

    fireEvent.click(screen.getByTitle('展开任务切换'));
    expect(screen.getByText('周报生成')).toBeTruthy();

    fireEvent.click(screen.getByText('周报生成'));
    expect(onSelectTask).toHaveBeenCalledWith('t-2');
    // 收起
    expect(screen.queryByText('周报生成')).toBeNull();
  });

  it('当前任务条目带「当前」标记且点击不触发切换', () => {
    const onSelectTask = jest.fn();
    render(<TaskSwitcherBar {...props({ onSelectTask })} />);
    fireEvent.click(screen.getByTitle('展开任务切换'));
    const dropdown = within(screen.getByTestId('task-switcher-dropdown'));
    expect(dropdown.getByText('当前')).toBeTruthy();
    fireEvent.click(dropdown.getByText('竞品分析'));
    expect(onSelectTask).not.toHaveBeenCalled();
  });

  it('空任务列表不渲染横条', () => {
    render(<TaskSwitcherBar items={[]} activeTaskId="t-1" onSelectTask={() => {}} />);
    expect(screen.queryByText('任务')).toBeNull();
  });
});