/**
 * TaskWorkflowView — task execution workflow 副屏 panel (default export).
 *
 * Mounted by the SDK UmdPanel loader when the skill emits:
 *   <AixUI type="panel" component="taskPanel.TaskWorkflowView"
 *          cdn="<backend>/assets/task-panel/dist/index.umd.js"
 *          entry="TaskWorkflowView"
 *          tab='{"id":"task-<id>","title":"任务:<title>","closable":true}'
 *          params='{"taskId":"<id>"}'/>
 *
 * Behavior:
 * - resolve taskId from UmdPanel-injected props.
 * - useTaskGraph polls GET /api/tasks/{id}/graph every 3s while non-terminal.
 * - header: title + root_phase pill + graph_status + loop_round + manual refresh.
 * - empty nodes (DRAFTING/DEFINED) → InitNode (初始化任务节点); else GraphCanvas.
 * - click node → NodeDetailModal (GET /api/tasks/{id}/nodes/{node_id}).
 *
 * Render format mirrors bcsPanel.StateMachineRunView (SVG nodes/edges, status
 * tone, edge states). React/react-dom are externalized (provided by UmdPanel).
 */
import React, { useCallback, useState } from 'react';

import { resolveTaskId } from './api';
import type { TaskWorkflowViewProps } from './types';
import { useTaskGraph } from './useTaskGraph';
import { GraphCanvas } from './components/GraphCanvas';
import { InitNode } from './components/InitNode';
import { NodeDetailModal } from './components/NodeDetailModal';
import { ROOT_PHASE_TERMINAL } from './constants';
import {
  getGraphStatusLabel,
  getRootPhaseLabel,
  getRootPhaseTone,
} from './utils/statusTone';
import { StatusPill } from './utils/render';

function TaskWorkflowView(props: TaskWorkflowViewProps): React.ReactElement {
  const taskId = resolveTaskId(props);
  const autoRefresh = props.autoRefresh !== false;
  const pollingInterval = props.pollingInterval;
  const { graph, loading, error, refresh } = useTaskGraph(taskId, {
    autoRefresh,
    pollingInterval,
  });
  const [selectedNodeId, setSelectedNodeId] = useState<string | undefined>(undefined);

  const onNodeClick = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
  }, []);

  if (!taskId) {
    return (
      <div style={{ padding: 24, color: '#b91c1c', fontSize: 13 }}>
        未提供 taskId — 无法加载任务工作流。
      </div>
    );
  }

  const rootPhase = graph?.root_phase;
  const isTerminal = rootPhase ? ROOT_PHASE_TERMINAL.has(String(rootPhase)) : false;
  const title =
    (graph?.definition_meta?.title as string | undefined) || taskId;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: '#f8fafc' }}>
      {/* header */}
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid #e2e8f0',
          background: '#fff',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 600, color: '#0f172a', marginRight: 4 }}>
          {title}
        </div>
        {graph ? (
          <StatusPill tone={getRootPhaseTone(rootPhase)} label={getRootPhaseLabel(rootPhase)} />
        ) : null}
        {graph && getGraphStatusLabel(graph.graph_status) ? (
          <span style={{ fontSize: 12, color: '#b45309' }}>
            {getGraphStatusLabel(graph.graph_status)}
          </span>
        ) : null}
        {graph && graph.loop_round > 0 ? (
          <span style={{ fontSize: 12, color: '#64748b' }}>轮次 {graph.loop_round}</span>
        ) : null}
        <div style={{ flex: 1 }} />
        <button
          type="button"
          onClick={refresh}
          disabled={isTerminal}
          style={{
            fontSize: 12,
            padding: '4px 10px',
            borderRadius: 6,
            border: '1px solid #cbd5e1',
            background: '#fff',
            color: '#475569',
            cursor: isTerminal ? 'default' : 'pointer',
            opacity: isTerminal ? 0.5 : 1,
          }}
        >
          刷新
        </button>
      </div>

      {/* body */}
      <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
        {loading && !graph ? (
          <div style={{ textAlign: 'center', color: '#94a3b8', fontSize: 13, marginTop: 32 }}>
            加载任务工作流…
          </div>
        ) : error && !graph ? (
          <div style={{ textAlign: 'center', color: '#b91c1c', fontSize: 13, marginTop: 32 }}>
            加载失败:{error.message}
          </div>
        ) : graph ? (
          graph.nodes.length === 0 ? (
            <InitNode graph={graph} />
          ) : (
            <GraphCanvas
              graph={graph}
              selectedNodeId={selectedNodeId}
              onNodeClick={onNodeClick}
            />
          )
        ) : null}
      </div>

      {selectedNodeId && graph ? (
        <NodeDetailModal
          taskId={taskId}
          nodeId={selectedNodeId}
          rootPhase={rootPhase}
          onClose={() => setSelectedNodeId(undefined)}
        />
      ) : null}
    </div>
  );
}

export default TaskWorkflowView;
