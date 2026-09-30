/** @jest-environment jsdom */
import { useStickToBottom } from '@/pages/Workspace/hooks/useStickToBottom';
import { beforeAll, describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { useRef, useState } from 'react';

/**
 * 模拟 BubbleList 的 DOM 结构：root → relative 容器 → overflow:auto 滚动容器 → 内容。
 * jsdom 无布局引擎，scrollHeight/clientHeight/scrollTop 用 defineProperty 打桩；
 * jsdom 无 ResizeObserver，用可手动触发的 mock 打桩。
 */

let resizeObserverCallbacks: Array<() => void> = [];

beforeAll(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      private cb: () => void;
      constructor(cb: () => void) {
        this.cb = cb;
        resizeObserverCallbacks.push(cb);
      }
      observe() {}
      unobserve() {}
      disconnect() {
        resizeObserverCallbacks = resizeObserverCallbacks.filter((fn) => fn !== this.cb);
      }
    },
  });
});

function fireResize() {
  resizeObserverCallbacks.forEach((cb) => cb());
}

function Harness({ withScroller = true }: { withScroller?: boolean }) {
  const rootRef = useRef<HTMLDivElement>(null);
  useStickToBottom(rootRef);
  return (
    <div ref={rootRef}>
      <div style={{ position: 'relative', height: '100%' }}>
        {withScroller ? (
          <div data-testid="scroller" style={{ height: '100%', overflowY: 'auto' }}>
            <div data-testid="content">msg</div>
          </div>
        ) : (
          <div data-testid="placeholder">加载中</div>
        )}
      </div>
    </div>
  );
}

/** 滚动容器延迟出现（loading → 列表）场景用。 */
function DeferredHarness() {
  const rootRef = useRef<HTMLDivElement>(null);
  const [showList, setShowList] = useState(false);
  useStickToBottom(rootRef);
  return (
    <div ref={rootRef}>
      <button type="button" data-testid="show" onClick={() => setShowList(true)}>
        show
      </button>
      {showList ? (
        <div data-testid="scroller" style={{ height: '100%', overflowY: 'auto' }}>
          <div data-testid="content">msg</div>
        </div>
      ) : (
        <div data-testid="placeholder">加载中</div>
      )}
    </div>
  );
}

interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

function mockScrollMetrics(el: HTMLElement, state: ScrollMetrics) {
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => state.clientHeight });
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => state.scrollHeight });
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => state.scrollTop,
    set: (value: number) => {
      state.scrollTop = Math.min(value, Math.max(0, state.scrollHeight - state.clientHeight));
      el.dispatchEvent(new Event('scroll'));
    },
  });
}

/** 模拟流式增长：内容高度变大 + DOM 文本节点追加（触发 MutationObserver）。 */
function growContent(contentEl: HTMLElement, state: ScrollMetrics, delta = 200) {
  state.scrollHeight += delta;
  contentEl.appendChild(document.createTextNode('…streaming'));
}

const settle = () =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, 60);
  });

function setup(initial: ScrollMetrics, withScroller = true) {
  render(<Harness withScroller={withScroller} />);
  const scroller = screen.getByTestId('scroller');
  const content = screen.getByTestId('content');
  mockScrollMetrics(scroller, initial);
  return { scroller, content, state: initial };
}

const atBottom: ScrollMetrics = { scrollTop: 700, scrollHeight: 1000, clientHeight: 300 };
const scrolledUp: ScrollMetrics = { scrollTop: 100, scrollHeight: 1000, clientHeight: 300 };

describe('useStickToBottom', () => {
  it('用户在底部时，内容增长自动贴底', async () => {
    const { content, state } = setup({ ...atBottom });
    growContent(content, state);
    await settle();
    expect(state.scrollTop).toBe(state.scrollHeight - state.clientHeight);
  });

  it('用户向上滚动后，内容增长不强制拉回底部', async () => {
    const { scroller, content, state } = setup({ ...scrolledUp });
    scroller.dispatchEvent(new Event('scroll'));
    growContent(content, state);
    await settle();
    expect(state.scrollTop).toBe(100);
  });

  it('用户滚回底部后恢复跟随', async () => {
    const { scroller, content, state } = setup({ ...scrolledUp });
    scroller.dispatchEvent(new Event('scroll'));
    growContent(content, state);
    await settle();
    expect(state.scrollTop).toBe(100);

    state.scrollTop = state.scrollHeight - state.clientHeight;
    scroller.dispatchEvent(new Event('scroll'));
    growContent(content, state);
    await settle();
    expect(state.scrollTop).toBe(state.scrollHeight - state.clientHeight);
  });

  it('连续多次增长持续贴底', async () => {
    const { content, state } = setup({ ...atBottom });
    growContent(content, state);
    await settle();
    growContent(content, state);
    await settle();
    growContent(content, state);
    await settle();
    expect(state.scrollTop).toBe(state.scrollHeight - state.clientHeight);
  });

  it('图片等媒体异步加载导致增高（无 DOM mutation）也贴底', async () => {
    const { content, state } = setup({ ...atBottom });
    const img = document.createElement('img');
    content.appendChild(img);
    await settle();
    // 图片加载完成：高度增长但无后续 mutation，仅触发 load 事件
    state.scrollHeight += 300;
    img.dispatchEvent(new Event('load'));
    await settle();
    expect(state.scrollTop).toBe(state.scrollHeight - state.clientHeight);
  });

  it('滚动容器变矮（如输入框增高挤压列表）时保持在底部', async () => {
    const { state } = setup({ ...atBottom });
    state.scrollTop = state.scrollHeight - state.clientHeight; // 1000-300=700 已贴底
    state.clientHeight = 200; // 输入框增高，列表可视区变矮
    fireResize();
    await settle();
    expect(state.scrollTop).toBe(state.scrollHeight - state.clientHeight);
  });

  it('滚动容器延迟出现（loading → 列表）仍能绑定并跟随', async () => {
    render(<DeferredHarness />);
    screen.getByTestId('show').click();
    const scroller = await screen.findByTestId('scroller');
    const content = screen.getByTestId('content');
    const state: ScrollMetrics = { ...atBottom };
    mockScrollMetrics(scroller, state);
    growContent(content, state);
    await settle();
    expect(state.scrollTop).toBe(state.scrollHeight - state.clientHeight);
  });
});
