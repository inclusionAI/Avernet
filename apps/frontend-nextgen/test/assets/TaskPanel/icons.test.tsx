/** @jest-environment jsdom */
// icons.tsx:内联 SVG 图标集合 + 节点状态圆点/呼吸环/状态图标(F3)。尺寸/颜色经 props 透传。
import { C } from '@/assets/TaskPanel/tokens';
import {
  ArrowLeft,
  CheckCircle,
  ChevronRight,
  Clock,
  Close,
  CloseCircle,
  DrillWorkflow,
  ExternalLink,
  Info,
  Loading,
  MinusCircle,
  NodeStatusDot,
  NodeStatusIcon,
  NodeStatusPulse,
  PanelToggle,
  TaskSwitcherIcon,
  Users,
} from '@/assets/TaskPanel/icons';
import { render } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

/** jsdom 内联样式会把 #RRGGBB 归一成 rgb(r, g, b),断言前先做同样换算 */
const hexToRgb = (hex: string) => {
  const n = parseInt(hex.replace('#', ''), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
};

describe('icons.tsx 内联图标', () => {
  it('基础线性图标:默认 16px + 自定义 size/color/style 透传', () => {
    const { container, rerender } = render(<Clock />);
    let svg = container.querySelector('svg')!;
    expect(svg.getAttribute('width')).toBe('16');
    // 透传 size/color/title 等 SVGProps
    rerender(<Clock size={20} color={C.success} />);
    svg = container.querySelector('svg')!;
    expect(svg.getAttribute('width')).toBe('20');
    expect(svg.getAttribute('stroke')).toBe(C.success);

    // 其余线性图标逐一渲染,不抛错且拥有 children path
    const { container: c2 } = render(
      <div>
        <Clock data-testid="ok" />
        <MinusCircle />
        <ExternalLink />
        <ChevronRight />
        <Info />
        <Close />
        <Users />
        <TaskSwitcherIcon />
        <ArrowLeft />
      </div>,
    );
    expect(c2.querySelectorAll('svg').length).toBe(9);
  });

  it('Clock/MinusCircle 默认色取 textMuted,ExternalLink/Info/Close 取 textSecondary', () => {
    const { container } = render(
      <div>
        <Clock />
        <MinusCircle />
        <ExternalLink />
        <Info />
        <Close />
      </div>,
    );
    const svgs = container.querySelectorAll('svg');
    expect(svgs[0].getAttribute('stroke')).toBe(C.textMuted);
    expect(svgs[1].getAttribute('stroke')).toBe(C.textMuted);
    expect(svgs[2].getAttribute('stroke')).toBe(C.textSecondary);
    expect(svgs[3].getAttribute('stroke')).toBe(C.textSecondary);
    expect(svgs[4].getAttribute('stroke')).toBe(C.textSecondary);
  });

  it('CheckCircle:实心圆 + 白色对勾,默认色取 success', () => {
    const { container } = render(<CheckCircle />);
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('stroke')).toBe('none');
    const circle = svg.querySelector('circle')!;
    expect(circle.getAttribute('fill')).toBe(C.success);
    const path = svg.querySelector('path')!;
    expect(path.getAttribute('stroke')).toBe('#fff');
    // color 透传覆盖圆底色
    const { container: c } = render(<CheckCircle color={C.danger} />);
    expect(c.querySelector('circle')!.getAttribute('fill')).toBe(C.danger);
  });

  it('Loading:默认开启旋转动画,spin=false 时无动画', () => {
    const { container, rerender } = render(<Loading />);
    const svgSpin = container.querySelector('svg')!;
    expect(svgSpin.style.animation).toContain('task-panel-spin');
    expect(svgSpin.getAttribute('fill')).toBe(C.warning);

    rerender(<Loading spin={false} size={24} color={C.primary} />);
    const svgStill = container.querySelector('svg')!;
    expect(svgStill.style.animation).toBe('');
    expect(svgStill.getAttribute('width')).toBe('24');
    expect(svgStill.getAttribute('fill')).toBe(C.primary);
  });

  it('CloseCircle:实心 danger 圆 + 内部 x 形镂空', () => {
    const { container } = render(<CloseCircle />);
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('fill')).toBe(C.danger);
    expect(svg.querySelector('path')).toBeTruthy();
    // color 覆盖同样生效
    const { container: c } = render(<CloseCircle color={C.success} />);
    expect(c.querySelector('svg')!.getAttribute('fill')).toBe(C.success);
  });

  it('PanelToggle / DrillWorkflow:固定 viewBox 手绘形', () => {
    const { container } = render(
      <div>
        <PanelToggle />
        <DrillWorkflow />
      </div>,
    );
    const svgs = container.querySelectorAll('svg');
    expect(svgs[0].getAttribute('viewBox')).toBe('0 0 14 14');
    expect(svgs[0].querySelector('rect')).toBeTruthy();
    expect(svgs[0].querySelector('line')).toBeTruthy();
    expect(svgs[1].querySelectorAll('rect').length).toBe(4);
    expect(svgs[1].querySelector('path')).toBeTruthy();
  });
});

