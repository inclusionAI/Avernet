/** @jest-environment jsdom */
import { TruncatedText } from '@/assets/TaskPanel/TruncatedText';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

// tooltip 由 portal 渲染到 document.body，故用 screen（全域）查询；render 容器内查不到。
// React 的 onMouseEnter/Leave 由 mouseover/mouseout 合成，故用 mouseOver/Out 触发。
// jsdom 的 getBoundingClientRect 默认全 0，会让 isAnchorOutOfView 误判“锚点已离开视口”
// 而立即收起 tooltip，故为被测元素 mock 一个位于视口内、有真实宽高的 rect。

const tipEl = () => screen.queryByRole('tooltip');

// jsdom 默认 innerWidth=1024 / innerHeight=768；以下 rect 完全位于视口内。
const IN_VIEW_RECT = {
  top: 100,
  bottom: 120,
  left: 100,
  right: 300,
  width: 200,
  height: 20,
  x: 100,
  y: 100,
} as DOMRect;

function mockRect(el: Element | null): void {
  if (!el) return;
  jest.spyOn(el, 'getBoundingClientRect').mockReturnValue(IN_VIEW_RECT);
}

describe('TruncatedText 截断悬浮 tooltip', () => {
  it('未截断时不弹 tooltip', () => {
    const { container } = render(<TruncatedText value="短" maxLength={10} />);
    fireEvent.mouseOver(container.firstChild as HTMLElement, { relatedTarget: null });
    expect(tipEl()).toBeNull();
  });

  it('悬浮截断文本 → 显示 tooltip(全文)；离开 → 消失', () => {
    const value = '这是一段很长的节点标题会被截断显示省略号但悬浮看全文';
    const { container } = render(<TruncatedText value={value} maxLength={5} />);
    const host = container.firstChild as HTMLElement;
    mockRect(host);
    fireEvent.mouseOver(host, { relatedTarget: null });
    expect(tipEl()).not.toBeNull();
    expect(screen.queryByText(value)).not.toBeNull();
    fireEvent.mouseOut(host, { relatedTarget: document.body });
    expect(tipEl()).toBeNull();
  });

  it('mousedown（点击意图，如点节点下钻）→ 立即收起 tooltip，防残留', () => {
    const value = '很长的节点标题很长的节点标题很长的节点标题';
    const { container } = render(<TruncatedText value={value} maxLength={5} />);
    const host = container.firstChild as HTMLElement;
    mockRect(host);
    fireEvent.mouseOver(host, { relatedTarget: null });
    expect(tipEl()).not.toBeNull();
    fireEvent.mouseDown(host);
    expect(tipEl()).toBeNull();
  });

  it('focus 显示 / blur 消失（键盘可达）', () => {
    const value = '键盘可达的截断标题键盘可达的截断标题';
    const { container } = render(<TruncatedText value={value} maxLength={4} as="div" />);
    const target = container.querySelector('[tabindex]') as HTMLElement;
    mockRect(target);
    fireEvent.focus(target);
    expect(tipEl()).not.toBeNull();
    fireEvent.blur(target);
    expect(tipEl()).toBeNull();
  });
});
