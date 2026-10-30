// @asset-migrated: teamclaw 自研资产
/**
 * DagViewV2 —— 双模式 DAG 渲染:
 * - interactive:任务总览 L1 交互图,复用既有 DagView(拖拽/缩放),由 DagView 扩展的
 *   边语义渲染(rework/split_work = warning 虚线 + reason title)与下钻热区承担 PRD 语义;
 * - readonly-exec:节点内执行流程(execDag,action_log 派生)的只读小图,静态分层纵向布局,
 *   不可点、不可选中、不可再下钻(两级栈封顶,Demo 同源约束)。
 */
import React from 'react';

import { DagView } from '../DagView';
import type { ExecDagEdge, ExecDagStep } from '../statusDerivation';
import { C } from '../tokens';
import type { DagEdgeView, DagNodeView } from '../types';

export interface DagViewV2Props {
  view: 'interactive' | 'readonly-exec';
  /** interactive 模式:任务级 dagNodes/dagEdges(mapper 已 enrich semantics/present/drillable) */
  nodes?: DagNodeView[];
  edges?: DagEdgeView[];
  selectedId?: string | null;
  onNodeClick?: (nodeId: string) => void;
  onNodeDrill?: (nodeId: string) => void;
  /** readonly-exec 模式:action_log 派生的执行步骤图 */
  steps?: ExecDagStep[];
  stepEdges?: ExecDagEdge[];
}

const EXEC_NODE_W = 148;
const EXEC_NODE_H = 40;
const EXEC_Y_GAP = 26;

interface ExecPos {
  id: string;
  label: string;
  x: number;
  y: number;
}

/** execDag 静态分层:按入度做最长路径分层(没有坐标数据);同层横排。 */
function layoutExecDag(steps: ExecDagStep[], stepEdges: ExecDagEdge[]): { boxes: ExecPos[]; width: number; height: number } {
  const indeg = new Map<string, number>();
  steps.forEach((s) => indeg.set(s.id, 0));
  stepEdges.forEach((e) => indeg.set(e.to, (indeg.get(e.to) ?? 0) + 1));

  // 最长路径分层:level(n) = max(level(前驱)) + 1;入度 0 为第 0 层
  const level = new Map<string, number>();
  const indegC = new Map(indeg);
  const queue = steps.filter((s) => indeg.get(s.id) === 0);
  queue.forEach((s) => level.set(s.id, 0));
  let guard = 0;
  while (queue.length && guard++ < steps.length + 5) {
    const cur = queue.shift()!;
    const curLevel = level.get(cur.id)!;
    for (const e of stepEdges) {
      if (e.from !== cur.id) continue;
      if ((level.get(e.to) ?? -1) < curLevel + 1) level.set(e.to, curLevel + 1);
      const remain = indegC.get(e.to)! - 1;
      indegC.set(e.to, remain);
      if (remain === 0) queue.push(steps.find((s) => s.id === e.to)!);
    }
  }
  const levelCount = new Map<number, number>();
  steps.forEach((s) => {
    const lv = level.get(s.id) ?? 0;
    levelCount.set(lv, (levelCount.get(lv) ?? 0) + 1);
  });
  // 纵向视图:层级沿 y 生长更贴近 demo;同层横排保持低宽
  const maxLevel = [...level.values()].reduce((m, v) => Math.max(m, v), 0);
  const perLevel = new Map<number, number>();
  const boxes: ExecPos[] = steps.map((s) => {
    const lv = level.get(s.id) ?? 0;
    const idx = perLevel.get(lv) ?? 0;
    perLevel.set(lv, idx + 1);
    const levelSize = levelCount.get(lv)!;
    const widthAtLevel = levelSize * EXEC_NODE_W + (levelSize - 1) * 8;
    const x = Math.max(10, 190 - widthAtLevel / 2) + idx * (EXEC_NODE_W + 8);
    return { id: s.id, label: s.label, x, y: 12 + lv * (EXEC_NODE_H + EXEC_Y_GAP) };
  });
  return { boxes, width: 380, height: 24 + (maxLevel + 1) * (EXEC_NODE_H + EXEC_Y_GAP) };
}

export const DagViewV2: React.FC<DagViewV2Props> = ({
  view,
  nodes = [],
  edges = [],
  selectedId,
  onNodeClick,
  onNodeDrill,
  steps = [],
  stepEdges = [],
}) => {
  if (view === 'interactive') {
    return (
      <DagView
        dagNodes={nodes}
        dagEdges={edges}
        selectedNodeId={selectedId ?? null}
        onNodeClick={onNodeClick}
        onNodeDrill={onNodeDrill}
      />
    );
  }

  // readonly-exec:节点内执行流程小图(静态,不可交互 —— L2 内无再下钻)
  if (!steps.length) return null;
  const { boxes, width, height } = layoutExecDag(steps, stepEdges);
  const posById = new Map(boxes.map((b) => [b.id, b]));
  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${width} ${height}`} data-testid="exec-dag-view" role="img" aria-label="节点内部执行流程(只读)">
      {stepEdges.map((e, i) => {
        const from = posById.get(e.from);
        const to = posById.get(e.to);
        if (!from || !to) return null;
        const x1 = from.x + EXEC_NODE_W / 2;
        const y1 = from.y + EXEC_NODE_H;
        const x2 = to.x + EXEC_NODE_W / 2;
        const y2 = to.y - 2;
        const midY = (y1 + y2) / 2;
        const rework = e.semantics === 'rework';
        return (
          <path
            key={i}
            d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
            fill="none"
            stroke={rework ? C.warning : C.border}
            strokeWidth={1.5}
            strokeDasharray={rework ? '4 3' : undefined}
            opacity={rework ? 0.9 : 0.7}
          />
        );
      })}
      {boxes.map((b) => (
        <g key={b.id}>
          <rect x={b.x} y={b.y} width={EXEC_NODE_W} height={EXEC_NODE_H} rx={8} fill={C.surface} stroke={C.border} />
          <text
            x={b.x + EXEC_NODE_W / 2}
            y={b.y + EXEC_NODE_H / 2 + 4}
            textAnchor="middle"
            fontSize={11}
            fill={C.textPrimary}
            style={{ userSelect: 'none', pointerEvents: 'none' }}
          >
            {b.label.length > 16 ? `${b.label.slice(0, 16)}…` : b.label}
          </text>
        </g>
      ))}
    </svg>
  );
};

export default DagViewV2;