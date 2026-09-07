/**
 * WorkflowDagView — 基于 React Flow 的只读 DAG 流程图视图。
 *
 * 从 WorkflowSpec.nodes + dependsOn 构建节点和边，
 * 自动分层布局，按执行器类型着色。
 */
import { Background, Controls, Handle, Position, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useMemo } from 'react';

import type { TaskEscortWorkflowNode, TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';
import { Card } from '@/components/ui/Card';
import { cn } from '@/utils/cn';

/** 安全提取字符串 */
function toText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return fallback;
}

/** 安全提取字符串数组 */
function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === 'string');
}

/** 安全提取数组 */
function toArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value : [];
}

/** 执行器类型颜色映射（与 open-claw 一致） */
const EXECUTOR_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  'embedded-agent': { bg: '#f0f0ff', border: '#7c6cdb', text: '#5046a5' },
  action: { bg: '#faf5ff', border: '#a855f7', text: '#7e22ce' },
  human: { bg: '#fffbeb', border: '#f59e0b', text: '#b45309' },
  'loop-group': { bg: '#f0fdfa', border: '#14b8a6', text: '#0f766e' },
  collaboration: { bg: '#fff1f2', border: '#f43f5e', text: '#be123c' },
  done: { bg: '#f9fafb', border: '#9ca3af', text: '#4b5563' },
  subagent: { bg: '#eef2ff', border: '#6366f1', text: '#4338ca' },
  'bcs-route': { bg: '#ecfeff', border: '#06b6d4', text: '#0e7490' },
  'baas-call': { bg: '#eff6ff', border: '#3b82f6', text: '#1d4ed8' },
  'mcp-call': { bg: '#f5f3ff', border: '#8b5cf6', text: '#6d28d9' },
  'cli-script': { bg: '#f7fee7', border: '#84cc16', text: '#4d7c0f' },
  subworkflow: { bg: '#fff7ed', border: '#f97316', text: '#c2410c' },
  approval: { bg: '#fdf2f8', border: '#ec4899', text: '#be185d' },
};

const DEFAULT_COLOR = { bg: '#f9fafb', border: '#9ca3af', text: '#4b5563' };

interface DagNodeData {
  label: string;
  executorType: string;
  executorDetail?: string;
  node: TaskEscortWorkflowNode;
}

type DagNode = Node<DagNodeData>;

interface DagNodeComponentProps {
  id: string;
  data: DagNodeData;
  selected?: boolean;
}

