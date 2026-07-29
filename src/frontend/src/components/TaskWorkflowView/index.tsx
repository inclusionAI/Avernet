/**
 * TaskWorkflowView — 副屏动态 workflow 画布 (Phase 4.5, plan §1.4b/FR-OBS-01~07,10)。
 *
 * 新建独立画布(参考 bcsPanel/StateMachineRunView 实现,不复用),消费
 * GET /api/tasks/{id}/graph (TaskGraphView)。按 run_mode/collab_mode 渲染模态
 * 标签;状态色映射对齐 §1.3c。点节点 → GET /nodes/{nid} 详情;协作群节点
 * 双击 → 下钻跨页导航(路 A)。轻量轮询 GET /graph 兜底 + WS /graph/stream 增量
 * (WS 增量 Phase 4.5.6 桩,此处轮询为主)。
 *
 * 作为副屏 panel (type=taskPanel.TaskWorkflowView) 与 TaskLoop 页面共用此组件。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import {
  getTaskGraph,
  getNodeDetail,
  type TaskGraphView,
  type TaskNodeDetailView,
  type TaskNodeView,
} from '@/services/backend-api/TaskController';
import {
  NODE_STATUS_COLOR,
  NODE_STATUS_LABEL,
  ROOT_PHASE_COLOR,
  isCoopGroupNode,
  nodeBadge,
} from './helpers';

const POLL_INTERVAL_MS = 3000;

interface TaskNodeData {
  node: TaskNodeView;
  rootPhase: string;
  onOpen: (nodeId: string) => void;
  onDrillDown: (groupId: string, bcsRunId: string) => void;
}

const TaskNodeCard: React.FC<NodeProps<Node<TaskNodeData>>> = ({ data }) => {
  const { node, onOpen, onDrillDown } = data;
  const color = NODE_STATUS_COLOR[node.status] ?? '#9ca3af';
  const coop = isCoopGroupNode(node);
  const badge = nodeBadge(node);
  return (
    <div
      style={{
        padding: '8px 12px',
        borderRadius: 8,
        border: `2px solid ${color}`,
        background: '#fff',
        minWidth: 140,
        cursor: 'pointer',
        boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
      }}
      onClick={() => onOpen(node.node_id)}
      onDoubleClick={() => {
        if (coop && node.sub_dag_ref) {
          onDrillDown(node.sub_dag_ref.group_id, node.sub_dag_ref.bcs_run_id);
        }
      }}
      title={coop ? '双击下钻协作群执行子 DAG' : '单击查看节点详情'}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{ fontWeight: 600, fontSize: 12, color: '#111827' }}>
        {node.display_name || node.node_id}
      </div>
      <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
        <span
          style={{
            background: color,
            color: '#fff',
            fontSize: 10,
            padding: '1px 6px',
            borderRadius: 4,
          }}
        >
          {NODE_STATUS_LABEL[node.status] ?? node.status}
        </span>
        {badge.mode && (
          <span
            style={{
              background: '#eef2ff',
              color: '#4338ca',
              fontSize: 10,
              padding: '1px 6px',
              borderRadius: 4,
            }}
          >
            {badge.mode}
            {badge.sub ? `·${badge.sub}` : ''}
          </span>
        )}
        {coop && (
          <span
            style={{
              background: '#fef3c7',
              color: '#92400e',
              fontSize: 10,
              padding: '1px 6px',
              borderRadius: 4,
            }}
          >
            可下钻
          </span>
        )}
      </div>
      {node.attempt > 0 && (
        <div style={{ fontSize: 10, color: '#6b7280', marginTop: 2 }}>
          第 {node.attempt} 轮 · {node.assignee || '未派发'}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
};

const nodeTypes = { task: TaskNodeCard };

export interface TaskWorkflowViewProps {
  taskId: string;
  /** 协作群下钻回调(跨页导航到该群页);缺省时打开只读预览悬浮卡 */
  onDrillDown?: (groupId: string, bcsRunId: string) => void;
  /** 轮询开关(嵌入式 panel 可关闭) */
  poll?: boolean;
}

