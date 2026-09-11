/** @jest-environment jsdom */
import { FuseFloatButton } from '@/pages/Workspace/components/GroupChatPane/FuseFloatButton';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const STORAGE_KEY = 'teamclaw:fuse-float-button-bottom';

// jsdom 无 PointerEvent 构造函数，RTL 的 fireEvent.pointerDown 会回退为普通 Event，
// 丢失 button/clientY。与 ResizableWorkspaceSidebar.test 同款方案：用预构造 MouseEvent
// 交给 fireEvent 派发，保留 button/clientY 且经 act 同步提交状态。
function dispatchPointer(
  target: Element | Document,
  type: string,
  init: { button?: number; clientY?: number; bubbles?: boolean },
) {
  fireEvent(
    target,
    new MouseEvent(type, {
      bubbles: init.bubbles ?? true,
      cancelable: true,
      button: init.button ?? 0,
      clientY: init.clientY ?? 0,
    }),
  );
}

function wrapper() {
  return screen.getByTestId('fuse-float-button');
}

function bottomOf(el: HTMLElement) {
  return Number.parseFloat(el.style.bottom);
}

describe('FuseFloatButton 右边缘拖拽', () => {
  beforeEach(() => {
    window.localStorage.clear();
    // jsdom 默认 innerHeight=768，显式固定避免环境影响断言
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 768 });
  });

  it('默认渲染在预设 bottom 位置，普通点击仍触发 onClick', () => {
    const onClick = jest.fn();
    render(<FuseFloatButton onClick={onClick} sessionId="s1" />);
    expect(wrapper().style.bottom).toBe('200px');
    fireEvent.click(screen.getByRole('button', { name: /融合模式/ }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('按住向上拖动后 bottom 增大，且拖拽后的 click 不触发 onClick', () => {
    const onClick = jest.fn();
    render(<FuseFloatButton onClick={onClick} sessionId="s1" />);
    dispatchPointer(wrapper(), 'pointerdown', { button: 0, clientY: 500 });
    dispatchPointer(document, 'pointermove', { clientY: 400 });
    dispatchPointer(document, 'pointerup', {});
    expect(bottomOf(wrapper())).toBe(300); // 200 + (500-400)
    fireEvent.click(screen.getByRole('button', { name: /融合模式/ }));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('拖动被钳制在视口范围内', () => {
    render(<FuseFloatButton onClick={jest.fn()} sessionId="s1" />);
    // 向上拖出视口：bottom 最大 = innerHeight - 按钮高(40) - 边距(16)
    dispatchPointer(wrapper(), 'pointerdown', { button: 0, clientY: 500 });
    dispatchPointer(document, 'pointermove', { clientY: -1000 });
    dispatchPointer(document, 'pointerup', {});
    expect(bottomOf(wrapper())).toBe(768 - 40 - 16);
    // 向下拖出视口：bottom 最小 = 16
    dispatchPointer(wrapper(), 'pointerdown', { button: 0, clientY: 100 });
    dispatchPointer(document, 'pointermove', { clientY: 2000 });
    dispatchPointer(document, 'pointerup', {});
    expect(bottomOf(wrapper())).toBe(16);
  });

  it('拖拽结束后写入 localStorage，重新挂载时恢复位置', () => {
    const { unmount } = render(<FuseFloatButton onClick={jest.fn()} sessionId="s1" />);
    dispatchPointer(wrapper(), 'pointerdown', { button: 0, clientY: 500 });
    dispatchPointer(document, 'pointermove', { clientY: 440 });
    dispatchPointer(document, 'pointerup', {});
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('260');
    unmount();

    render(<FuseFloatButton onClick={jest.fn()} sessionId="s1" />);
    expect(wrapper().style.bottom).toBe('260px');
  });

  it('键盘 ArrowUp/ArrowDown 可微调位置（无障碍兜底）', () => {
    render(<FuseFloatButton onClick={jest.fn()} sessionId="s1" />);
    const button = screen.getByRole('button', { name: /融合模式/ });
    fireEvent.keyDown(button, { key: 'ArrowUp' });
    expect(bottomOf(wrapper())).toBe(212); // 200 + 12
    fireEvent.keyDown(button, { key: 'ArrowDown' });
    expect(bottomOf(wrapper())).toBe(200);
    fireEvent.keyDown(button, { key: 'ArrowUp' });
    fireEvent.keyDown(button, { key: 'ArrowUp' });
    expect(Number(window.localStorage.getItem(STORAGE_KEY))).toBe(224);
  });
});
