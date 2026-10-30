/** @jest-environment jsdom */
import { DagView } from '@/assets/TaskPanel/DagView';
import type { DagNodeView } from '@/assets/TaskPanel/types';
import { describe, expect, it, jest, beforeEach, afterEach } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen } from '@testing-library/react';

const node = (id: string, over: Partial<DagNodeView> = {}): DagNodeView =>
  ({ id, label: id, status: 'pending', x: 0, y: 0, isCurrent: false, ...over } as DagNodeView);

let rectSpy: ReturnType<typeof jest.spyOn>;
let hasRO = false;

beforeEach(() => {
  rectSpy = jest.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 800,
    height: 600,
    left: 0,
    top: 0,
    right: 800,
    bottom: 600,
    x: 0,
    y: 0,
    toJSON() {
      return { width: 800, height: 600, left: 0, top: 0, right: 800, bottom: 600, x: 0, y: 0 };
    },
  } as DOMRect);
  (global as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  hasRO = true;
});

afterEach(() => {
  rectSpy.mockRestore();
  if (hasRO) {
    delete (global as any).ResizeObserver;
    hasRO = false;
  }
});

describe('DagView empty', () => {
  it('渲染空状态，并 flush rAF 让 revealLatest 走 maxBottom -Infinity 早返 (L158/L176)', async () => {
    render(<DagView dagNodes={[]} dagEdges={[]} />);
    expect(screen.getByText('暂无 DAG 数据')).toBeInTheDocument();
    await act(async () => {
      await new Promise<void>((r) => {
      setTimeout(r, 30);
    });
    });
  });
});

