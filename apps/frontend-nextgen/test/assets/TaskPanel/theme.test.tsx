/** @jest-environment jsdom */
// theme.tsx:通用样式块 Empty/StatusTag/Label/Value/LabelValue/SectionCard/Segmented + GlobalKeyframes。
import { C, TASK_STATUS_TONES } from '@/assets/TaskPanel/tokens';
import {
  Empty,
  GlobalKeyframes,
  Label,
  LabelValue,
  SectionCard,
  Segmented,
  StatusTag,
  Value,
} from '@/assets/TaskPanel/theme';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, jest } from '@jest/globals';

/** jsdom 内联样式会把 #RRGGBB 归一成 rgb(r, g, b),断言前先做同样换算 */
const hexToRgb = (hex: string) => {
  const n = parseInt(hex.replace('#', ''), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
};

describe('theme.tsx 通用样式块', () => {
  it('Empty:渲染描述文案与圆形底图 minHeight 可覆盖', () => {
    const { container } = render(<Empty description="暂无 DAG 数据" />);
    expect(screen.getByText('暂无 DAG 数据')).toBeTruthy();
    expect(screen.getByText('∅')).toBeTruthy();
    // 未传 minHeight 走默认 200
    expect(container.querySelector('div')!.style.minHeight).toBe('200px');

    const { container: c2 } = render(<Empty description="空" minHeight={300} />);
    expect(c2.querySelector('div')!.style.minHeight).toBe('300px');
  });

  it('StatusTag:已知状态取 7 态药丸配色,未知状态回退 DRAFTING', () => {
    const { container, rerender } = render(<StatusTag status="EXECUTING" />);
    const tag = container.querySelector('span')!;
    expect(tag.textContent).toBe(TASK_STATUS_TONES.EXECUTING.label);
    expect(tag.style.color).toBe(hexToRgb(TASK_STATUS_TONES.EXECUTING.color));
    expect(tag.style.background).toBe(hexToRgb(TASK_STATUS_TONES.EXECUTING.bg));
    // border 色 = color + 透明度后缀 → jsdom 归一为 rgba(…, 0.125)
    expect(tag.style.border).toContain('1px solid rgba(255, 125, 0, 0.125)');

    rerender(<StatusTag status="DONE" />);
    expect(container.querySelector('span')!.textContent).toBe(TASK_STATUS_TONES.DONE.label);

    // 未知状态 → DRAFTING 兜底
    rerender(<StatusTag status="__unknown__" />);
    expect(container.querySelector('span')!.textContent).toBe(TASK_STATUS_TONES.DRAFTING.label);
  });

  it('Label / Value:独立使用时按各自排版渲染', () => {
    render(
      <div>
        <Label>标签</Label>
        <Value>一段内容</Value>
      </div>,
    );
    expect(screen.getByText('标签').style.fontSize).toBe('12px');
    expect(screen.getByText('标签').style.color).toBe(hexToRgb(C.textSecondary));
    expect(screen.getByText('一段内容').style.wordBreak).toBe('break-all');
    expect(screen.getByText('一段内容').style.color).toBe(hexToRgb(C.textPrimary));
  });

  it('LabelValue:value 为 null 或空串时显示占位符 —', () => {
    const { rerender } = render(<LabelValue label="执行者" value={null} />);
    expect(screen.getByText('执行者')).toBeTruthy();
    expect(screen.getByText('—').style.color).toBe(hexToRgb(C.textMuted));

    rerender(<LabelValue label="执行者" value="" />);
    expect(screen.getByText('—')).toBeTruthy();

    // 有值时原样渲染
    rerender(<LabelValue label="执行者" value="竞品研究员Bot" />);
    expect(screen.getByText('竞品研究员Bot')).toBeTruthy();
    expect(screen.queryByText('—')).toBeNull();
  });

  it('SectionCard:marginTop>0 时渲染顶部分隔线,为 0 时不渲染', () => {
    const { container, rerender } = render(<SectionCard title="节点信息">内容甲</SectionCard>);
    const card = container.querySelector('div')!;
    expect(card.style.marginTop).toBe('0px');
    expect(card.style.borderTop).toBe(''); // 未传 marginTop → borderTop: none(jsdom 序列化为空)
    expect(screen.getByText('节点信息')).toBeTruthy();
    expect(screen.getByText('内容甲')).toBeTruthy();

    rerender(
      <SectionCard title="成果产物" marginTop={12}>
        内容乙
      </SectionCard>,
    );
    expect(container.querySelector('div')!.style.marginTop).toBe('12px');
    expect(container.querySelector('div')!.style.borderTop).toBe(`1px solid ${hexToRgb(C.border)}`);
  });

  it('Segmented:激活项高亮,点击非激活项回调新值', () => {
    const onChange = jest.fn();
    const options = [
      { label: '节点视图', value: 'nodes' },
      { label: '产物视图', value: 'artifacts' },
    ];
    const { container } = render(<Segmented value="nodes" options={options} onChange={onChange} />);
    const buttons = container.querySelectorAll('button');
    expect(buttons.length).toBe(2);
    // 激活态样式
    expect(buttons[0].style.fontWeight).toBe('600');
    expect(buttons[1].style.fontWeight).toBe('400');

    fireEvent.click(buttons[1]);
    expect(onChange).toHaveBeenCalledWith('artifacts');
  });

  it('GlobalKeyframes:styled-components 全局样式注入不抛错', () => {
    expect(() =>
      render(
        <div>
          <GlobalKeyframes />
          内容
        </div>,
      ),
    ).not.toThrow();
    expect(screen.getByText('内容')).toBeTruthy();
  });
});