function DagNodeComponent({ id, data, selected }: DagNodeComponentProps) {
  const colors = EXECUTOR_COLORS[data.executorType] ?? DEFAULT_COLOR;
  return (
    <Card
      data-node-id={id}
      className={cn(
        'min-w-[120px] max-w-[180px] rounded-lg border-2 px-2.5 py-1.5 shadow-sm cursor-pointer transition-shadow',
        selected ? 'ring-2 ring-primary ring-offset-2' : 'hover:shadow-md',
      )}
      style={{ borderColor: colors.border, backgroundColor: colors.bg }}
    >
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !bg-muted-foreground !border-white" />
      <div className="text-center">
        <div className="text-[11px] font-medium leading-tight" style={{ color: colors.text }}>
          {data.label}
        </div>
        <div className="mt-0.5 text-[10px]" style={{ color: colors.text, opacity: 0.7 }}>
          {data.executorType}
        </div>
        {data.executorDetail && (
          <div className="mt-0.5 truncate font-mono text-[8px] text-muted-foreground">{data.executorDetail}</div>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !bg-muted-foreground !border-white" />
    </Card>
  );
}

const nodeTypes = { dagNode: DagNodeComponent };

interface WorkflowDagViewProps {
  spec: TaskEscortWorkflowSpec;
  selectedNodeId?: string | null;
  onNodeClick?: (nodeId: string) => void;
}

export default function WorkflowDagView({ spec, selectedNodeId, onNodeClick }: WorkflowDagViewProps) {
  const { flowNodes, edges } = useMemo(() => {
    const specNodes = toArray<Record<string, unknown>>(spec.nodes);

    // 构建边：dependsOn → 当前节点
    const flowEdges: Edge[] = [];
    for (const node of specNodes) {
      const nodeId = toText(node.id);
      const deps = toStringArray(node.dependsOn);
      const branchId = toText(node.branchId);
      for (const dep of deps) {
        const isBranchEdge = !!branchId;
        flowEdges.push({
          id: `${dep}->${nodeId}`,
          source: dep,
          target: nodeId,
          animated: isBranchEdge,
          label: isBranchEdge ? branchId : undefined,
          style: isBranchEdge
            ? { stroke: '#8b5cf6', strokeWidth: 1.5, strokeDasharray: '4 2' }
            : { stroke: '#94a3b8', strokeWidth: 1.5 },
        });
      }
    }

    // 自动分层布局：基于 dependsOn 计算层级
    const levels = new Map<string, number>();
    function resolveLevel(id: string, visited = new Set<string>()): number {
      if (levels.has(id)) return levels.get(id)!;
      if (visited.has(id)) return 0;
      visited.add(id);
      const node = specNodes.find((n) => toText(n.id) === id);
      const deps = node ? toStringArray(node.dependsOn) : [];
      if (deps.length === 0) {
        levels.set(id, 0);
        return 0;
      }
      const maxDepLevel = Math.max(...deps.map((d) => resolveLevel(d, visited)));
      const level = maxDepLevel + 1;
      levels.set(id, level);
      return level;
    }
    for (const n of specNodes) {
      resolveLevel(toText(n.id));
    }

    // 按层级分组
    const levelGroups = new Map<number, string[]>();
    for (const n of specNodes) {
      const id = toText(n.id);
      const level = levels.get(id) ?? 0;
      const group = levelGroups.get(level) ?? [];
      group.push(id);
      levelGroups.set(level, group);
    }

    // 构建节点
    const nodes: DagNode[] = specNodes.map((node) => {
      const id = toText(node.id);
      const level = levels.get(id) ?? 0;
      const siblings = levelGroups.get(level) ?? [id];
      const indexInLevel = siblings.indexOf(id);
      const levelCount = siblings.length;

      // 提取执行器类型
      const executor = node.executor;
      let executorType = 'unknown';
      let executorDetail: string | undefined;
      if (typeof executor === 'string') {
        executorType = executor;
      } else if (executor && typeof executor === 'object') {
        executorType = toText((executor as Record<string, unknown>).type, 'unknown');
        executorDetail = toText((executor as Record<string, unknown>).command) || undefined;
      }
      // 也检查 node.type 字段
      if (executorType === 'unknown') {
        executorType = toText(node.type, 'unknown');
      }

      return {
        id,
        type: 'dagNode',
        position: {
          x: indexInLevel * 220 + (3 - levelCount) * 110,
          y: level * 140,
        },
        data: {
          label: toText(node.title) || id,
          executorType,
          executorDetail,
          node: node as TaskEscortWorkflowNode,
        },
      };
    });

    return { flowNodes: nodes, edges: flowEdges };
  }, [spec]);

  if (flowNodes.length === 0) {
    return (
      <Card className="flex h-64 items-center justify-center border-dashed bg-muted/20 text-sm text-muted-foreground">
        暂无节点数据可用于 DAG 可视化
      </Card>
    );
  }

  return (
    <Card className="h-96 overflow-hidden">
      <ReactFlow
        nodes={flowNodes.map((n) => ({ ...n, selected: n.id === selectedNodeId }))}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={
          onNodeClick
            ? (_event: React.MouseEvent, node: Node) => {
                onNodeClick(node.id);
              }
            : undefined
        }
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} color="#e2e8f0" />
        <Controls />
      </ReactFlow>
    </Card>
  );
}
