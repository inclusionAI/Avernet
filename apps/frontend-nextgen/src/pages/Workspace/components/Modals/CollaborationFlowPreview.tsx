import { Empty } from '@/components/ui';
import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';
import { cn } from '@/utils/cn';
import { Background, Controls, Handle, Position, ReactFlow, type ReactFlowInstance } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import React, { useEffect, useMemo, useRef } from 'react';
import { buildCollaborationLoopLayout, hasLogicalLoopDescriptors } from './collaborationLoopLayout';
import CollaborationLoopContainer from './CollaborationLoopContainer';
import CollaborationLoopEdge from './CollaborationLoopEdge';
import CollaborationTransitionEdge from './CollaborationTransitionEdge';
import CollaborationPreviewMetadata from './CollaborationPreviewMetadata';
import {
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
  const execution = data.definition.execution;
  const ariaLabel = [data.title, `预览节点 ${data.definition.node_id}`, `角色 ${roleName}`, `Bot ${botName}`,
    ...(execution ? [`Loop ${execution.loop_id}`, `逻辑节点 ${execution.definition_node_id}`,
      ...(execution.loop_path ? [`路径 ${execution.loop_path.map((frame) =>
        `${frame.loop_id} ${frame.iteration}/${frame.max_iterations}`).join(' → ')}`] : [])] : []),
    data.isInitial ? '入口节点' : '',
    data.definition.final_output ? '最终输出节点' : '',
    data.definition.judge ? 'Judge 节点' : '',
  ].filter(Boolean).join('，');

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      data-loop-id={execution?.loop_id}
      data-loop-iteration={execution?.iteration}
      data-loop-path={execution?.loop_path?.map((frame) => frame.loop_id).join('/')}
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
      <Handle id="main" type="target" position={Position.Top} className="pointer-events-none opacity-0" />
      <Handle id="loop-return" type="source" position={Position.Right} style={{ top: '65%' }} className="pointer-events-none opacity-0" />
      <Handle id="loop-return" type="target" position={Position.Right} style={{ top: '35%' }} className="pointer-events-none opacity-0" />
      <div className="truncate text-center text-sm font-semibold text-foreground">{data.title}</div>
      <div className="mt-3 flex min-w-0 items-center justify-between gap-3">
        <span className={cn('min-w-0 flex-1 truncate text-xs font-medium', isUnbound ? theme.unbound : theme.bot)}>
          {botName}
        </span>
        <span
          className={cn(
            'max-w-[46%] flex-shrink-0 truncate rounded-full border px-2 py-0.5 text-xs font-semibold',
            theme.role,
          )}
        >
          {roleName}
        </span>
      </div>
      <Handle id="main" type="source" position={Position.Bottom} className="pointer-events-none opacity-0" />
    </div>
  );
}

const nodeTypes = { collaboration: CollaborationNode, loopContainer: CollaborationLoopContainer };
const edgeTypes = { loop: CollaborationLoopEdge, transition: CollaborationTransitionEdge };
const FIT_VIEW_OPTIONS = { padding: 0.2, maxZoom: 1 };
const LOOP_FIT_VIEW_OPTIONS = { padding: 0.05, maxZoom: 1 };

/** 协作流程图预览（ReactFlow），显示在校验通过后的自定义协作创建弹窗中。 */
export function CollaborationFlowPreview({
  graph,
  initialNodes,
  bindingViews,
  highlightedBinding,
}: CollaborationFlowPreviewProps) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const flowInstanceRef = useRef<ReactFlowInstance | null>(null);
  const hasLoops = hasLogicalLoopDescriptors(graph);
  const fitViewOptions = hasLoops ? LOOP_FIT_VIEW_OPTIONS : FIT_VIEW_OPTIONS;
  const result = useMemo(() => {
    try {
      return { layout: buildCollaborationLoopLayout(graph, initialNodes, bindingViews), error: null as string | null };
    } catch (error) {
      return { layout: null, error: error instanceof Error ? error.message : '协作流程数据无效' };
    }
  }, [graph, initialNodes, bindingViews]);

  // 绑定高亮只更新节点数据，不重置用户缩放；循环结构或容器尺寸变化才重新适配视图。
  const viewportLayoutKey = result.layout && JSON.stringify({
    nodes: result.layout.nodes.map(({ id, parentId, position }) => [id, parentId, position.x, position.y]),
    groups: result.layout.groups.map(({ id, parentId, position, width, height }) => [id, parentId, position.x, position.y, width, height]),
    edges: result.layout.edges.map(({ source, target }) => [source, target]),
  });
  useEffect(() => {
    const frame = requestAnimationFrame(() => { void flowInstanceRef.current?.fitView(fitViewOptions); });
    return () => cancelAnimationFrame(frame);
  }, [viewportLayoutKey, fitViewOptions]);
  useEffect(() => {
    if (!canvasRef.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => { void flowInstanceRef.current?.fitView(fitViewOptions); });
    observer.observe(canvasRef.current);
    return () => observer.disconnect();
  }, [viewportLayoutKey, fitViewOptions]);

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
    sourceHandle: edge.sourceHandle || 'main',
    targetHandle: edge.targetHandle || 'main',
    type: edge.data?.loopRoute ? 'loop' : 'transition',
    data: edge.data ?? { outcome: edge.outcome },
    markerEnd: { type: ARROW_CLOSED, color: 'hsl(var(--primary))' },
    style: { stroke: 'hsl(var(--primary))', strokeWidth: 2 },
    labelStyle: { fill: 'hsl(var(--primary))', fontSize: 11, fontWeight: 600 },
    labelBgStyle: { fill: 'hsl(var(--background))', fillOpacity: 0.92 },
    labelBgPadding: [5, 3] as [number, number],
    labelBgBorderRadius: 4,
  }));

  return (
    <div className="flex w-full flex-col">
      <CollaborationPreviewMetadata graph={graph} />
      <div ref={canvasRef} role="region" aria-label="协作流程预览" className="h-[400px] w-full overflow-hidden rounded-xl bg-muted">
        <ReactFlow
          nodes={[...result.layout.groups.map((group) => ({
            id: group.id, type: 'loopContainer', position: group.position, parentId: group.parentId,
            data: { title: group.title, maxIterations: group.maxIterations, loopId: group.loopId },
            style: { width: group.width, height: group.height }, draggable: false, selectable: false, focusable: false,
          })), ...nodes] as never}
          edges={edges as never}
          nodeTypes={nodeTypes as never}
          edgeTypes={edgeTypes as never}
          fitView
          fitViewOptions={fitViewOptions}
          onInit={(instance) => { flowInstanceRef.current = instance; }}
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
          <Background color="hsl(var(--primary) / 0.05)" gap={20} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}

export default React.memo(CollaborationFlowPreview);
