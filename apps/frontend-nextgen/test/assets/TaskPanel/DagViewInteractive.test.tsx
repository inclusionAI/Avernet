/** @jest-environment jsdom */
// DagView:任务级交互 DAG(定位/布局生长/平移/拖拽/滚轮缩放/工具栏/下钻热区/自动下露)。
// 入口覆盖:DagViewV2 view="interactive" 分支 + DagView 公开导出直挂。
import { C } from '@/assets/TaskPanel/tokens';
import { DagView } from '@/assets/TaskPanel/DagView';
import { DagViewV2 } from '@/assets/TaskPanel/v2/DagViewV2';
import type { DagEdgeView, DagNodeView } from '@/assets/TaskPanel/types';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

// ---------- fixture ----------
const node = (n: Partial<DagNodeView>): DagNodeView => ({
  id: 'n-1',
  label: '规划',
  status: 'done',
  x: 0,
  y: 0,
  isCurrent: false,
  ...n,
});

/** 深链:c-i 依次父子相连,y 每 100 一层(8 层时底部会超出 600 高的视口,触发自动下露) */
const chain = (count: number): DagNodeView[] =>
  Array.from({ length: count }, (_, i) => node({ id: `c-${i}`, label: `阶段${i}`, status: 'pending', y: i * 100 }));
const chainEdges = (count: number): DagEdgeView[] =>
  Array.from({ length: count - 1 }, (_, i) => ({ from: `c-${i}`, to: `c-${i + 1}` }));

const svgEl = () => document.querySelector('svg')!;
/** 世界坐标系外层 g(承载 base+pan+scale 变换) */
const graphG = () => document.querySelector('svg > g[transform]')!;
/** 节点卡片 g(aria-label 取完整 label,正文超 10 字截断但 aria 不截) */
const nodeG = (label: string) => document.querySelector(`g[aria-label="查看节点 ${label}"]`)!;
/** 连线分组(按 dagEdges 顺序排布;ghost 边产出 null 不渲染) */
const edgeGroups = () =>
  Array.from(document.querySelectorAll('svg > g[transform] > g')).filter((g) => !g.hasAttribute('role'));

/** 等待 rAF 帧触发组件的自动下露 effect */
const flushFrames = async (frames: number) => {
  for (let i = 0; i < frames; i++) {
    await act(async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
    });
  }
};

beforeEach(() => {
  // jsdom 无 ResizeObserver:注入桩让 observe/disconnect 分支可达
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
  // jsdom rect 全 0:固定 800x600,让 useLayoutEffect 的 measure 写入 view(baseX=400/baseY=56)
  jest
    .spyOn(Element.prototype, 'getBoundingClientRect')
    .mockReturnValue({ width: 800, height: 600, top: 0, left: 0, bottom: 600, right: 800, x: 0, y: 0 } as DOMRect);
});

