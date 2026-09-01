// @asset-migrated: teamclaw 自研资产
/** DAG 视图（竖向）：节点自上而下分层排列，边为竖向正交折线。
 * 交互：拖拽节点 / 平移画布 / 滚轮缩放 / 双击重置 / 工具栏。
 * 视觉：节点卡片化（状态色 + 图标 + 标签），连线带流动动画，running 发光，背景网格。
 *
 * 居中策略（响应式）：
 *  - 容器尺寸(view.w/view.h)在 useLayoutEffect + ResizeObserver 中实时测量，
 *    「基准变换」在每次 render 由尺寸现算，所以缩放副屏/拉窗口都能即时把根节点
 *    重新对到画布水平中心、图例栏下方。
 *  - pan 仅承载「用户手动平移/缩放」的偏移，初值 {0,0}；不靠一次性 setPan 居中，
 *    避免挂载时尺寸为 0 / rAF 抖动导致先左闪再回正。
 *  - 世界坐标：根节点中心落在 world x=0、顶部落在 world y=0；旧「placedRef 锁定」
 *    语义保持不变——节点一旦落位即不再重排。
 */
import React, { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { NodeStatusDot } from './icons';
import { Empty } from './theme';
import { C, NODE_STATUS_TONES } from './tokens';
import type { DagEdgeView, DagNodeView } from './types';

const NODE_W = 150;
const NODE_H = 52;
const MIN_SCALE = 0.3;
const MAX_SCALE = 2.5;
// 给左上角图例留出的顶部带(legend top:12 + 自身高 + 间距)，节点顶部对齐到这里，避免与图例相互遮挡。
const TOP_BAND = 56;

// 节点状态 → 连线色
const EDGE_COLORS: Record<string, string> = {
  done: C.success,
  running: C.warning,
  failed: C.danger,
  hung: C.primary,
  cancelled: C.border,
  pending: C.border,
};

export const DagView: React.FC<{
  dagNodes: DagNodeView[];
  dagEdges: DagEdgeView[];
  selectedNodeId?: string | null;
  onViewNodeDetail?: (node: DagNodeView) => void;
}> = ({ dagNodes: propNodes, dagEdges, selectedNodeId, onViewNodeDetail }) => {
  const [scale, setScale] = useState(1);
  // pan 仅表示用户偏移；居中由 render 时的 base(view.w/2, TOP_BAND) 承担。
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  // 容器实时尺寸（用于 render 时算居中基准 + 缩放/下露坐标换算）。
  const [view, setView] = useState({ w: 0, h: 0 });
  // 用户是否手动平移/缩放过；为 true 后停止自动下露，点「重置」恢复。
  const userInteractedRef = useRef(false);
  // 已放置节点「世界坐标」缓存：id -> {x,y}。一旦落位即锁定。
  const placedRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  // 根节点的原始 y（mapper 给根的 y 非零，如 Y_GAP=40）；用它把根顶部归零到 world y=0。
  const originYRef = useRef<number | null>(null);
  // 已「露过」的最底 world-y；仅当出现更底的节点才最小下露，避免空闲轮询/重置后乱动。
  const lastBottomYRef = useRef(-Infinity);
  // 拖拽改 placedRef 后用它强制重渲染。
  const [tick, setTick] = useState(0);

  const dragRef = useRef<{ id: string; offsetX: number; offsetY: number } | null>(null);
  const nodePointerRef = useRef<{ node: DagNodeView; startX: number; startY: number; moved: boolean } | null>(null);
  const panRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  // 子 -> 父 列表（取首个父用于"父列偏移生长"）。
  const childParents = React.useMemo(() => {
    const m = new Map<string, string[]>();
    for (const e of dagEdges) {
      const arr = m.get(e.to);
      if (arr) arr.push(e.from);
      else m.set(e.to, [e.from]);
    }
    return m;
  }, [dagEdges]);

  // 合成「带锁定位」的展示节点。根节点中心对齐 world x=0、顶部对齐 world y=0；
  // 子节点 = 父中心向右偏移 ord × SIBLING_STEP，层级沿 y 自上而下生长。
  // 「已展示位置不再动」，只在顶部居中轴上自上而下动态生长。
  const nodes: DagNodeView[] = React.useMemo(() => {
    const step = NODE_W + 32;
    const ids = new Set(propNodes.map((n) => n.id));
    placedRef.current.forEach((_, id) => {
      if (!ids.has(id)) placedRef.current.delete(id);
    });
    const byId = new Map(propNodes.map((n) => [n.id, n]));
    const order = [...propNodes].sort((a, b) => a.y - b.y);
    // 全新 DAG（placedRef 被清空，即切换了任务）：以最低 y 节点为根重新捕获 origin。
    if (placedRef.current.size === 0 && order.length) {
      originYRef.current = order[0].y;
    }
    for (const n of order) {
      if (placedRef.current.has(n.id)) continue;
      const pId = (childParents.get(n.id) ?? [])[0];
      if (pId) {
        if (!placedRef.current.has(pId)) {
          const pp = byId.get(pId);
          if (originYRef.current === null) originYRef.current = pp?.y ?? n.y;
          placedRef.current.set(pId, {
            x: -NODE_W / 2,
            y: (pp?.y ?? n.y) - (originYRef.current ?? 0),
          });
        }
        let ord = 0;
        placedRef.current.forEach((_, idv) => {
          if ((childParents.get(idv) ?? [])[0] === pId) ord += 1;
        });
        const pp = placedRef.current.get(pId)!;
        placedRef.current.set(n.id, {
          x: pp.x + ord * step,
          y: n.y - (originYRef.current ?? n.y),
        });
      } else {
        if (originYRef.current === null) originYRef.current = n.y;
        placedRef.current.set(n.id, { x: -NODE_W / 2, y: n.y - originYRef.current });
      }
    }
    return propNodes.map((n) => {
      const pos = placedRef.current.get(n.id) ?? { x: n.x, y: n.y };
      return { ...n, x: pos.x, y: pos.y };
    });
  }, [propNodes, dagEdges, childParents, tick]);

  // 渲染基准：根中心 → 画布水平中心；根顶部 → TOP_BAND(图例栏下方)。
  // 每次 render 都现算，所以容器尺寸一变就即时居中（响应式）。
  const baseX = view.w / 2;
  const baseY = TOP_BAND;

  // 一次性在首次 paint 前测容器尺寸 + 监听 resize 持续更新；无一次性居中抖动。
  useLayoutEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const measure = () => {
      const r = svg.getBoundingClientRect();
      if (r.width && r.height) setView({ w: r.width, h: r.height });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(svg);
    return () => ro.disconnect();
  }, []);

  // 出现更底层节点且被视口下边遮住时，最小化下滚让它露出；不改其它节点位置。
  const revealLatest = useCallback(() => {
    if (!view.w || !view.h) return;
    let maxBottom = -Infinity;
    for (const n of propNodes) {
      const p = placedRef.current.get(n.id);
      if (p) maxBottom = Math.max(maxBottom, p.y + NODE_H);
    }
    if (maxBottom === -Infinity) return;
    if (lastBottomYRef.current >= maxBottom) return; // 无新增底层 → 不动（含重置/空闲轮询）
    lastBottomYRef.current = maxBottom;
    const screenBottomY = maxBottom * scale + baseY + pan.y;
    const visibleBottom = view.h - 16;
    if (screenBottomY <= visibleBottom) return;
    setPan((p) => ({ ...p, y: p.y - (screenBottomY - visibleBottom) }));
  }, [propNodes, pan, scale, view, baseY]);

  // 图谱增长：执行中不跳；出现更底层且看不到时最小化下露。
  React.useEffect(() => {
    if (userInteractedRef.current) return;
    if (!view.w || !view.h) return;
    if (typeof requestAnimationFrame === 'undefined') {
      revealLatest();
      return;
    }
    const id = requestAnimationFrame(() => {
      if (!userInteractedRef.current) revealLatest();
    });
    return () => cancelAnimationFrame(id);
  }, [revealLatest, view]);

  const toSvgPoint = useCallback(
    (clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return null;
      const rect = svg.getBoundingClientRect();
      return {
        x: (clientX - rect.left - baseX - pan.x) / scale,
        y: (clientY - rect.top - baseY - pan.y) / scale,
      };
    },
    [pan, scale, baseX, baseY],
  );

  const handleNodeMouseDown = useCallback(
    (e: React.MouseEvent, node: DagNodeView) => {
      e.preventDefault();
      e.stopPropagation();
      const pt = toSvgPoint(e.clientX, e.clientY);
      if (!pt) return;
      dragRef.current = { id: node.id, offsetX: pt.x - node.x, offsetY: pt.y - node.y };
      nodePointerRef.current = { node, startX: e.clientX, startY: e.clientY, moved: false };
      userInteractedRef.current = true;
      setIsDragging(true);
    },
    [toSvgPoint],
  );

  const handleCanvasMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const tag = (e.target as SVGElement).tagName;
      if (tag === 'svg' || tag === 'rect' || tag === 'pattern' || tag === 'circle') {
        e.preventDefault();
        userInteractedRef.current = true;
        panRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
      }
    },
    [pan],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const drag = dragRef.current;
      if (drag) {
        const pointer = nodePointerRef.current;
        if (pointer && Math.hypot(e.clientX - pointer.startX, e.clientY - pointer.startY) > 5) {
          pointer.moved = true;
        }
        const pt = toSvgPoint(e.clientX, e.clientY);
        if (!pt) return;
        if (placedRef.current.has(drag.id)) {
          placedRef.current.set(drag.id, { x: pt.x - drag.offsetX, y: pt.y - drag.offsetY });
          setTick((t) => t + 1);
        }
        return;
      }
      const p = panRef.current;
      if (p) {
        setPan({ x: p.panX + (e.clientX - p.startX), y: p.panY + (e.clientY - p.startY) });
      }
    },
    [toSvgPoint],
  );

  const handleMouseUp = useCallback(() => {
    const pointer = nodePointerRef.current;
    if (pointer && !pointer.moved) {
      onViewNodeDetail?.(pointer.node);
    }
    dragRef.current = null;
    nodePointerRef.current = null;
    panRef.current = null;
    setIsDragging(false);
  }, [onViewNodeDetail]);

  const handleWheel = useCallback(
    (e: WheelEvent) => {
      e.preventDefault();
      userInteractedRef.current = true;
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      const ns = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * delta));
      // 缩放到光标处：保持光标下世界点不动。
      const lx = (mx - baseX - pan.x) / scale;
      const ly = (my - baseY - pan.y) / scale;
      setPan({ x: mx - baseX - lx * ns, y: my - baseY - ly * ns });
      setScale(ns);
    },
    [pan, scale, baseX, baseY],
  );

  // React onWheel 为 passive 监听器,preventDefault 无效(滚轮缩放时无法阻止外层页面滚动)。
  // 改用原生 addEventListener 注册非 passive wheel 监听,preventDefault 才生效。
  React.useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    svg.addEventListener('wheel', handleWheel, { passive: false });
    return () => svg.removeEventListener('wheel', handleWheel);
  }, [handleWheel]);

  const handleReset = useCallback(() => {
    // 「重置」= 回到顶部居中的初始观感：清交互、缩放 1、pan 归零；render 自动重居中。
    // 把已露的最底 y 标记为当前，避免重置完立刻被下露拉走。
    userInteractedRef.current = false;
    setScale(1);
    setPan({ x: 0, y: 0 });
    let g = -Infinity;
    placedRef.current.forEach((v) => {
      g = Math.max(g, v.y + NODE_H);
    });
    lastBottomYRef.current = g;
  }, []);

  if (!nodes.length) {
    return <Empty description="暂无 DAG 数据" minHeight={300} />;
  }

  const byId = new Map(nodes.map((n) => [n.id, n]));

  // 图例数据
  const legendItems = [
    { status: 'done', label: '已完成' },
    { status: 'running', label: '执行中' },
    { status: 'failed', label: '失败' },
    { status: 'pending', label: '待执行' },
    { status: 'hung', label: '任务挂起' },
    { status: 'cancelled', label: '已取消' },
  ];

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        overflow: 'hidden',
        background: `linear-gradient(135deg, ${C.surfaceAlt} 0%, #FAFBFC 100%)`,
      }}
    >
      <svg
        ref={svgRef}
        width="100%"
        height="100%"
        style={{ display: 'block', cursor: isDragging ? 'grabbing' : 'default' }}
        onMouseDown={handleCanvasMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onDoubleClick={handleReset}
      >
        <defs>
          <marker id="dag-arrow" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
            <path d="M 0 0 L 8 4 L 0 8 Z" fill={C.textMuted} />
          </marker>
          <pattern id="dag-grid" width="24" height="24" patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="0.8" fill={C.border} opacity={0.5} />
          </pattern>
          {/* running 节点发光滤镜 */}
          <filter id="dag-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          {/* 节点阴影 */}
          <filter id="dag-shadow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="2" stdDeviation="3" floodOpacity={0.1} />
          </filter>
        </defs>

        <rect width="100%" height="100%" fill="url(#dag-grid)" />

        <g transform={`translate(${baseX + pan.x}, ${baseY + pan.y}) scale(${scale})`}>
          {/* 连线 */}
          {dagEdges.map((e, i) => {
            const from = byId.get(e.from);
            const to = byId.get(e.to);
            if (!from || !to) return null;
            const x1 = from.x + NODE_W / 2;
            const y1 = from.y + NODE_H;
            const x2 = to.x + NODE_W / 2;
            const y2 = to.y - 2;
            const sameCol = Math.abs(x1 - x2) < 2;
            const midY = (y1 + y2) / 2;
            const d = sameCol
              ? `M ${x1} ${y1} L ${x2} ${y2}`
              : `M ${x1} ${y1} C ${x1} ${midY} ${x2} ${midY} ${x2} ${y2}`;
            // 目标节点状态决定连线色
            const edgeColor = EDGE_COLORS[to.status] ?? C.border;
            const isActive = to.status === 'running';
            return (
              <g key={i}>
                <path
                  d={d}
                  fill="none"
                  stroke={edgeColor}
                  strokeWidth={isActive ? 2 : 1.5}
                  opacity={to.status === 'pending' ? 0.5 : 0.8}
                  markerEnd="url(#dag-arrow)"
                  strokeDasharray={to.status === 'pending' ? '5 3' : undefined}
                  style={isActive ? { animation: 'dag-flow 1.5s linear infinite' } : undefined}
                />
              </g>
            );
          })}

          {/* 节点 */}
          {nodes.map((n) => {
            const tone = NODE_STATUS_TONES[n.status] ?? NODE_STATUS_TONES.pending;
            const isRunning = n.status === 'running';
            const isCurrent = n.isCurrent;
            return (
              <g
                key={n.id}
                role="button"
                tabIndex={0}
                aria-label={`查看节点 ${n.label}`}
                onMouseDown={(e) => handleNodeMouseDown(e, n)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onViewNodeDetail?.(n);
                  }
                }}
                style={{ cursor: 'pointer' }}
              >
                {/* running 外发光圈 */}
                {isRunning && (
                  <rect
                    x={n.x - 4}
                    y={n.y - 4}
                    width={NODE_W + 8}
                    height={NODE_H + 8}
                    rx={12}
                    ry={12}
                    fill="none"
                    stroke={C.warning}
                    strokeWidth={2}
                    opacity={0.4}
                    style={{ animation: 'task-panel-dag-pulse 1.5s ease-in-out infinite' }}
                  />
                )}
                {/* 节点卡片 */}
                <rect
                  x={n.x}
                  y={n.y}
                  width={NODE_W}
                  height={NODE_H}
                  rx={10}
                  ry={10}
                  fill={tone.fill}
                  stroke={selectedNodeId === n.id ? C.primary : isCurrent ? tone.stroke : `${tone.stroke}80`}
                  strokeWidth={selectedNodeId === n.id ? 2.5 : isCurrent ? 2 : 1.5}
                  filter="url(#dag-shadow)"
                />
                {/* 左侧状态色条 */}
                <rect x={n.x} y={n.y + 8} width={3} height={NODE_H - 16} rx={1.5} fill={tone.stroke} opacity={0.8} />
                {/* 状态圆点 */}
                <foreignObject x={n.x + 8} y={n.y + (NODE_H - 12) / 2} width={12} height={12}>
                  <NodeStatusDot status={n.status} size={12} />
                </foreignObject>
                {/* 节点文本 */}
                <text
                  x={n.x + 28}
                  y={n.y + NODE_H / 2 + 4}
                  textAnchor="start"
                  fontSize={12}
                  fontWeight={isCurrent ? 600 : 500}
                  fill={C.textPrimary}
                  style={{ pointerEvents: 'none', userSelect: 'none' }}
                >
                  {n.label.length > 10 ? `${n.label.slice(0, 10)}…` : n.label}
                </text>
                {/* 节点序号角标 */}
                <text
                  x={n.x + NODE_W - 10}
                  y={n.y + 14}
                  textAnchor="end"
                  fontSize={9}
                  fill={C.textMuted}
                  style={{ pointerEvents: 'none', userSelect: 'none' }}
                >
                  #{n.id.slice(-4)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      {/* 图例栏（左上角） */}
      <div
        style={{
          position: 'absolute',
          left: 12,
          top: 12,
          display: 'flex',
          gap: 12,
          alignItems: 'center',
          background: `${C.surface}EE`,
          backdropFilter: 'blur(8px)',
          border: `1px solid ${C.border}`,
          borderRadius: 8,
          padding: '6px 12px',
          fontSize: 11,
          color: C.textSecondary,
          zIndex: 10,
          boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
        }}
      >
        {legendItems.map((item) => (
          <div key={item.status} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background:
                  item.status === 'done'
                    ? C.success
                    : item.status === 'running'
                    ? C.warning
                    : item.status === 'failed'
                    ? C.danger
                    : item.status === 'hung'
                    ? C.primary
                    : item.status === 'cancelled'
                    ? C.border
                    : C.textMuted,
              }}
            />
            {item.label}
          </div>
        ))}
      </div>

      {/* 工具栏（右下角） */}
      <div
        style={{
          position: 'absolute',
          right: 8,
          bottom: 8,
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
          background: `${C.surface}EE`,
          backdropFilter: 'blur(8px)',
          border: `1px solid ${C.border}`,
          borderRadius: 8,
          padding: 4,
          zIndex: 10,
          boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
        }}
      >
        {[
          { label: '+', action: () => setScale((s) => Math.min(MAX_SCALE, s * 1.2)), interact: true },
          { label: '−', action: () => setScale((s) => Math.max(MIN_SCALE, s * 0.83)), interact: true },
          { label: '⊙', action: handleReset, interact: false },
        ].map((btn, i) => (
          <button
            key={i}
            type="button"
            onClick={() => {
              if (btn.interact) userInteractedRef.current = true;
              btn.action();
            }}
            style={{
              width: 30,
              height: 30,
              border: 'none',
              background: 'none',
              cursor: 'pointer',
              fontSize: 16,
              color: C.textSecondary,
              borderRadius: 6,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'background 0.15s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = C.surfaceAlt)}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
          >
            {btn.label}
          </button>
        ))}
      </div>

      {/* 缩放比例（左下角） */}
      <div
        style={{
          position: 'absolute',
          left: 8,
          bottom: 8,
          fontSize: 11,
          color: C.textMuted,
          background: `${C.surface}EE`,
          backdropFilter: 'blur(8px)',
          padding: '3px 8px',
          borderRadius: 6,
          border: `1px solid ${C.border}`,
          zIndex: 10,
        }}
      >
        {Math.round(scale * 100)}%
      </div>

      {/* 流动动画 keyframes */}
      <style>{`
        @keyframes dag-flow {
          to { stroke-dashoffset: -16; }
        }
      `}</style>
    </div>
  );
};
