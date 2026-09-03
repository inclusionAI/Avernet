import { Empty } from '@/components/ui';
import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';
import { cn } from '@/utils/cn';
import { Background, Controls, Handle, Position, ReactFlow } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import React, { useMemo } from 'react';
import {
  buildCollaborationGraphLayout,
  COLLABORATION_FLOW_NODE_WIDTH,
  COLLABORATION_NODE_KIND_LABELS,
  getCollaborationNodeTone,
  type CollaborationBindingView,
  type CollaborationGraphNodeData,
  type CollaborationNodeTone,
} from './collaborationGraphLayout';

interface CollaborationFlowPreviewProps {
  graph: CollaborationDefinitionGraphPreview;
  initialNodes: string[];
  bindingViews: Record<string, CollaborationBindingView>;
  highlightedBinding?: string;
}

const ARROW_CLOSED = 'arrowclosed' as const;

const NODE_THEME: Record<
  CollaborationNodeTone,
  { default: string; highlighted: string; badge: string; bot: string; role: string; unbound: string }
> = {
  blue: {
    default: 'border border-primary/30 bg-primary/5 hover:border-primary/60',
    highlighted: 'border border-primary bg-primary/10 ring-2 ring-primary/20',
    badge: 'border-primary/40 bg-primary text-primary-foreground',
    bot: 'text-primary',
    role: 'border-primary/30 bg-background/90 text-primary',
    unbound: 'text-warning',
  },
  green: {
    default: 'border border-success/30 bg-success/5 hover:border-success/60',
    highlighted: 'border border-success bg-success/10 ring-2 ring-success/20',
    badge: 'border-success/40 bg-success text-primary-foreground',
    bot: 'text-success',
    role: 'border-success/30 bg-background/90 text-success',
    unbound: 'text-warning',
  },
  neutral: {
    default: 'border border-border bg-muted hover:border-muted-foreground',
    highlighted: 'border border-muted-foreground bg-muted ring-2 ring-border',
    badge: 'border-border bg-muted text-foreground',
    bot: 'text-foreground',
    role: 'border-border bg-background/90 text-foreground',
    unbound: 'text-warning',
  },
};

interface FlowNodeData extends CollaborationGraphNodeData {
  highlighted: boolean;
}

function CollaborationNode({ data }: { data: FlowNodeData }) {
  const tone = getCollaborationNodeTone(data.definition.kind);
  const theme = NODE_THEME[tone];
  const kindLabel = COLLABORATION_NODE_KIND_LABELS[data.definition.kind] ?? '节点';
  const isUnbound = !!data.assigneeBinding && !data.assigneeBotId;
  const botName =
    data.assigneeBotName || (data.assigneeBotId ? '已绑定 Bot' : data.assigneeBinding ? '未绑定 Bot' : '无固定执行者');
  const roleName = data.assigneeLabel;

  return (
    <div
      className={cn(
        'nodrag nopan relative flex min-h-[84px] w-[210px] cursor-default flex-col justify-center rounded-xl px-4 py-3 shadow-sm transition-all',
        data.highlighted ? theme.highlighted : theme.default,
      )}
    >
      <span
        className={cn(
          'absolute -right-1.5 -top-2 rounded-full border px-2 py-0.5 text-[10px] font-semibold shadow-sm',
          theme.badge,
        )}
      >
        {kindLabel}
      </span>
      <Handle type="target" position={Position.Top} className="pointer-events-none opacity-0" />
      <div className="truncate text-center text-sm font-semibold text-foreground">{data.title}</div>
      <div className="mt-3 flex min-w-0 items-center justify-between gap-3">
        <span className={cn('min-w-0 flex-1 truncate text-xs font-medium', isUnbound ? theme.unbound : theme.bot)}>
          {botName}
        </span>
        <span
          className={cn(
            'max-w-[46%] flex-shrink-0 truncate rounded-full border px-2 py-0.5 text-[11px] font-semibold',
            theme.role,
          )}
        >
          {roleName}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} className="pointer-events-none opacity-0" />
    </div>
  );
}

const nodeTypes = { collaboration: CollaborationNode };
const FIT_VIEW_OPTIONS = { padding: 0.2, maxZoom: 1 };

/** 协作流程图预览（ReactFlow），显示在校验通过后的自定义协作创建弹窗中。 */
export function CollaborationFlowPreview({
  graph,
  initialNodes,
  bindingViews,
  highlightedBinding,
}: CollaborationFlowPreviewProps) {
  const result = useMemo(() => {
    try {
      return { layout: buildCollaborationGraphLayout(graph, initialNodes, bindingViews), error: null as string | null };
    } catch (error) {
      return { layout: null, error: error instanceof Error ? error.message : '协作流程数据无效' };
    }
  }, [graph, initialNodes, bindingViews]);

  if (!result.layout) {
    return <Empty compact title="协作流程数据无效" description={result.error ?? undefined} className="flex-1" />;
  }

  const nodes: Record<string, unknown>[] = result.layout.nodes.map((node) => ({
    ...node,
    type: 'collaboration',
    draggable: false,
    selectable: false,
    style: { width: COLLABORATION_FLOW_NODE_WIDTH },
    data: {
      ...node.data,
      highlighted: !highlightedBinding ? false : node.data.assigneeBinding === highlightedBinding,
    },
  }));

  const edges: Record<string, unknown>[] = result.layout.edges.map((edge) => ({
    ...edge,
    type: 'default',
    markerEnd: { type: ARROW_CLOSED, color: 'hsl(var(--primary))' },
    style: { stroke: 'hsl(var(--primary))', strokeWidth: 2 },
    labelStyle: { fill: 'hsl(var(--primary))', fontSize: 11, fontWeight: 600 },
    labelBgStyle: { fill: 'hsl(var(--background))', fillOpacity: 0.92 },
    labelBgPadding: [5, 3] as [number, number],
    labelBgBorderRadius: 4,
  }));

  return (
    <div role="region" aria-label="协作流程预览" className="h-[400px] w-full overflow-hidden rounded-xl bg-muted">
      <ReactFlow
        nodes={nodes as never}
        edges={edges as never}
        nodeTypes={nodeTypes as never}
        fitView
        fitViewOptions={FIT_VIEW_OPTIONS}
        minZoom={0.2}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        nodesFocusable={false}
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        zoomOnDoubleClick={false}
        preventScrolling
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#dbeafe" gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

export default React.memo(CollaborationFlowPreview);