describe('DagView 节点布局', () => {
  it('单个根节点：图例/工具栏/100% 缩放', () => {
    const { container } = render(<DagView dagNodes={[node('A', { y: 0 })]} dagEdges={[]} />);
    expect(container.querySelector('svg')).toBeInTheDocument();
    // 图例：已完成
    expect(screen.getByText('已完成')).toBeInTheDocument();
    // 工具栏按钮
    expect(screen.getByText('+')).toBeInTheDocument();
    expect(screen.getByText('−')).toBeInTheDocument();
    expect(screen.getByText('⊙')).toBeInTheDocument();
    // 缩放比例
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('两节点边：子节点 y 大于父节点（父先放置）', () => {
    const A = node('A', { y: 0 });
    const B = node('B', { y: 100 });
    const { container } = render(
      <DagView dagNodes={[A, B]} dagEdges={[{ from: 'A', to: 'B' }]} />,
    );
    const paths = container.querySelectorAll('svg > g > g > path');
    expect(paths.length).toBeGreaterThan(0);
  });

  it('两节点边：子节点 y 小于父节点（父未放置 → L102-105）', () => {
    const A = node('A', { y: 200 }); // parent
    const B = node('B', { y: 0 }); // child, processed first
    const { container } = render(
      <DagView dagNodes={[A, B]} dagEdges={[{ from: 'A', to: 'B' }]} />,
    );
    expect(container.querySelectorAll('rect').length).toBeGreaterThan(0);
  });

  it('移除节点后清理 placedRef（L90）', () => {
    const A = node('A');
    const B = node('B', { y: 100 });
    const { rerender } = render(<DagView dagNodes={[A, B]} dagEdges={[{ from: 'A', to: 'B' }]} />);
    act(() => {
      rerender(<DagView dagNodes={[A]} dagEdges={[]} />);
    });
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('边引用的父节点不存在 → byId.get(pId) undefined 走 n.y 兜底（L107）', () => {
    const { container } = render(
      <DagView dagNodes={[node('B', { y: 0 })]} dagEdges={[{ from: 'GHOST', to: 'B' }]} />,
    );
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('getBoundingClientRect 返回 0 → view 保持 0（L141 false）', () => {
    rectSpy.mockReturnValue({
      width: 0,
      height: 0,
      left: 0,
      top: 0,
      right: 0,
      bottom: 0,
      x: 0,
      y: 0,
      toJSON() {},
    } as DOMRect);
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('未知节点状态走 NODE_STATUS_TONES pending 兜底', () => {
    const { container } = render(
      <DagView dagNodes={[node('A', { status: 'weirdstatus' as any })]} dagEdges={[]} />,
    );
    // 节点卡片 rect 存在
    expect(container.querySelectorAll('rect').length).toBeGreaterThan(0);
  });
});

describe('DagView 节点视觉分支', () => {
  it('running 节点渲染发光圈 + isCurrent 用 tone.stroke', () => {
    const { container } = render(
      <DagView dagNodes={[node('A', { status: 'running', isCurrent: true })]} dagEdges={[]} />,
    );
    // 发光圈：动画 style
    const glow = Array.from(container.querySelectorAll('rect')).find((r) =>
      r.getAttribute('style')?.includes('task-panel-dag-pulse'),
    );
    expect(glow).toBeTruthy();
  });

  it('selectedNodeId → primary stroke + width 2.5', () => {
    const { container } = render(
      <DagView dagNodes={[node('A')]} dagEdges={[]} selectedNodeId="A" />,
    );
    const cardRect = Array.from(container.querySelectorAll('rect')).find(
      (r) => r.getAttribute('width') === '150',
    );
    expect(cardRect).toBeTruthy();
    expect(cardRect!.getAttribute('stroke-width')).toBe('2.5');
  });

  it('drilledNodeId → primary stroke + width 2.5', () => {
    const { container } = render(
      <DagView dagNodes={[node('A')]} dagEdges={[]} drilledNodeId="A" />,
    );
    const cardRect = Array.from(container.querySelectorAll('rect')).find(
      (r) => r.getAttribute('width') === '150',
    );
    expect(cardRect!.getAttribute('stroke-width')).toBe('2.5');
  });

  it('isCurrent 但未选中 → tone.stroke width 2', () => {
    const { container } = render(
      <DagView dagNodes={[node('A', { isCurrent: true, status: 'done' })]} dagEdges={[]} />,
    );
    const cardRect = Array.from(container.querySelectorAll('rect')).find(
      (r) => r.getAttribute('width') === '150',
    );
    expect(cardRect!.getAttribute('stroke-width')).toBe('2');
  });

  it('label 长度 > 10 截断 + ≤10 原样', () => {
    const { container } = render(
      <DagView
        dagNodes={[node('A', { label: 'ABCDEFGHIJK' }), node('B', { label: 'AB', y: 200 })]}
        dagEdges={[]}
      />,
    );
    const texts = Array.from(container.querySelectorAll('text')).map((t) => t.textContent);
    expect(texts).toContain('ABCDEFGHIJ…');
    expect(texts).toContain('AB');
  });
});

describe('DagView 节点交互', () => {
  it('onKeyDown Enter → onNodeClick；Space → onNodeClick', () => {
    const onNodeClick = jest.fn();
    const { container } = render(
      <DagView dagNodes={[node('A')]} dagEdges={[]} onNodeClick={onNodeClick} />,
    );
    const g = container.querySelector('svg > g > g[role="button"]') as unknown as HTMLElement;
    expect(g).toBeTruthy();
    fireEvent.keyDown(g, { key: 'Enter' });
    expect(onNodeClick).toHaveBeenCalledWith('A');
    fireEvent.keyDown(g, { key: ' ' });
    expect(onNodeClick).toHaveBeenCalledTimes(2);
  });

  it('onKeyDown 无 onNodeClick → onViewNodeDetail', () => {
    const onViewNodeDetail = jest.fn();
    const { container } = render(
      <DagView dagNodes={[node('A')]} dagEdges={[]} onViewNodeDetail={onViewNodeDetail} />,
    );
    const g = container.querySelector('svg > g > g[role="button"]') as unknown as HTMLElement;
    fireEvent.keyDown(g, { key: 'Enter' });
    expect(onViewNodeDetail).toHaveBeenCalledTimes(1);
  });

  it('onKeyDown 非 Enter/Space → 无动作（L409 false 分支）', () => {
    const onNodeClick = jest.fn();
    const onViewNodeDetail = jest.fn();
    const { container } = render(
      <DagView
        dagNodes={[node('A')]}
        dagEdges={[]}
        onNodeClick={onNodeClick}
        onViewNodeDetail={onViewNodeDetail}
      />,
    );
    const g = container.querySelector('svg > g > g[role="button"]') as unknown as HTMLElement;
    fireEvent.keyDown(g, { key: 'Tab' });
    fireEvent.keyDown(g, { key: 'Escape' });
    expect(onNodeClick).not.toHaveBeenCalled();
    expect(onViewNodeDetail).not.toHaveBeenCalled();
  });

  it('mouseMove <5px → moved false → 释放触发 click（L225 false）', () => {
    const onNodeClick = jest.fn();
    const { container } = render(
      <DagView dagNodes={[node('A')]} dagEdges={[]} onNodeClick={onNodeClick} />,
    );
    const svg = container.querySelector('svg')!;
    const g = container.querySelector('svg > g > g[role="button"]') as unknown as HTMLElement;
    fireEvent.mouseDown(g, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(svg, { clientX: 11, clientY: 11 }); // <5px → moved false
    fireEvent.mouseUp(svg);
    expect(onNodeClick).toHaveBeenCalledWith('A');
  });

  it('节点 mousedown+move>5+up → moved 不触发 click；mousedown+up 无 move → onNodeClick', () => {
    const onNodeClick = jest.fn();
    const { container } = render(
      <DagView dagNodes={[node('A')]} dagEdges={[]} onNodeClick={onNodeClick} />,
    );
    const svg = container.querySelector('svg')!;
    const g = container.querySelector('svg > g > g[role="button"]') as unknown as HTMLElement;
    // drag with move
    fireEvent.mouseDown(g, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(svg, { clientX: 50, clientY: 50 });
    fireEvent.mouseUp(svg);
    expect(onNodeClick).not.toHaveBeenCalled();
    // click without move
    fireEvent.mouseDown(g, { clientX: 10, clientY: 10 });
    fireEvent.mouseUp(svg);
    expect(onNodeClick).toHaveBeenCalledWith('A');
  });

  it('节点 mousedown+up 无 onNodeClick → onViewNodeDetail', () => {
    const onViewNodeDetail = jest.fn();
    const { container } = render(
      <DagView dagNodes={[node('A')]} dagEdges={[]} onViewNodeDetail={onViewNodeDetail} />,
    );
    const svg = container.querySelector('svg')!;
    const g = container.querySelector('svg > g > g[role="button"]') as unknown as HTMLElement;
    fireEvent.mouseDown(g, { clientX: 10, clientY: 10 });
    fireEvent.mouseUp(svg);
    expect(onViewNodeDetail).toHaveBeenCalledTimes(1);
  });

  it('拖拽节点更新位置（placedRef.has true 分支 L230-233）', () => {
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    const svg = container.querySelector('svg')!;
    const g = container.querySelector('svg > g > g[role="button"]') as unknown as HTMLElement;
    const cardRectBefore = Array.from(container.querySelectorAll('rect')).find(
      (r) => r.getAttribute('width') === '150',
    );
    const xBefore = cardRectBefore!.getAttribute('x');
    fireEvent.mouseDown(g, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(svg, { clientX: 200, clientY: 200 });
    fireEvent.mouseUp(svg);
    const cardRectAfter = Array.from(container.querySelectorAll('rect')).find(
      (r) => r.getAttribute('width') === '150',
    );
    expect(cardRectAfter!.getAttribute('x')).not.toBe(xBefore);
  });

  it('下钻热区：drillable + onNodeDrill', () => {
    const onNodeDrill = jest.fn();
    const { container } = render(
      <DagView
        dagNodes={[node('A', { drillable: true })]}
        dagEdges={[]}
        onNodeDrill={onNodeDrill}
      />,
    );
    const hotzone = container.querySelector('g[aria-label="下钻节点副屏"]') as unknown as HTMLElement;
    expect(hotzone).toBeTruthy();
    fireEvent.mouseDown(hotzone);
    fireEvent.click(hotzone);
    expect(onNodeDrill).toHaveBeenCalledWith('A');
  });
});

describe('DagView 画布平移', () => {
  it('svg 背景mousedown → 平移', () => {
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    const svg = container.querySelector('svg')!;
    fireEvent.mouseDown(svg, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(svg, { clientX: 40, clientY: 50 });
    fireEvent.mouseUp(svg);
    // 触发后 userInteractedRef=true，缩放比例仍 100%
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('mousedown 目标tag不在白名单 → 不平移（guard false；L211 false 分支）', () => {
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    // defs 内 marker 箭头 path：tag 'path' 不在白名单，且不在节点 g（无 stopPropagation）→ 冒泡到 svg handler
    const markerPath = container.querySelector('svg defs path') as unknown as HTMLElement;
    expect(markerPath).toBeTruthy();
    const svg = container.querySelector('svg')!;
    fireEvent.mouseDown(markerPath, { clientX: 0, clientY: 0 });
    fireEvent.mouseMove(svg, { clientX: 100, clientY: 100 });
    fireEvent.mouseUp(svg);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('mousedown 命中 rect/pattern/circle tag → 平移启动（L211 所有 tag 分支）', () => {
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    const svg = container.querySelector('svg')!;
    // 背景 grid rect：tag 'rect' → guard true
    const bgRect = svg.querySelector('rect') as unknown as HTMLElement;
    fireEvent.mouseDown(bgRect, { clientX: 5, clientY: 5 });
    fireEvent.mouseMove(svg, { clientX: 20, clientY: 20 });
    fireEvent.mouseUp(svg);
    // pattern：tag 'pattern' → guard true（mousedown 不触发平移反应即可，仅覆盖 tag 匹配分支）
    const pattern = svg.querySelector('pattern') as unknown as HTMLElement;
    fireEvent.mouseDown(pattern, { clientX: 5, clientY: 5 });
    fireEvent.mouseUp(svg);
    // pattern 内 circle：tag 'circle' → guard true
    const circle = svg.querySelector('pattern circle') as unknown as HTMLElement;
    fireEvent.mouseDown(circle, { clientX: 5, clientY: 5 });
    fireEvent.mouseUp(svg);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('mouseMove 无 drag/pan → 无动作（L237 false）', () => {
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    const svg = container.querySelector('svg')!;
    fireEvent.mouseMove(svg, { clientX: 10, clientY: 10 });
    expect(screen.getByText('100%')).toBeInTheDocument();
  });
});

describe('DagView revealLatest / rAF', () => {
  it('内容超出视口 → 下露并 setPan（L163 false,L164,L176 true,L159 true）', async () => {
    const nodes: DagNodeView[] = [];
    for (let i = 0; i < 30; i++) nodes.push(node(`N${i}`, { y: i * 120 }));
    render(<DagView dagNodes={nodes} dagEdges={[]} />);
    // 第一次 rAF：revealLatest 下露，设 lastBottomYRef，调整 pan
    await act(async () => {
      await new Promise<void>((r) => {
      setTimeout(r, 30);
    });
    });
    // pan 变化 → revealLatest identity 变 → effect 重跑 → 第二次 rAF：
    // lastBottomYRef 已 == maxBottom → L159 true 早返
    await act(async () => {
      await new Promise<void>((r) => {
      setTimeout(r, 30);
    });
    });
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('用户已交互后 rAF 跳过 revealLatest（L176 false）', async () => {
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    // node mousedown 设 userInteractedRef=true，但 revealLatest 依赖不变 →
    // L168 effect 不会重跑取消旧 rAF → 旧 rAF 触发时检查 false → 跳过 revealLatest
    const g = container.querySelector('svg > g > g[role="button"]') as unknown as HTMLElement;
    fireEvent.mouseDown(g, { clientX: 10, clientY: 10 });
    await act(async () => {
      await new Promise<void>((r) => {
      setTimeout(r, 30);
    });
    });
    fireEvent.mouseUp(container.querySelector('svg')!);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });
});

describe('DagView 缩放', () => {
  it('滚轮缩放：deltaY>0 缩小，deltaY<0 放大，并触及上下限', () => {
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    const svg = container.querySelector('svg')!;
    // 缩小到极限：每次 wheel 触发 re-render 才会用新 scale 重新挂监听
    for (let i = 0; i < 25; i++) {
      act(() => {
        svg.dispatchEvent(new WheelEvent('wheel', { clientX: 400, clientY: 300, deltaY: 100, cancelable: true }));
      });
    }
    expect(screen.getByText('30%')).toBeInTheDocument();
    // 放大到极限
    for (let i = 0; i < 25; i++) {
      act(() => {
        svg.dispatchEvent(new WheelEvent('wheel', { clientX: 400, clientY: 300, deltaY: -100, cancelable: true }));
      });
    }
    expect(screen.getByText('250%')).toBeInTheDocument();
  });

  it('工具栏 + / − / ⊙ 与 hover', () => {
    render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    const plus = screen.getByText('+');
    const minus = screen.getByText('−');
    const reset = screen.getByText('⊙');
    fireEvent.mouseEnter(plus);
    fireEvent.mouseLeave(plus);
    fireEvent.click(plus);
    fireEvent.mouseEnter(minus);
    fireEvent.mouseLeave(minus);
    fireEvent.click(minus);
    fireEvent.mouseEnter(reset);
    fireEvent.mouseLeave(reset);
    fireEvent.click(reset);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('双击 svg 重置', () => {
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    const svg = container.querySelector('svg')!;
    fireEvent.doubleClick(svg);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });
});

describe('DagView 边渲染', () => {
  it('边引用缺失节点 → 无 path（覆盖 !from 与 !to 两臂）', () => {
    const { container } = render(
      <DagView
        dagNodes={[node('A')]}
        dagEdges={[
          { from: 'A', to: 'MISSING_TO' },
          { from: 'MISSING_FROM', to: 'A' },
        ]}
      />,
    );
    // 两条边都 return null；仅 marker 箭头（defs 内 1 个 path）
    expect(container.querySelectorAll('path').length).toBe(1);
  });

  it('sameCol 直线 vs 立方曲线', () => {
    // A 根，B、C 都是 A 子节点；B x=A.x（直），C x=A.x+step（曲）
    const A = node('A', { y: 0 });
    const B = node('B', { y: 100 });
    const C = node('C', { y: 100 });
    const { container } = render(
      <DagView
        dagNodes={[A, B, C]}
        dagEdges={[
          { from: 'A', to: 'B' },
          { from: 'A', to: 'C' },
        ]}
      />,
    );
    const dAttrs = Array.from(container.querySelectorAll('path'))
      .map((p) => p.getAttribute('d') || '')
      .filter((d) => d.startsWith('M'));
    expect(dAttrs.some((d) => d.includes(' L '))).toBe(true); // 直线
    expect(dAttrs.some((d) => d.includes(' C '))).toBe(true); // 立方
  });

  it('语义边 rework → warning 虚线 + title；split_work 同', () => {
    const { container } = render(
      <DagView
        dagNodes={[node('A'), node('B', { y: 100, status: 'running' })]}
        dagEdges={[
          { from: 'A', to: 'B', semantics: 'rework', reason: '返工原因' },
          { from: 'A', to: 'B', semantics: 'split_work' },
        ]}
      />,
    );
    const titles = Array.from(container.querySelectorAll('path > title')).map((t) => t.textContent);
    expect(titles).toContain('返工原因');
    expect(titles).toContain('split_work');
    const semanticPaths = Array.from(container.querySelectorAll('path')).filter((p) =>
      p.getAttribute('stroke-dasharray') === '4 3',
    );
    expect(semanticPaths.length).toBeGreaterThanOrEqual(2);
    // isSemantic + status running → 无动画
    const animated = Array.from(container.querySelectorAll('path')).find((p) =>
      p.getAttribute('style')?.includes('dag-flow'),
    );
    expect(animated).toBeUndefined();
  });

  it('待执行边 → 虚线 5 3；运行边（无语义）→ 动画；未知 to.status → C.border 兜底', () => {
    const { container } = render(
      <DagView
        dagNodes={[
          node('A'),
          node('P', { y: 100, status: 'pending' }),
          node('R', { y: 200, status: 'running' as any }),
          node('U', { y: 300, status: 'unknownstatus' as any }),
        ]}
        dagEdges={[
          { from: 'A', to: 'P' },
          { from: 'A', to: 'R' },
          { from: 'A', to: 'U' },
        ]}
      />,
    );
    const paths = Array.from(container.querySelectorAll('path')).filter((p) =>
      (p.getAttribute('d') || '').startsWith('M'),
    );
    expect(paths.some((p) => p.getAttribute('stroke-dasharray') === '5 3')).toBe(true);
    expect(paths.some((p) => p.getAttribute('style')?.includes('dag-flow'))).toBe(true);
  });
});

describe('DagView ResizeObserver / requestAnimationFrame 分支', () => {
  it('ResizeObserver 未定义 → useLayoutEffect 提前返回（L144）', () => {
    delete (global as any).ResizeObserver;
    hasRO = false;
    const { container } = render(<DagView dagNodes={[node('A')]} dagEdges={[]} />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('requestAnimationFrame 未定义 → 直接 revealLatest（L171-174）', () => {
    const raf = globalThis.requestAnimationFrame;
    // jsdom 提供 cancelAnimationFrame；保留它，避免破坏其它路径
    (globalThis as any).requestAnimationFrame = undefined;
    try {
      const { container } = render(<DagView dagNodes={[node('A', { y: 0 })]} dagEdges={[]} />);
      expect(container.querySelector('svg')).toBeInTheDocument();
    } finally {
      (globalThis as any).requestAnimationFrame = raf;
    }
  });
});

describe('DagView 边引用未知节点', () => {
  it('from 不在 dagNodes → 以幻影父节点落位子节点,边不渲染(命中 pp?.y ?? n.y 右臂)', () => {
    const { container } = render(
      <DagView dagNodes={[node('B', { y: 0 })]} dagEdges={[{ from: 'phantom-A', to: 'B' }]} />,
    );
    // 子节点 B 正常落位渲染
    expect(container.querySelectorAll('g[role="button"][aria-label^="查看节点"]')).toHaveLength(1);
    // from 节点缺失 → if(!from||!to) return null,边不渲染(仅剩 defs 内的箭头 marker 自身 path,无带 markerEnd 的边连线)
    expect(container.querySelectorAll('path[markerend]')).toHaveLength(0);
  });
});