afterEach(() => {
  jest.restoreAllMocks();
  delete (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver;
});

describe('DagView 挂载与渲染', () => {
  it('空 DAG 数据 → 渲染空态占位', () => {
    render(<DagView dagNodes={[]} dagEdges={[]} />);
    expect(screen.getByText('暂无 DAG 数据')).toBeTruthy();
    expect(document.querySelector('svg')).toBeNull();
  });

  it('入口:DagViewV2 view="interactive" 挂载 DagView,selectedId 透传为选中描边', () => {
    render(
      <DagViewV2 view="interactive" nodes={[node({ id: 'n-1', label: '需求澄清与规划' })]} edges={[]} selectedId="n-1" />,
    );
    expect(screen.getByText('需求澄清与规划')).toBeTruthy();
    expect(nodeG('需求澄清与规划').querySelector('rect')!.getAttribute('stroke-width')).toBe('2.5');
  });

  it('基础渲染:图例/状态色/动画/边语义/截断标题/序号角标', () => {
    const nodes = [
      node({ id: 'n-root', label: '需求澄清与规划' }),
      node({ id: 'n-run', label: '并行调研执行测试超长标题', status: 'running', y: 100, drillable: true, isCurrent: true }),
      node({ id: 'n-sib', label: '旁支节点', status: 'pending', y: 100 }),
      node({ id: 'n-failed', label: '写作节点', status: 'failed', y: 200 }),
      node({ id: 'n-run2', label: '复核发布', status: 'running', y: 300 }),
    ];
    const edges: DagEdgeView[] = [
      // V2 语义边:rework → warning 虚线 + reason title
      { from: 'n-root', to: 'n-run', semantics: 'rework', reason: 'Owner Bot 裁决 revise: 返工' },
      // 同层旁支(pending 目标):跨列曲线 + 弱化样式
      { from: 'n-root', to: 'n-sib' },
      { from: 'n-run', to: 'n-failed' },
      // 运行中目标:连线加粗 + 流动动画
      { from: 'n-failed', to: 'n-run2' },
      // 端点缺失的边:渲染 null
      { from: 'n-ghost', to: 'n-missing' },
      // 第二条进入 n-failed 的边:命中 childParents 的 arr.push 合并分支
      { from: 'n-sib', to: 'n-failed' },
    ];
    const onNodeDrill = jest.fn();
    render(
      <DagView dagNodes={nodes} dagEdges={edges} selectedNodeId="n-run" drilledNodeId="n-failed" onNodeDrill={onNodeDrill} />,
    );

    // 图例六态齐全
    for (const label of ['已完成', '执行中', '失败', '待执行', '任务挂起', '已取消']) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    // 超长标题截断(aria-label 保留全文):slice(0,10) = 并行调研执行测试超长
    expect(screen.getByText('并行调研执行测试超长…')).toBeTruthy();
    expect(screen.getByText('#-run')).toBeTruthy(); // 序号角标取 id 后 4 位
    // running 节点:外发光圈呼吸动画;选中态描边加粗
    const runRects = nodeG('并行调研执行测试超长标题').querySelectorAll('rect');
    expect(runRects[0].getAttribute('fill')).toBe('none'); // 发光圈
    expect(runRects[0].style.animation).toContain('task-panel-dag-pulse');
    expect(runRects[1].getAttribute('stroke-width')).toBe('2.5'); // selectedNodeId 选中卡片
    expect(runRects[1].getAttribute('stroke')).toBe(C.primary);
    // 下钻节点 drilledNodeId 同样走 primary 加粗
    expect(nodeG('写作节点').querySelector('rect')!.getAttribute('stroke')).toBe(C.primary);
    // isCurrent 文案加粗
    expect(nodeG('并行调研执行测试超长标题').querySelector('text')!.getAttribute('font-weight')).toBe('600');
    // 复用 StatusDot 的 foreignObject
    expect(nodeG('复核发布').querySelector('foreignObject span')).toBeTruthy();

    // ghost 边被丢弃:仅 5 组连线
    const groups = edgeGroups();
    expect(groups.length).toBe(5);
    // 语义边:warning 虚线 + reason title + 无流动动画
    const semanticPath = groups[0].querySelector('path')!;
    expect(semanticPath.getAttribute('stroke')).toBe(C.warning);
    expect(semanticPath.getAttribute('stroke-dasharray')).toBe('4 3');
    expect(semanticPath.getAttribute('opacity')).toBe('0.9');
    expect(semanticPath.querySelector('title')!.textContent).toBe('Owner Bot 裁决 revise: 返工');
    expect(semanticPath.style.animation).toBe('');
    // 同列直角边(L)与跨列曲线边(C)
    expect(semanticPath.getAttribute('d')).toContain(' L ');
    expect((groups[1].querySelector('path') as SVGPathElement).getAttribute('d')).toContain(' C ');
    // pending 目标边:弱化透明度/虚线
    const sibPath = groups[1].querySelector('path')!;
    expect(sibPath.getAttribute('opacity')).toBe('0.5');
    expect(sibPath.getAttribute('stroke-dasharray')).toBe('5 3');
    // failed 目标边:danger 实线
    expect(groups[2].querySelector('path')!.getAttribute('stroke')).toBe(C.danger);
    // running 目标边:加粗 + 流动动画
    const activePath = groups[3].querySelector('path')!;
    expect(activePath.getAttribute('stroke-width')).toBe('2');
    expect(activePath.style.animation).toContain('dag-flow');
    // 跨列汇入边(sib→failed):曲线 + danger
    const mergePath = groups[4].querySelector('path')!;
    expect(mergePath.getAttribute('d')).toContain(' C ');
    expect(mergePath.getAttribute('stroke')).toBe(C.danger);
  });

  it('拓扑:子节点先于父节点落位时自动补齐父节点,且兄弟节点横向错位生长', () => {
    // 父 y=100 > 子 y=0 → order 先处理子节点,父节点在循环内补齐占位
    const edges: DagEdgeView[] = [
      { from: 'n-parent', to: 'n-kid-a' },
      { from: 'n-parent', to: 'n-kid-b' },
    ];
    const nodes = [
      node({ id: 'n-parent', label: '母节点', y: 100 }),
      node({ id: 'n-kid-a', label: '子甲', y: 0 }),
      node({ id: 'n-kid-b', label: '子乙', y: 0 }),
    ];
    render(<DagView dagNodes={nodes} dagEdges={edges} />);
    // 三个节点都渲染出来(父节点由循环补齐,不再前置空缺)
    for (const label of ['母节点', '子甲', '子乙']) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    // 子甲 ord=0 与父同列,子乙 ord=1 右移一档 → 跨列曲线
    expect(edgeGroups()[1].querySelector('path')!.getAttribute('d')).toContain(' C ');
  });

  it('rerender 移除已有节点 → placedRef 中失效坐标被清理,剩余节点渲染', () => {
    const nodes = [
      node({ id: 'a', label: '甲节点' }),
      node({ id: 'b', label: '乙节点', y: 100 }),
      node({ id: 'c', label: '丙节点', y: 200 }),
    ];
    const { rerender } = render(
      <DagView dagNodes={nodes} dagEdges={[{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }]} />,
    );
    expect(screen.getByText('丙节点')).toBeTruthy();
    rerender(<DagView dagNodes={nodes.slice(0, 2)} dagEdges={[{ from: 'a', to: 'b' }]} />);
    expect(screen.queryByText('丙节点')).toBeNull();
    expect(screen.getByText('乙节点')).toBeTruthy();
  });
});

describe('DagView 节点交互(拖拽/点击/键盘)', () => {
  const mountPair = (props: Record<string, unknown> = {}) =>
    render(
      <DagView
        dagNodes={[node({ id: 'n-root', label: '甲节点' }), node({ id: 'n-child', label: '乙节点', y: 100 })]}
        dagEdges={[{ from: 'n-root', to: 'n-child' }]}
        {...props}
      />,
    );

  it('按下未拖动即松开 → onNodeClick(新语义);legacy 无 onNodeClick 时走 onViewNodeDetail', () => {
    const onNodeClick = jest.fn();
    const { rerender } = mountPair({ onNodeClick });
    fireEvent.mouseDown(nodeG('甲节点'), { clientX: 300, clientY: 200 });
    fireEvent.mouseUp(svgEl());
    expect(onNodeClick).toHaveBeenCalledWith('n-root');
    expect(svgEl().style.cursor).toBe('default'); // 松开后光标复位

    const onViewNodeDetail = jest.fn();
    rerender(
      <DagView
        dagNodes={[node({ id: 'n-root', label: '甲节点' }), node({ id: 'n-child', label: '乙节点', y: 100 })]}
        dagEdges={[{ from: 'n-root', to: 'n-child' }]}
        onViewNodeDetail={onViewNodeDetail}
      />,
    );
    fireEvent.mouseDown(nodeG('乙节点'), { clientX: 300, clientY: 300 });
    fireEvent.mouseLeave(svgEl()); // 鼠标离开画布等价于松开
    expect(onViewNodeDetail).toHaveBeenCalledWith(expect.objectContaining({ id: 'n-child' }));
  });

  it('拖动超过 5px 移动后松开 → 不触发点击,节点按光标位移重排', () => {
    const onNodeClick = jest.fn();
    mountPair({ onNodeClick });
    fireEvent.mouseDown(nodeG('甲节点'), { clientX: 300, clientY: 200 });
    expect(svgEl().style.cursor).toBe('grabbing'); // 拖拽中光标
    fireEvent.mouseMove(svgEl(), { clientX: 350, clientY: 200 });
    fireEvent.mouseUp(svgEl());
    expect(onNodeClick).not.toHaveBeenCalled();
    // 世界坐标换算:告警位移 50px 减 baseX=400 偏移 → 节点 x 从 -75 移到 -25
    expect(nodeG('甲节点').querySelector('rect')!.getAttribute('x')).toBe('-25');
  });

  it('键盘 Enter/空格 触达节点,onNodeClick 优先于 onViewNodeDetail', () => {
    const onNodeClick = jest.fn();
    const { rerender } = mountPair({ onNodeClick });
    fireEvent.keyDown(nodeG('甲节点'), { key: 'Enter' });
    expect(onNodeClick).toHaveBeenCalledWith('n-root');
    fireEvent.keyDown(nodeG('甲节点'), { key: ' ' });
    expect(onNodeClick).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(nodeG('甲节点'), { key: 'a' }); // 无关键不响应
    expect(onNodeClick).toHaveBeenCalledTimes(2);

    const onViewNodeDetail = jest.fn();
    rerender(
      <DagView
        dagNodes={[node({ id: 'n-root', label: '甲节点' }), node({ id: 'n-child', label: '乙节点', y: 100 })]}
        dagEdges={[{ from: 'n-root', to: 'n-child' }]}
        onViewNodeDetail={onViewNodeDetail}
      />,
    );
    fireEvent.keyDown(nodeG('乙节点'), { key: 'Enter' });
    expect(onViewNodeDetail).toHaveBeenCalledWith(expect.objectContaining({ id: 'n-child' }));
  });
});

describe('DagView 画布交互(平移/缩放/工具栏)', () => {
  it('按下画布空白处拖动 → 整体 pan 平移', () => {
    render(<DagView dagNodes={[node({ id: 'n-root', label: '甲节点' })]} dagEdges={[]} />);
    expect(graphG().getAttribute('transform')).toBe('translate(400, 56) scale(1)');
    fireEvent.mouseDown(svgEl(), { clientX: 100, clientY: 100 }); // 目标 tag=svg
    fireEvent.mouseMove(svgEl(), { clientX: 150, clientY: 120 });
    expect(graphG().getAttribute('transform')).toBe('translate(450, 76) scale(1)');
    fireEvent.mouseUp(svgEl());

    // 空白画布点击后无节点指针,松开不触发任何节点回调
    expect(document.querySelector('g[aria-label="查看节点 甲节点"]')).toBeTruthy();
  });

  it('原生 wheel 事件:向下滚 0.9 倍,向上滚 1.1 倍', () => {
    render(<DagView dagNodes={[node({ id: 'n-root', label: '甲节点' })]} dagEdges={[]} />);
    fireEvent.wheel(svgEl(), { deltaY: 120, clientX: 100, clientY: 100 });
    expect(screen.getByText('90%')).toBeTruthy();
    // 缩放作用于当前 scale:0.9 * 1.1 = 0.99 → 99%
    fireEvent.wheel(svgEl(), { deltaY: -120, clientX: 100, clientY: 100 });
    expect(screen.getByText('99%')).toBeTruthy();
  });

  it('工具栏:放大/缩小/重置 + hover 样式 + 双击画布重置', () => {
    render(<DagView dagNodes={[node({ id: 'n-root', label: '甲节点' })]} dagEdges={[]} />);
    fireEvent.click(screen.getByRole('button', { name: '+' }));
    expect(screen.getByText('120%')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '−' }));
    expect(screen.getByText('100%')).toBeTruthy(); // 1.2 * 0.83 = 0.996 → 100%

    // 工具按钮 hover 高亮/离开复位
    const zoomIn = screen.getByRole('button', { name: '+' });
    fireEvent.mouseEnter(zoomIn);
    expect(zoomIn.getAttribute('style')).toContain('background');
    fireEvent.mouseLeave(zoomIn);
    expect(zoomIn.getAttribute('style')).toContain('none');

    // 双击画布重置:先缩放再回正
    fireEvent.wheel(svgEl(), { deltaY: -120, clientX: 100, clientY: 100 });
    expect(screen.getByText('110%')).toBeTruthy();
    fireEvent.dblClick(svgEl());
    expect(screen.getByText('100%')).toBeTruthy();

    // ⊙ 重置按钮同样回到 100%
    fireEvent.click(screen.getByRole('button', { name: '⊙' }));
    expect(screen.getByText('100%')).toBeTruthy();
  });

  it('下钻热区:mousedown 截断节点拖起,click 只触发 onNodeDrill', () => {
    const onNodeDrill = jest.fn();
    const onNodeClick = jest.fn();
    const { unmount } = render(
      <DagView
        dagNodes={[node({ id: 'n-root', label: '甲节点' }), node({ id: 'n-run', label: '乙节点', y: 100, drillable: true })]}
        dagEdges={[{ from: 'n-root', to: 'n-run' }]}
        onNodeDrill={onNodeDrill}
        onNodeClick={onNodeClick}
      />,
    );
    const hot = document.querySelector('[aria-label="下钻节点副屏"]')!;
    expect(hot).toBeTruthy();
    fireEvent.mouseDown(hot, { clientX: 300, clientY: 300 });
    fireEvent.click(hot);
    expect(onNodeDrill).toHaveBeenCalledWith('n-run');
    expect(onNodeClick).not.toHaveBeenCalled(); // 拖拽状态未建立,连点不走 onNodeClick

    // 同一用例内先卸载前一次渲染,再验证未提供 onNodeDrill 时不渲染热区
    unmount();
    render(<DagView dagNodes={[node({ id: 'n-run2', label: '丙节点', drillable: true })]} dagEdges={[]} />);
    expect(document.querySelectorAll('[aria-label="下钻节点副屏"]').length).toBe(0);
  });
});

