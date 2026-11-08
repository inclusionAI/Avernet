import { Button, Empty } from '@/components/ui';
import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';
import { cn } from '@/utils/cn';
import { Background, Controls, Handle, Position, ReactFlow, type CoordinateExtent, type ReactFlowInstance } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { buildCollaborationLoopLayout, hasLogicalLoopDescriptors } from './collaborationLoopLayout';
import CollaborationLoopContainer from './CollaborationLoopContainer';
import CollaborationLoopEdge from './CollaborationLoopEdge';
import CollaborationTransitionEdge from './CollaborationTransitionEdge';
import CollaborationPreviewMetadata from './CollaborationPreviewMetadata';
import { collapseCollaborationLoops } from './collapseCollaborationLoops';
import { CollaborationNodeDetails } from './CollaborationNodeDetails';
import {
  COLLABORATION_FLOW_NODE_WIDTH,
  COLLABORATION_FLOW_NODE_HEIGHT,
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
  onSelectBinding?: (binding: string) => void;
  onLocateBinding?: (binding: string) => void;
}

const ARROW_CLOSED = 'arrowclosed' as const;

const NODE_THEME: Record<
  CollaborationNodeTone,
  { default: string; highlighted: string }
> = {
  blue: {
    default: 'border border-primary/30 bg-primary/5 hover:border-primary/60',
    highlighted: 'border border-primary bg-primary/10 ring-2 ring-primary/20',
  },
  green: {
    default: 'border border-success/30 bg-success/5 hover:border-success/60',
    highlighted: 'border border-success bg-success/10 ring-2 ring-success/20',
  },
  neutral: {
    default: 'border border-border bg-muted hover:border-muted-foreground',
    highlighted: 'border border-muted-foreground bg-muted ring-2 ring-border',
  },
};

interface FlowNodeData extends CollaborationGraphNodeData {
  highlighted: boolean;
  selected: boolean;
  onSelect: () => void;
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
        'nodrag nopan relative h-[104px] w-[210px] rounded-xl transition-colors',
        data.highlighted ? theme.highlighted : theme.default,
      )}
    >
      <Handle id="main" type="target" position={Position.Top} className="pointer-events-none opacity-0" />
      <Handle id="loop-return" type="source" position={Position.Right} style={{ top: '65%' }} className="pointer-events-none opacity-0" />
      <Handle id="loop-return" type="target" position={Position.Right} style={{ top: '35%' }} className="pointer-events-none opacity-0" />
      <Button variant="ghost" aria-label={`查看节点 ${data.title}`} aria-pressed={data.selected} onClick={data.onSelect}
        className="h-full w-full min-w-0 flex-col items-stretch gap-1 rounded-xl border-0 px-3 py-2 text-left hover:bg-accent/40">
        <span className="text-xs font-normal text-muted-foreground">{kindLabel}{data.definition.final_output ? ' · 最终输出' : data.isInitial ? ' · 入口' : ''}</span>
        <span className="truncate text-base font-semibold text-foreground">{data.title}</span>
        <span className={cn('truncate text-sm font-normal', isUnbound ? 'text-warning' : 'text-muted-foreground')}>
          {roleName}{data.assigneeBinding ? ` · ${botName}` : ''}
        </span>
      </Button>
      <Handle id="main" type="source" position={Position.Bottom} className="pointer-events-none opacity-0" />
    </div>
  );
}

const nodeTypes = { collaboration: CollaborationNode, loopContainer: CollaborationLoopContainer };
const edgeTypes = { loop: CollaborationLoopEdge, transition: CollaborationTransitionEdge };
const FIT_VIEW_OPTIONS = { padding: 0.12, minZoom: 0.85, maxZoom: 1 };
const LOOP_FIT_VIEW_OPTIONS = { padding: 0.05, minZoom: 0.85, maxZoom: 1 };
const VIEWPORT_PADDING = 64;

