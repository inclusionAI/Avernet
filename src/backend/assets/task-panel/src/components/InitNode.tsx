/**
 * InitNode — renders the "初始化任务节点" when the task graph has no execution
 * nodes yet (DRAFTING / DEFINED before spawn_build_dag). Shows a single centered
 * synthetic node: the task title + status badge, so the canvas is never
 * empty on open. Transitions naturally to the real DAG once nodes populate.
 */
import React from 'react';

import { NODE_HEIGHT, NODE_WIDTH, PADDING } from '../constants';
import type { TaskGraphView } from '../types';
import { getTaskStatusLabel, getTaskStatusTone } from '../utils/statusTone';
import { StatusPill } from '../utils/render';

export function InitNode({
  graph,
}: {
  graph: TaskGraphView;
}): React.ReactElement {
  const tone = getTaskStatusTone(graph.status);
  const title =
    (graph.definition_meta?.title as string | undefined) || graph.task_id;
  const width = NODE_WIDTH * 2 + PADDING * 2;
  const height = NODE_HEIGHT + PADDING * 2;
  const x = (width - NODE_WIDTH) / 2;
  const y = PADDING;

  return (
    <div style={{ width: '100%', textAlign: 'center', marginTop: 24 }}>
      <div style={{ marginBottom: 12 }}>
        <StatusPill tone={tone} label={getTaskStatusLabel(graph.status)} />
      </div>
      <svg
        width={width}
        height={height}
        style={{ display: 'block', margin: '0 auto', maxWidth: '100%' }}
      >
        <rect
          x={x}
          y={y}
          width={NODE_WIDTH}
          height={NODE_HEIGHT}
          rx={8}
          ry={8}
          fill={tone.fill}
          stroke={tone.stroke}
          strokeWidth={1.5}
          strokeDasharray="4 3"
        />
        <circle cx={x + NODE_WIDTH - 12} cy={y + 12} r={7} fill={tone.stroke} stroke="#fff" strokeWidth={1.5} />
        <text
          x={x + 12}
          y={y + 26}
          fontSize={13}
          fontWeight={600}
          fill="#1e293b"
          style={{ userSelect: 'none' }}
        >
          {title.length > 16 ? `${title.slice(0, 15)}…` : title}
        </text>
        <text x={x + 12} y={y + 44} fontSize={11} fill={tone.text} style={{ userSelect: 'none' }}>
          {graph.status === 'drafting'
            ? '要素补全中…'
            : graph.status === 'defined'
              ? '等待确认执行…'
              : '初始化中…'}
        </text>
      </svg>
      <div style={{ marginTop: 12, fontSize: 12, color: '#94a3b8' }}>
        每 3s 自动刷新 · 计划确认后将展开执行节点
      </div>
    </div>
  );
}