describe('DagView 自动下露与响应式', () => {
  it('出现超出视口的底层节点时,未交互场景最小化下移', async () => {
    render(<DagView dagNodes={chain(8)} dagEdges={chainEdges(8)} />);
    await flushFrames(2); // 第一帧下露;第二帧校验 lastBottom 后保持稳定
    // 第 7 层 y=700 + NODE_H 52 + baseY 56 → 超出视口 584 一段,pan.y = -224
    expect(graphG().getAttribute('transform')).toBe('translate(400, -168) scale(1)');
    expect(screen.getByText('100%')).toBeTruthy();
  });

  it('浅层 DAG 底部可见 → 不做下移', async () => {
    render(<DagView dagNodes={chain(2)} dagEdges={chainEdges(2)} />);
    await flushFrames(1);
    expect(graphG().getAttribute('transform')).toBe('translate(400, 56) scale(1)');
  });

  it('用户手动平移后停止自动下露;底部遮盖不再拉动画布', async () => {
    const deep = (): { dagNodes: DagNodeView[]; dagEdges: DagEdgeView[] } => ({
      dagNodes: chain(8),
      dagEdges: chainEdges(8),
    });
    const shallow = { dagNodes: chain(2), dagEdges: chainEdges(2) };
    const { rerender } = render(<DagView {...shallow} />);
    await flushFrames(1);
    fireEvent.mouseDown(svgEl(), { clientX: 100, clientY: 100 }); // userInteracted = true
    fireEvent.mouseMove(svgEl(), { clientX: 140, clientY: 130 }); // 用户平移 (40, 30)
    expect(graphG().getAttribute('transform')).toBe('translate(440, 86) scale(1)');

    rerender(<DagView {...deep()} />);
    await flushFrames(2);
    // 交互后不再自动下露:保持用户的 pan
    expect(graphG().getAttribute('transform')).toBe('translate(440, 86) scale(1)');
  });

  it('环境缺少 requestAnimationFrame 时同步执行下露逻辑', () => {
    const g = globalThis as unknown as { requestAnimationFrame?: unknown };
    const original = g.requestAnimationFrame;
    g.requestAnimationFrame = undefined;
    try {
      render(<DagView dagNodes={chain(8)} dagEdges={chainEdges(8)} />);
      // layout effect 测得 800x600 后,revealLatest 在 effect 内同步执行 → 直接下露
      expect(graphG().getAttribute('transform')).toBe('translate(400, -168) scale(1)');
    } finally {
      g.requestAnimationFrame = original;
    }
  });

  it('环境缺少 ResizeObserver 时:仅执行一次初始 measure,不挂观察器直接返回', () => {
    // 卸掉 beforeEach 注入的桩 → useLayoutEffect 中 typeof 检查命中提前 return 分支
    const g = globalThis as unknown as { ResizeObserver?: unknown };
    const stub = g.ResizeObserver;
    g.ResizeObserver = undefined;
    try {
      render(<DagView dagNodes={chain(2)} dagEdges={chainEdges(2)} />);
      expect(screen.getByText('阶段0')).toBeTruthy(); // 初始 measure 仍完成,视图可用
    } finally {
      g.ResizeObserver = stub;
    }
  });

  it('节点清空后 rerender:自动下露对空列表哨兵(maxBottom=-Infinity)直接返回', async () => {
    const { rerender } = render(<DagView dagNodes={chain(2)} dagEdges={chainEdges(2)} />);
    await flushFrames(1); // view 已测得,清空节点后 reveal 仍会被 effect 调用
    rerender(<DagView dagNodes={[]} dagEdges={[]} />);
    expect(screen.getByText('暂无 DAG 数据')).toBeTruthy();
    // 该帧内 revealLatest 对空 propNodes 走 maxBottom=-Infinity 哨兵返回
    await flushFrames(2);
  });
});