/** 协作流程图预览（ReactFlow），显示在校验通过后的自定义协作创建弹窗中。 */
export function CollaborationFlowPreview({
  graph,
  initialNodes,
  bindingViews,
  highlightedBinding,
  onSelectBinding,
  onLocateBinding,
}: CollaborationFlowPreviewProps) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const flowInstanceRef = useRef<ReactFlowInstance | null>(null);
  const userViewportRef = useRef(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsedLoops, setCollapsedLoops] = useState<Set<string>>(() => new Set());
  const hasLoops = hasLogicalLoopDescriptors(graph);
  const fitViewOptions = hasLoops ? LOOP_FIT_VIEW_OPTIONS : FIT_VIEW_OPTIONS;
  const result = useMemo(() => {
    try {
      const fullLayout = buildCollaborationLoopLayout(graph, initialNodes, bindingViews);
      const layout = collapseCollaborationLoops(fullLayout, collapsedLoops);
      // 顶层循环容器已包含嵌套节点与返回线；子节点的位置是相对坐标，不参与全局边界计算。
      const boxes = [
        ...layout.groups.filter((group) => !group.parentId),
        ...layout.nodes.filter((node) => !node.parentId).map((node) => ({
          position: node.position, width: COLLABORATION_FLOW_NODE_WIDTH, height: COLLABORATION_FLOW_NODE_HEIGHT,
        })),
      ];
      const translateExtent: CoordinateExtent = [
        [Math.min(...boxes.map(({ position }) => position.x)) - VIEWPORT_PADDING,
          Math.min(...boxes.map(({ position }) => position.y)) - VIEWPORT_PADDING],
        [Math.max(...boxes.map(({ position, width }) => position.x + width)) + VIEWPORT_PADDING,
          Math.max(...boxes.map(({ position, height }) => position.y + height)) + VIEWPORT_PADDING],
      ];
      return { fullLayout, layout, translateExtent, error: null as string | null };
    } catch (error) {
      return { layout: null, error: error instanceof Error ? error.message : '协作流程数据无效' };
    }
  }, [graph, initialNodes, bindingViews, collapsedLoops]);

  // 绑定高亮只更新节点数据，不重置用户缩放；循环结构或容器尺寸变化才重新适配视图。
  const viewportLayoutKey = result.layout && JSON.stringify({
    nodes: result.layout.nodes.map(({ id, parentId, position }) => [id, parentId, position.x, position.y]),
    groups: result.layout.groups.map(({ id, parentId, position, width, height }) => [id, parentId, position.x, position.y, width, height]),
    edges: result.layout.edges.map(({ source, target }) => [source, target]),
  });
  useEffect(() => {
    userViewportRef.current = false;
    const frame = requestAnimationFrame(() => { void flowInstanceRef.current?.fitView(fitViewOptions); });
    return () => cancelAnimationFrame(frame);
  }, [viewportLayoutKey, fitViewOptions]);
  useEffect(() => {
    if (!canvasRef.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (!userViewportRef.current) void flowInstanceRef.current?.fitView(fitViewOptions);
    });
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
    style: { width: COLLABORATION_FLOW_NODE_WIDTH, pointerEvents: 'all' },
    data: {
      ...node.data,
      highlighted: !highlightedBinding ? false : node.data.assigneeBinding === highlightedBinding,
      selected: node.id === selectedId,
      onSelect: () => {
        userViewportRef.current = true;
        setSelectedId(node.id);
        if (node.data.assigneeBinding) onSelectBinding?.(node.data.assigneeBinding);
      },
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
    labelStyle: { fill: 'hsl(var(--foreground))', fontSize: 14, fontWeight: 500 },
    labelBgStyle: { fill: 'hsl(var(--background))', fillOpacity: 0.92 },
    labelBgPadding: [5, 3] as [number, number],
    labelBgBorderRadius: 4,
  }));

  const selectedNode = result.layout.nodes.find((node) => node.id === selectedId);
  const toggleLoop = (id: string) => setCollapsedLoops((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <CollaborationPreviewMetadata graph={graph} />
      <div ref={canvasRef} role="region" aria-label="协作流程预览" className="relative min-h-48 w-full flex-1 overflow-hidden bg-muted/30">
        <ReactFlow
          nodes={[...result.layout.groups.map((group) => ({
            id: group.id, type: 'loopContainer', position: group.position, parentId: group.parentId,
            data: { title: group.title, maxIterations: group.maxIterations, loopId: group.loopId,
              collapsed: collapsedLoops.has(group.id), onToggle: () => toggleLoop(group.id) },
            style: { width: group.width, height: group.height, pointerEvents: 'all' }, draggable: false, selectable: false, focusable: false,
          })), ...nodes] as never}
          edges={edges as never}
          nodeTypes={nodeTypes as never}
          edgeTypes={edgeTypes as never}
          fitView
          fitViewOptions={fitViewOptions}
          onInit={(instance) => { flowInstanceRef.current = instance; }}
          onMoveStart={(event) => { if (event) userViewportRef.current = true; }}
          minZoom={0.2}
          maxZoom={2}
          translateExtent={result.translateExtent}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          nodesFocusable={false}
          panOnDrag
          panOnScroll
          panOnScrollSpeed={1}
          zoomOnScroll={false}
          zoomOnPinch
          zoomOnDoubleClick={false}
          preventScrolling
          proOptions={{ hideAttribution: true }}
          ariaLabelConfig={{ 'controls.ariaLabel': '缩放控件', 'controls.zoomIn.ariaLabel': '放大', 'controls.zoomOut.ariaLabel': '缩小' }}
        >
          <Background color="hsl(var(--primary) / 0.05)" gap={20} size={1} />
          <Controls showInteractive={false} showFitView={false} />
        </ReactFlow>
        <div className="absolute bottom-3 right-3 flex gap-1 rounded-lg border border-border bg-background p-1 shadow-sm">
          <Button variant="ghost" size="sm" aria-label="原始大小" onClick={() => {
            userViewportRef.current = true; void flowInstanceRef.current?.zoomTo(1);
          }}>100%</Button>
          <Button variant="ghost" size="sm" onClick={() => {
            userViewportRef.current = true; void flowInstanceRef.current?.fitView({ padding: 0.1, minZoom: 0.2, maxZoom: 1 });
          }}>适应全图</Button>
        </div>
      </div>
      {selectedNode && <CollaborationNodeDetails node={selectedNode} layout={result.fullLayout!}
        onClose={() => setSelectedId(null)} onLocateBinding={onLocateBinding} />}
    </div>
  );
}

export default React.memo(CollaborationFlowPreview);
