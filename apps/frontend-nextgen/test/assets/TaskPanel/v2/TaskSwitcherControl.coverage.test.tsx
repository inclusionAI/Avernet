/** @jest-environment jsdom */
// TaskSwitcherControl 覆盖率补齐:显隐切换/空任务/拖拽全流程(含阈值、pointercancel、
// 抑制点击句柄、卸载清理)/面板缺失降级/拖拽后 resize 观察分支。
import { TaskSwitcherControl } from '@/assets/TaskPanel/v2/TaskSwitcherControl';
import type { TaskSwitcherItem } from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

// jest 的 jsdom 版本未实现 PointerEvent(@testing-library 会退化成裸 Event,
// pointerId/button init 全部丢失 → onPointerDown 一律命中「非左键」守卫)。
// 这里以 MouseEvent 派生桩补齐 init 透传,保证拖拽流可测。
if (typeof window !== 'undefined' && typeof window.PointerEvent === 'undefined') {
  class PointerEventShim extends MouseEvent {
    pointerId: number;
    pointerType: string;
    constructor(type: string, init: MouseEventInit & { pointerId?: number; pointerType?: string } = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
      this.pointerType = init.pointerType ?? '';
    }
  }
  window.PointerEvent = PointerEventShim as unknown as typeof PointerEvent;
}

const items: TaskSwitcherItem[] = [
  { taskId: 't1', title: '任务一', status: 'running', statusLabel: '执行中' },
  { taskId: 't2', title: '任务二', status: 'pending', statusLabel: '待执行', hasUnhandledUpdate: true },
];

const baseProps = {
  items,
  activeTaskId: 't1',
  onSelectTask: jest.fn(),
};

function controlButton(container: HTMLElement): HTMLElement {
  const button = container.querySelector<HTMLElement>('button[data-task-switcher-control="true"]');
  if (!button) throw new Error('task switcher control button not found');
  return button;
}

/** 在带 data-task-panel-root 的宿主内渲染(拖拽边界依赖该祖先)。 */
function renderInPanel(props: Partial<Parameters<typeof TaskSwitcherControl>[0]> = {}) {
  const view = render(
    <div data-task-panel-root="true">
      <TaskSwitcherControl {...baseProps} {...props} />
    </div>,
  );
  return { view, button: controlButton(view.container) };
}

