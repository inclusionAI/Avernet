/** @jest-environment jsdom */
// TaskSwitcherBar/Control:默认保留旧行为；聊天副屏可传 defaultVisible/defaultOpen 首帧展开。
import {
  TaskSwitcherBar,
  type TaskSwitcherItem,
  type TaskSwitcherProps,
} from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import { TaskSwitcherControl } from '@/assets/TaskPanel/v2/TaskSwitcherControl';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeAll, describe, expect, it, jest } from '@jest/globals';

beforeAll(() => {
  if (!window.PointerEvent) {
    Object.defineProperty(window, 'PointerEvent', { configurable: true, value: MouseEvent });
  }
});

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

  it('只有一个任务时不渲染横条,保持任务总览头部简洁', () => {
    render(<TaskSwitcherBar items={[items[0]]} activeTaskId="t-1" onSelectTask={() => {}} />);
    expect(screen.queryByText('任务')).toBeNull();
    expect(screen.queryByText('竞品分析')).toBeNull();
  });
});

describe('TaskSwitcherControl', () => {
  it('多任务会话默认只展示悬浮开关,不展示顶部切换条', () => {
    render(<TaskSwitcherControl {...props()} />);

    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeTruthy();
    expect(screen.queryByText('竞品分析')).toBeNull();
    expect(screen.queryByText('任务')).toBeNull();
  });

  it('defaultVisible=true 首帧展示切换条,defaultOpen=true 首帧展开历史任务', () => {
    render(<TaskSwitcherControl {...props()} defaultVisible defaultOpen />);

    expect(screen.getAllByText('竞品分析').length).toBeGreaterThan(0);
    expect(screen.getByText('周报生成')).toBeTruthy();
    expect(screen.getByRole('button', { name: '隐藏任务切换' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('点击悬浮开关显示切换条,再次点击后隐藏', () => {
    render(<TaskSwitcherControl {...props()} />);

    const showButton = screen.getByRole('button', { name: '显示任务切换' });
    expect(showButton.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(showButton);

    expect(screen.getByText('竞品分析')).toBeTruthy();
    const hideButton = screen.getByRole('button', { name: '隐藏任务切换' });
    expect(hideButton.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(hideButton);

    expect(screen.queryByText('竞品分析')).toBeNull();
    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeTruthy();
  });

  it('悬浮开关可在 TaskPanel 内拖拽,拖拽结束不误触发切换条', () => {
    const { container } = render(
      <div data-task-panel-root="true">
        <TaskSwitcherControl {...props()} />
      </div>,
    );
    const panel = container.querySelector<HTMLElement>('[data-task-panel-root="true"]');
    const button = screen.getByRole('button', { name: '显示任务切换' });
    expect(panel).toBeTruthy();

    panel!.getBoundingClientRect = jest.fn(() => ({
      x: 100,
      y: 50,
      left: 100,
      top: 50,
      right: 500,
      bottom: 650,
      width: 400,
      height: 600,
      toJSON: () => ({}),
    }));
    button.getBoundingClientRect = jest.fn(() => ({
      x: 420,
      y: 570,
      left: 420,
      top: 570,
      right: 468,
      bottom: 618,
      width: 48,
      height: 48,
      toJSON: () => ({}),
    }));

    fireEvent.pointerDown(button, { pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerMove(button, { pointerId: 1, clientX: 344, clientY: 394 });
    fireEvent.pointerUp(button, { pointerId: 1, clientX: 344, clientY: 394 });
    fireEvent.click(button);

    expect(button.style.left).toBe('220px');
    expect(button.style.top).toBe('320px');
    expect(screen.queryByText('竞品分析')).toBeNull();
    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeTruthy();
  });

  it('单任务会话既不展示切换条,也不展示悬浮开关', () => {
    render(<TaskSwitcherControl {...props({ items: [items[0]] })} />);

    expect(screen.queryByRole('button', { name: '显示任务切换' })).toBeNull();
    expect(screen.queryByText('竞品分析')).toBeNull();
  });
});
