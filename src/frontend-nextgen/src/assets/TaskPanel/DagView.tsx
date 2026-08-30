// @asset-migrated: teamclaw 自研资产
/** DAG 视图（竖向）：节点自上而下分层排列，边为竖向正交折线。
 * 交互：拖拽节点 / 平移画布 / 滚轮缩放 / 双击重置 / 工具栏。
 * 视觉：节点卡片化（状态色 + 图标 + 标签），连线带流动动画，running 发光，背景网格。
 */
import React, { useCallback, useRef, useState } from 'react';
import { NodeStatusDot } from './icons';
import { Empty } from './theme';
import { C, NODE_STATUS_TONES } from './tokens';
import type { DagEdgeView, DagNodeView } from './types';

const NODE_W = 150;
const NODE_H = 52;
const MIN_SCALE = 0.3;
const MAX_SCALE = 2.5;

// 节点状态 → 连线色
const EDGE_COLORS: Record<string, string> = {
  done: C.success,
  running: C.warning,
  failed: C.danger,
  skipped: C.textMuted,
  pending: C.border,
};

export const DagView: React.FC<{
  dagNodes: DagNodeView[];
  dagEdges: DagEdgeView[];
  selectedNodeId?: string | null;
  onViewNodeDetail?: (node: DagNodeView) => void;
}> = ({ dagNodes: propNodes, dagEdges, selectedNodeId, onViewNodeDetail }) => {
  const [nodes, setNodes] = useState<DagNodeView[]>(propNodes);
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);

  const dragRef = useRef<{ id: string; offsetX: number; offsetY: number } | null>(null);
  const nodePointerRef = useRef<{ node: DagNodeView; startX: number; startY: number; moved: boolean } | null>(null);
  const panRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  React.useEffect(() => {
    setNodes(propNodes);
  }, [propNodes]);

  const toSvgPoint = useCallback(
    (clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return null;
      const rect = svg.getBoundingClientRect();
      return {
        x: (clientX - rect.left - pan.x) / scale,
        y: (clientY - rect.top - pan.y) / scale,
      };
    },
    [pan, scale],
  );

  const handleNodeMouseDown = useCallback(
    (e: React.MouseEvent, node: DagNodeView) => {
      e.preventDefault();
      e.stopPropagation();
      const pt = toSvgPoint(e.clientX, e.clientY);
      if (!pt) return;
      dragRef.current = { id: node.id, offsetX: pt.x - node.x, offsetY: pt.y - node.y };
      nodePointerRef.current = { node, startX: e.clientX, startY: e.clientY, moved: false };
      setIsDragging(true);
    },
    [toSvgPoint],
  );

  const handleCanvasMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const tag = (e.target as SVGElement).tagName;
      if (tag === 'svg' || tag === 'rect' || tag === 'pattern' || tag === 'circle') {
        e.preventDefault();
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
        setNodes((prev) =>
          prev.map((n) => (n.id === drag.id ? { ...n, x: pt.x - drag.offsetX, y: pt.y - drag.offsetY } : n)),
        );
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
    (e: React.WheelEvent) => {
      e.preventDefault();
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      const ns = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * delta));
      const lx = (mx - pan.x) / scale;
      const ly = (my - pan.y) / scale;
      setPan({ x: mx - lx * ns, y: my - ly * ns });
      setScale(ns);
    },
    [pan, scale],
  );

  const handleReset = useCallback(() => {
    setScale(1);
    setPan({ x: 0, y: 0 });
    setNodes(propNodes);
  }, [propNodes]);

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
    { status: 'skipped', label: '已跳过' },
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
        onWheel={handleWheel}
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

        <g transform={`translate(${pan.x}, ${pan.y}) scale(${scale})`}>
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
                    : item.status === 'skipped'
                    ? C.textMuted
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
          { label: '+', action: () => setScale((s) => Math.min(MAX_SCALE, s * 1.2)) },
          { label: '−', action: () => setScale((s) => Math.max(MIN_SCALE, s * 0.83)) },
          { label: '⊙', action: handleReset },
        ].map((btn, i) => (
          <button
            key={i}
            type="button"
            onClick={btn.action}
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