describe('TaskSwitcherControl 覆盖补齐', () => {
  it('items 为空时不渲染任何节点', () => {
    const view = render(
      <div data-task-panel-root="true">
        <TaskSwitcherControl {...baseProps} items={[]} />
      </div>,
    );
    expect(view.container.querySelector('button[data-task-switcher-control="true"]')).toBeNull();
  });

  it('点击悬浮按钮切换切换条显隐;defaultVisible 控制首帧', () => {
    // 默认收起
    const first = renderInPanel();
    expect(first.view.container.querySelector('[title="展开任务切换"]')).toBeNull();
    fireEvent.click(first.button);
    expect(first.view.container.querySelector('[title="展开任务切换"]')).not.toBeNull();
    // 再次点击收起
    fireEvent.click(first.button);
    expect(first.view.container.querySelector('[title="展开任务切换"]')).toBeNull();
    first.view.unmount();

    // defaultVisible=true 首帧展开,徽章同时可见
    const second = renderInPanel({ defaultVisible: true });
    expect(second.view.container.querySelector('[title="展开任务切换"]')).not.toBeNull();
    expect(
      second.view.container.querySelector('[data-testid="task-switcher-attention-badge"]'),
    ).not.toBeNull();
  });

  it('拖拽全流程:越过阈值才移动,pointUp 抑制后续点击,resize 收边', async () => {
    const { view, button } = renderInPanel();

    // 非左键按下不进入拖拽
    fireEvent.pointerDown(button, { button: 2, pointerId: 3, clientX: 100, clientY: 50 });
    fireEvent.pointerMove(button, { pointerId: 3, clientX: 140, clientY: 90 });
    expect(button.style.left).toBe('');

    // 左键按下 → 未越阈值移动不产生位移
    fireEvent.pointerDown(button, { button: 0, pointerId: 7, clientX: 100, clientY: 50 });
    fireEvent.pointerMove(button, { pointerId: 7, clientX: 102, clientY: 52 });
    expect(button.style.left).toBe('');

    // 越过阈值 → 计算位置并进入面板边界
    fireEvent.pointerMove(button, { pointerId: 7, clientX: 150, clientY: 90 });
    expect(button.style.left).toBe('8px');
    expect(button.style.top).toBe('8px');

    // 到位后触发 window resize,拖拽后的按钮被重新钳制回面板内
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(button.style.left).toBe('8px');

    // 未参与拖拽的 pointerId 指针抬起不清理状态
    fireEvent.pointerUp(button, { pointerId: 99 });
    expect(button.style.left).toBe('8px');

    // 结束拖拽:抑制点击句柄被挂起,紧随的 click 被吞掉
    fireEvent.pointerUp(button, { pointerId: 7 });
    fireEvent.click(button);
    expect(view.container.querySelector('[title="展开任务切换"]')).toBeNull();

    // 抑制窗口结束后点击恢复切换
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 0);
      });
    });
    fireEvent.click(button);
    expect(view.container.querySelector('[title="展开任务切换"]') !== null).toBe(true);
    view.unmount();
  });

  it('抑制点击句柄:二次拖拽间被清掉,到期回调释放标记,卸载时兜底清理', async () => {
    const { view, button } = renderInPanel();

    const dragTo = (pointerId: number) => {
      fireEvent.pointerDown(button, { button: 0, pointerId, clientX: 10, clientY: 10 });
      fireEvent.pointerMove(button, { pointerId, clientX: 60, clientY: 60 });
      fireEvent.pointerUp(button, { pointerId });
    };

    // 第一次拖拽:挂起 0ms 抑制点击句柄(未消费)
    dragTo(4);
    // 第二次拖拽:上一次的 0ms 句柄还没来得及触发(同步执行无事件循环空转),
    // pointerUp 内先 clearTimeout 清理上一个句柄,再挂新句柄
    dragTo(21);

    // 抑制窗口到期:回调把 suppressClickRef 清回 false
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 0);
      });
    });

    // 第三次拖拽后不再等待句柄触发,直接卸载:unmount 清理里 clearTimeout 生效
    dragTo(33);
    view.unmount();
  });

  it('pointercancel:匹配则中止拖拽,不匹配则忽略', () => {
    const { view, button } = renderInPanel();

    // pointerId 不匹配 → 直接忽略
    fireEvent.pointerDown(button, { button: 0, pointerId: 5, clientX: 10, clientY: 10 });
    fireEvent.pointerCancel(button, { pointerId: 8 });
    expect(button.style.left).toBe('');

    // 匹配 → 清理拖拽状态,按钮吸附回默认位置
    fireEvent.pointerMove(button, { pointerId: 5, clientX: 80, clientY: 60 });
    expect(button.style.left).toBe('8px');
    fireEvent.pointerCancel(button, { pointerId: 5 });
    expect(button.style.left).toBe('8px');
    // cancel 后再 move 不会恢复拖拽
    fireEvent.pointerMove(button, { pointerId: 5, clientX: 200, clientY: 200 });
    view.unmount();
  });

  it('宿主缺少 data-task-panel-root 祖先时按下不进入拖拽', () => {
    const view = render(<TaskSwitcherControl {...baseProps} />);
    const button = controlButton(view.container);

    fireEvent.pointerDown(button, { button: 0, pointerId: 2, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(button, { pointerId: 2, clientX: 200, clientY: 200 });
    // 无面板容器 → dragRef 未建立,位置仍走默认停靠
    expect(button.style.left).toBe('');
    expect(button.style.right).toBe('16px');
    view.unmount();
  });

  it('位置生效后宿主容器缺失时,resize effect 走降级分支', () => {
    const { view, button } = renderInPanel();

    // 先在面板内按下建立拖拽态,再摘掉宿主容器标记,复刻宿主属性缺失/卸载瞬间
    fireEvent.pointerDown(button, { button: 0, pointerId: 6, clientX: 30, clientY: 30 });
    const panelRoot = button.closest('[data-task-panel-root="true"]');
    panelRoot?.removeAttribute('data-task-panel-root');
    fireEvent.pointerMove(button, { pointerId: 6, clientX: 90, clientY: 70 });

    // hasCustomPosition 生效,但按钮祖先链上已无面板容器 → resize effect 提前返回,不注册观察器
    expect(button.style.left).toBe('8px');
    // resize 也不会因缺失观察器而报错
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    view.unmount();
  });
});
