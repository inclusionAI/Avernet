/** @jest-environment jsdom */
import { TruncatedText } from '@/assets/TaskPanel/TruncatedText';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

// 未覆盖分支补充:tooltip 打开后锚点滚出视口 → 立即收起 tooltip(50-52)。

// jsdom 默认 getBoundingClientRect 全 0;先给视口内 rect 让 tooltip 能开,再把 rect 改为
// 已滚出视口的坐标,触发 scroll 让守卫生效。
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

const OUT_OF_VIEW_RECT = {
  top: -100,
  bottom: -80,
  left: 100,
  right: 300,
  width: 200,
  height: 20,
  x: -100,
  y: -100,
} as DOMRect;

describe('TruncatedText 滚动收起 tooltip', () => {
  it('tooltip 打开后锚点滚出视口 → scroll 时立即收起', () => {
    const value = '很长的节点标题很长的节点标题很长的节点标题很长';
    const { container } = render(<TruncatedText value={value} maxLength={5} />);
    const host = container.firstChild as HTMLElement;
    const rectSpy = jest.spyOn(host, 'getBoundingClientRect').mockReturnValue(IN_VIEW_RECT);

    // 打开 tooltip(先确认确实打开)
    fireEvent.mouseOver(host, { relatedTarget: null });
    expect(screen.queryByRole('tooltip')).not.toBeNull();

    // 锚点滚出视口(布局移出/被滚走)→ 触发 scroll 应立即收起 tooltip
    rectSpy.mockReturnValue(OUT_OF_VIEW_RECT);
    fireEvent.scroll(window);

    expect(screen.queryByRole('tooltip')).toBeNull();
    rectSpy.mockRestore();
  });
});