export const TaskWorkflowView: React.FC<TaskWorkflowViewProps> = ({
  taskId,
  onDrillDown,
  poll = true,
}) => {
  const [graph, setGraph] = useState<TaskGraphView | null>(null);
  const [detail, setDetail] = useState<TaskNodeDetailView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchGraph = useCallback(async () => {
    try {
      const g = await getTaskGraph(taskId);
      setGraph(g);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? '加载任务图谱失败');
    }
  }, [taskId]);

  useEffect(() => {
    fetchGraph();
    if (!poll) return;
    timerRef.current = setInterval(fetchGraph, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchGraph, poll]);

  const openDetail = useCallback(
    async (nodeId: string) => {
      setLoading(true);
      try {
        const d = await getNodeDetail(taskId, nodeId);
        setDetail(d);
      } catch (e: any) {
        setError(e?.message ?? '加载节点详情失败');
      } finally {
        setLoading(false);
      }
    },
    [taskId],
  );

  const handleDrillDown = useCallback(
    (groupId: string, bcsRunId: string) => {
      if (onDrillDown) {
        onDrillDown(groupId, bcsRunId);
      } else {
        // 兜底:打开只读下钻预览(同画布内悬浮)—— Phase 4.5.5 P1 预览卡
        // 真实生产由跨页导航接管(onDrillDown 注入)。
        window.open(
          `/bcn/task-loop/${taskId}?drill=${bcsRunId}`,
          '_blank',
        );
      }
    },
    [onDrillDown, taskId],
  );

  const rfNodes: Node<NodeTaskData>[] = useMemo(() => {
    if (!graph) return [];
    // 简单拓扑分层布局:按出现顺序 + 前置深度决定 y
    const depth: Record<string, number> = {};
    graph.nodes.forEach((n) => (depth[n.node_id] = 0));
    // BFS over edges
    let changed = true;
    let guard = 0;
    while (changed && guard < graph.nodes.length + 2) {
      changed = false;
      guard++;
      graph.edges.forEach((e) => {
        if (depth[e.to_node] !== undefined && depth[e.from_node] !== undefined) {
          if (depth[e.to_node] < depth[e.from_node] + 1) {
            depth[e.to_node] = depth[e.from_node] + 1;
            changed = true;
          }
        }
      });
    }
    const byDepth: Record<number, TaskNodeView[]> = {};
    graph.nodes.forEach((n) => {
      const d = depth[n.node_id] ?? 0;
      (byDepth[d] ??= []).push(n);
    });
    const nodes: Node<NodeTaskData>[] = [];
    Object.entries(byDepth).forEach(([d, list]) => {
      list.forEach((n, i) => {
        nodes.push({
          id: n.node_id,
          type: 'task',
          position: {
            x: 200 + i * 220 - ((list.length - 1) * 220) / 2,
            y: 40 + Number(d) * 120,
          },
          data: {
            node: n,
            rootPhase: graph.root_phase,
            onOpen: openDetail,
            onDrillDown: handleDrillDown,
          },
        });
      });
    });
    return nodes;
  }, [graph, openDetail, handleDrillDown]);

  const rfEdges: Edge[] = useMemo(() => {
    if (!graph) return [];
    return graph.edges.map((e) => ({
      id: e.edge_id,
      source: e.from_node,
      target: e.to_node,
      label: e.outcome ?? undefined,
      animated: e.kind === 'conditional',
      style: { stroke: e.kind === 'fallback' ? '#dc2626' : '#94a3b8' },
    }));
  }, [graph]);

  const rootColor = graph
    ? ROOT_PHASE_COLOR[graph.root_phase] ?? '#6b7280'
    : '#6b7280';

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 360 }}>
      <div style={{ flex: 1, position: 'relative' }}>
        {/* 顶部展示元 (根相位 / loop_round / graph_status) */}
        <div
          style={{
            position: 'absolute',
            top: 8,
            left: 8,
            zIndex: 5,
            background: 'rgba(255,255,255,0.92)',
            border: `1px solid ${rootColor}`,
            borderRadius: 6,
            padding: '4px 8px',
            fontSize: 12,
          }}
        >
          <span style={{ color: rootColor, fontWeight: 600 }}>
            {graph?.root_phase ?? '加载中'}
          </span>
          {graph && (
            <>
              <span style={{ color: '#6b7280', marginLeft: 8 }}>
                状态: {graph.graph_status}
              </span>
              <span style={{ color: '#6b7280', marginLeft: 8 }}>
                第 {graph.loop_round} 轮
              </span>
            </>
          )}
        </div>
        {error && (
          <div
            style={{
              position: 'absolute',
              top: 8,
              right: 8,
              zIndex: 5,
              background: '#fee2e2',
              color: '#991b1b',
              padding: '2px 8px',
              borderRadius: 6,
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
          nodesDraggable
        >
          <Background variant={BackgroundVariant.Dots} gap={16} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {/* 节点详情侧栏 (FR-OBS-04) */}
      {(detail || loading) && (
        <div
          style={{
            width: 320,
            borderLeft: '1px solid #e5e7eb',
            padding: 12,
            overflowY: 'auto',
            background: '#fafafa',
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              marginBottom: 8,
            }}
          >
            <strong>节点详情</strong>
            <span
              style={{ cursor: 'pointer', color: '#2563eb' }}
              onClick={() => setDetail(null)}
            >
              关闭
            </span>
          </div>
          {loading && <div style={{ color: '#6b7280' }}>加载中…</div>}
          {detail && <NodeDetailPanel detail={detail} />}
        </div>
      )}
    </div>
  );
};

const NodeDetailPanel: React.FC<{ detail: TaskNodeDetailView }> = ({
  detail,
}) => {
  return (
    <div style={{ fontSize: 12, color: '#374151' }}>
      <Row label="节点ID" value={detail.node_id} />
      <Row label="名称" value={detail.display_name} />
      <Row label="状态" value={NODE_STATUS_LABEL[detail.status] ?? detail.status} />
      <Row label="子状态" value={detail.sub_status ?? '-'} />
      <Row label="轮次" value={String(detail.attempt)} />
      <Row label="执行方" value={detail.assignee || '-'} />
      <Row label="模态" value={nodeBadge(detail).mode || '-'} />
      <Row label="验收" value={detail.acceptance_result ?? '-'} />
      <Row label="终产" value={detail.is_final_output ? '是' : '否'} />
      {detail.instruction && <Row label="指令" value={detail.instruction} />}
      {detail.properties?.error_msg && (
        <Row label="错误" value={String(detail.properties.error_msg)} />
      )}
      {detail.artifacts && detail.artifacts.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>产出</div>
          {detail.artifacts.map((a, i) => (
            <div
              key={i}
              style={{ background: '#fff', padding: 6, borderRadius: 4, marginBottom: 4 }}
            >
              <div style={{ fontWeight: 600 }}>{a.name}</div>
              {a.text && (
                <pre style={{ whiteSpace: 'pre-wrap', margin: '4px 0 0' }}>
                  {a.text}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
      {detail.attempted_executors && detail.attempted_executors.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>执行历史</div>
          {detail.attempted_executors.map((a, i) => (
            <div key={i} style={{ marginBottom: 2 }}>
              #{a.round} {a.executor_id} ({a.outcome ?? '-'})
            </div>
          ))}
        </div>
      )}
      {isCoopGroupNode(detail) && detail.sub_dag_ref && (
        <div style={{ marginTop: 8, color: '#92400e' }}>
          协作群 {detail.sub_dag_ref.group_id} · 双击节点下钻
        </div>
      )}
    </div>
  );
};

const Row: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={{ display: 'flex', marginBottom: 4 }}>
    <span style={{ width: 56, color: '#6b7280' }}>{label}</span>
    <span style={{ flex: 1, wordBreak: 'break-all' }}>{value}</span>
  </div>
);

type NodeTaskData = TaskNodeData;

export default TaskWorkflowView;