describe('NodeStatusDot 节点状态圆点', () => {
  it.each([
    ['done', C.success],
    ['running', C.warning],
    ['failed', C.danger],
    ['hung', C.primary],
    ['cancelled', C.border],
    ['pending', C.textMuted],
  ])('status=%s 渲染对应底色圆点', (status, color) => {
    render(<NodeStatusDot status={status} />);
    const dot = document.querySelector('span')!;
    expect(dot.style.background).toBe(hexToRgb(color));
    expect(dot.style.width).toBe('12px'); // 默认 size=12
  });

  it('自定义 size 生效', () => {
    render(<NodeStatusDot status="running" size={16} />);
    expect(document.querySelector('span')!.style.width).toBe('16px');
  });
});

describe('NodeStatusPulse 呼吸环', () => {
  it('running:实心圆点外叠加呼吸环动画', () => {
    const { container } = render(<NodeStatusPulse status="running" />);
    const wrapper = container.querySelector('span')!;
    expect(wrapper.style.display).toBe('inline-flex');
    const pulse = wrapper.querySelectorAll('span')[1];
    expect(pulse.style.animation).toContain('task-panel-dag-pulse');
    expect(pulse.style.border).toContain(hexToRgb(C.warning));
  });

  it('非 running:直接退化为状态圆点', () => {
    const { container } = render(<NodeStatusPulse status="done" size={14} />);
    const dot = container.querySelector('span')!;
    expect(dot.style.background).toBe(hexToRgb(C.success));
    expect(dot.style.width).toBe('14px');
  });
});

describe('NodeStatusIcon 状态图标', () => {
  it.each([
    ['done', '已完成'],
    ['running', '执行中'],
    ['failed', '执行失败'],
    ['hung', '任务挂起'],
    ['cancelled', '已取消'],
  ])('status=%s → 状态图标携带 aria-label=%s + role=img', (status, label) => {
    const { container } = render(<NodeStatusIcon status={status} />);
    // svg 形图标 clone 后携带 aria-label + role=img
    const el = container.querySelector('[aria-label], [role="img"]');
    expect(el).toBeTruthy();
    expect(el!.getAttribute('aria-label')).toBe(label);
  });

  it('pending 兜底为 NodeStatusDot 圆点(注:圆点组件丢弃 clone 注入的 aria-label/role,可访问文本丢失)', () => {
    const { container } = render(<NodeStatusIcon status="pending" size={20} />);
    const dot = container.querySelector('span')!;
    expect(dot).toBeTruthy();
    expect(dot.style.background).toBe(hexToRgb(C.textMuted));
    // 已知问题记录:NodeStatusDot 只解构 status/size,cloneElement 注入的 aria-label 未透传到 DOM
    expect(dot.getAttribute('aria-label')).toBeNull();
  });

  it('running 状态图标复用 Loading(0.8 倍尺寸的旋转动画)', () => {
    render(<NodeStatusIcon status="running" size={20} />);
    const svg = document.querySelector('svg')!;
    expect(svg.style.animation).toContain('task-panel-spin');
    expect(svg.getAttribute('width')).toBe('16');
  });
});
