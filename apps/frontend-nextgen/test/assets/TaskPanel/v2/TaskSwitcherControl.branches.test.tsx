/** @jest-environment jsdom */
// TaskSwitcherControl 的拖拽、取消、边距与生命周期分支。
import { TaskSwitcherControl } from '@/assets/TaskPanel/v2/TaskSwitcherControl';
import type { TaskSwitcherItem, TaskSwitcherProps } from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import { beforeAll, beforeEach, afterEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, within } from '@testing-library/react';


const items: TaskSwitcherItem[] = [
  { taskId: 't-1', title: '竞品分析', status: 'running', statusLabel: '执行中' },
  { taskId: 't-2', title: '周报生成', status: 'pending', statusLabel: '待开始' },
];

const props = (overrides?: Partial<TaskSwitcherProps>): TaskSwitcherProps => ({
  items,
  activeTaskId: 't-1',
  onSelectTask: jest.fn(),
  ...overrides,
});

const mockRects = (panel: HTMLElement, button: HTMLElement, panelWidth = 400) => {
  panel.getBoundingClientRect = jest.fn(() => ({
    x: 100,
    y: 50,
    left: 100,
    top: 50,
    right: 500,
    bottom: 650,
    width: panelWidth,
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
};

const renderInPanel = (overrides?: Partial<TaskSwitcherProps>) => {
  const view = render(
    <div data-task-panel-root="true">
      <TaskSwitcherControl {...props(overrides)} />
    </div>,
  );
  const panel = view.container.querySelector<HTMLElement>('[data-task-panel-root="true"]')!;
  const button = screen.getByRole('button', { name: '显示任务切换' });
  mockRects(panel, button);
  return { panel, button, view };
};

class TestPointerEvent extends MouseEvent {
  constructor(type: string, init: MouseEventInit & { pointerId?: number } = {}) {
    super(type, init);
    Object.defineProperty(this, 'pointerId', {
      configurable: true,
      value: init.pointerId,
    });
  }
}

beforeAll(() => {
  Object.defineProperty(window, 'PointerEvent', {
    configurable: true,
    value: TestPointerEvent,
  });
});

class ResizeObserverMock {
  observe = jest.fn();
  disconnect = jest.fn();
}

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: ResizeObserverMock,
  });
});

afterEach(() => {
  delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
});

describe('TaskSwitcherControl 分支', () => {
  it('空任务列表不渲染', () => {
    render(<TaskSwitcherControl {...props({ items: [] })} />);

    expect(screen.queryByTestId('task-switcher-control')).toBeNull();
  });

  it('右键按下不启动拖拽黏滞,普通点击仍可展开任务切换条', () => {
    const { button } = renderInPanel();

    fireEvent.pointerDown(button, { button: 2, pointerId: 1 });
    fireEvent.pointerMove(button, { pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerUp(button, { pointerId: 1 });
    fireEvent.click(button);

    expect(screen.getByText('竞品分析')).toBeInTheDocument();
  });

  it('其他 pointer-id 不影响当前拖拽状态', () => {
    const { button } = renderInPanel();

    fireEvent.pointerDown(button, { button: 0, pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerMove(button, { pointerId: 2, clientX: 344, clientY: 394 });
    fireEvent.pointerUp(button, { pointerId: 2, clientX: 344, clientY: 394 });
    fireEvent.pointerMove(button, { pointerId: 1, clientX: 344, clientY: 394 });

    expect(button.style.left).toBe('220px');
  });

  it('按下后取消拖拽会释放指针并恢复光标', () => {
    const { button, panel } = renderInPanel();
    button.setPointerCapture = jest.fn();
    button.releasePointerCapture = jest.fn();

    fireEvent.pointerDown(button, { button: 0, pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerCancel(button, { pointerId: 2 });

    fireEvent.pointerCancel(button, { pointerId: 1 });
    expect(button.style.cursor).toBe('grab');
    expect(button.releasePointerCapture).toHaveBeenCalledWith(1);
    expect(panel).toBeInTheDocument();
  });

  it('低于阈值的移动不进入拖拽状态', () => {
    const { button, view } = renderInPanel();

    fireEvent.pointerDown(button, { button: 0, pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerMove(button, { pointerId: 1, clientX: 446, clientY: 596 });
    fireEvent.pointerUp(button, { pointerId: 1 });
    fireEvent.click(button);

    expect(screen.getByText('竞品分析')).toBeInTheDocument();
    view.unmount();
  });

  it('移动后 unmount 会清理 suppress-click 定时器', () => {
    const { button, view } = renderInPanel();
    button.setPointerCapture = jest.fn();

    fireEvent.pointerDown(button, { button: 0, pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerMove(button, { pointerId: 1, clientX: 344, clientY: 394 });
    fireEvent.pointerUp(button, { pointerId: 1 });

    expect(button.style.left).toBe('220px');
    view.unmount();
    expect(screen.queryByText('竞品分析')).toBeNull();
  });

  it('面板 resize 后将悬浮按钮保持在面板内部', () => {
    const { panel, button, view } = renderInPanel();

    fireEvent.pointerDown(button, { button: 0, pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerMove(button, { pointerId: 1, clientX: 344, clientY: 394 });
    fireEvent.pointerUp(button, { pointerId: 1 });
    expect(button.style.left).toBe('220px');

    mockRects(panel, button, 100);
    fireEvent(window, new Event('resize'));

    expect(button.style.left).toBe('44px');
    view.unmount();
  });

  it('没有 ResizeObserver 时仍监听 window resize 事件', () => {
    Object.defineProperty(globalThis, 'ResizeObserver', {
      configurable: true,
      value: undefined,
    });
    const { panel, button, view } = renderInPanel();

    fireEvent.pointerDown(button, { button: 0, pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerMove(button, { pointerId: 1, clientX: 344, clientY: 394 });
    mockRects(panel, button, 100);

    fireEvent(window, new Event('resize'));

    expect(button.style.left).toBe('44px');
    view.unmount();
  });
});

describe('TaskSwitcherControl 手动事件', () => {
  it('指针状态为空时 move/up/cancel 均可安全结束', () => {
    render(<TaskSwitcherControl {...props()} />);

    const rendered = document.querySelector('[data-task-switcher-control="true"]');
    expect(rendered).not.toBeNull();

    fireEvent.pointerMove(rendered!, { pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerUp(rendered!, { pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerCancel(rendered!, { pointerId: 1, clientX: 444, clientY: 594 });

    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeInTheDocument();
  });

  it('拖拽后并展开任务条,焦点任务和普通任务均可见', () => {
    const { button } = renderInPanel();

    fireEvent.pointerDown(button, { button: 0, pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.pointerUp(button, { pointerId: 1, clientX: 444, clientY: 594 });
    fireEvent.click(button);

    expect(screen.getByText('竞品分析')).toBeInTheDocument();
    const contentRoot = screen.getByRole('button', { name: '隐藏任务切换' }).parentElement!;
    expect(within(contentRoot).getByText('竞品分析')).toBeInTheDocument();
  });
});
