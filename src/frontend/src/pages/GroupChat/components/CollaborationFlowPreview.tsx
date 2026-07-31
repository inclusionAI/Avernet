import { Empty } from '@/components';
import type { CollaborationDefinitionGraphPreview } from '@/services/backend-api/BcnController';
import { cn } from '@/utils/utils';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import React, { useEffect, useMemo, useRef } from 'react';
import {
  buildCollaborationGraphLayout,
  buildCollaborationNodePresentation,
  COLLABORATION_FLOW_NODE_WIDTH,
  CollaborationGraphLayoutError,
  getCollaborationNodeInteractionState,
  getCollaborationNodeTone,
  type CollaborationBindingView,
  type CollaborationGraphNodeData,
  type CollaborationNodeTone,
} from '../utils/collaborationGraphLayout';

interface CollaborationFlowPreviewProps {
  graph: CollaborationDefinitionGraphPreview;
  initialNodes: string[];
  bindingViews: Record<string, CollaborationBindingView>;
  selectedNodeId?: string;
  highlightedBinding?: string;
  onNodeSelect?: (nodeId: string) => void;
}

interface CollaborationFlowNodeData extends CollaborationGraphNodeData {
  selected: boolean;
  highlighted: boolean;
  onSelect?: (nodeId: string) => void;
}

type CollaborationFlowNode = Node<CollaborationFlowNodeData, 'collaboration'>;

const NODE_THEME_CLASSES: Record<
  CollaborationNodeTone,
  {
    default: string;
    highlighted: string;
    selected: string;
    badge: string;
    bot: string;
    role: string;
  }
> = {
  blue: {
    default:
      'border border-blue-300 bg-blue-50/60 hover:border-blue-400 hover:bg-blue-50',
    highlighted:
      'border border-blue-400 bg-blue-50 ring-2 ring-blue-100/80',
    selected:
      'border-2 border-blue-500 bg-blue-100/80 ring-2 ring-blue-200/60 shadow-md',
    badge: 'border-blue-200 bg-blue-500 text-white',
    bot: 'text-blue-700',
    role: 'border-blue-200 bg-white/90 text-blue-700',
  },
  green: {
    default:
      'border border-emerald-300 bg-emerald-50/60 hover:border-emerald-400 hover:bg-emerald-50',
    highlighted:
      'border border-emerald-400 bg-emerald-50 ring-2 ring-emerald-100/80',
    selected:
      'border-2 border-emerald-500 bg-emerald-100/80 ring-2 ring-emerald-200/60 shadow-md',
    badge: 'border-emerald-200 bg-emerald-500 text-white',
    bot: 'text-emerald-700',
    role: 'border-emerald-200 bg-white/90 text-emerald-700',
  },
  neutral: {
    default:
      'border border-slate-300 bg-slate-50/80 hover:border-slate-400 hover:bg-slate-100/70',
    highlighted:
      'border border-slate-400 bg-slate-100/70 ring-2 ring-slate-200/70',
    selected:
      'border-2 border-slate-500 bg-slate-100 ring-2 ring-slate-300/60 shadow-md',
    badge: 'border-slate-200 bg-slate-500 text-white',
    bot: 'text-slate-600',
    role: 'border-slate-200 bg-white/90 text-slate-600',
  },
};

function buildNodeAriaLabel(data: CollaborationGraphNodeData) {
  const presentation = buildCollaborationNodePresentation(data);
  const markers = [
    data.isInitial ? '入口节点' : '',
    data.definition.final_output ? '最终输出节点' : '',
    data.definition.judge ? 'Judge 节点' : '',
  ].filter(Boolean);
  return [
    presentation.title,
    `节点名称 ${data.definition.node_id}`,
    `类型 ${presentation.kindLabel}`,
    `Bot ${presentation.botName}`,
    `角色 ${presentation.roleName}`,
    ...markers,
  ].join('，');
}

function CollaborationNode({ data }: NodeProps<CollaborationFlowNode>) {
  const handleSelect = () => data.onSelect?.(data.definition.node_id);
  const ariaLabel = buildNodeAriaLabel(data);
  const presentation = buildCollaborationNodePresentation(data);
  const tone = getCollaborationNodeTone(data.definition.kind);
  const theme = NODE_THEME_CLASSES[tone];
  const isUnboundBot = !!data.assigneeBinding && !data.assigneeBotId;

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={data.selected}
      aria-label={ariaLabel}
      title={ariaLabel}
      onClick={handleSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleSelect();
        }
      }}
      className={cn(
        'nodrag nopan relative flex min-h-[84px] w-[210px] cursor-pointer flex-col justify-center rounded-[18px] px-4 py-3 shadow-sm outline-none transition-all',
        'focus-visible:ring-2 focus-visible:ring-slate-300 focus-visible:ring-offset-1',
        data.selected
          ? theme.selected
          : data.highlighted
          ? theme.highlighted
          : theme.default,
      )}
    >
      <span
        className={cn(
          'absolute -right-1.5 -top-2 rounded-full border px-2 py-0.5 text-[10px] font-semibold shadow-sm',
          theme.badge,
        )}
      >
        {presentation.kindLabel}
      </span>
      <Handle
        type="target"
        position={Position.Top}
        className="pointer-events-none opacity-0"
      />
      <div className="truncate text-center text-sm font-semibold text-slate-900">
        {presentation.title}
      </div>
      <div className="mt-3 flex min-w-0 items-center justify-between gap-3">
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-xs font-medium',
            isUnboundBot ? 'text-amber-600' : theme.bot,
          )}
          title={presentation.botName}
        >
          {presentation.botName}
        </span>
        <span
          className={cn(
            'max-w-[46%] flex-shrink-0 truncate rounded-full border px-2 py-0.5 text-[11px] font-semibold',
            theme.role,
          )}
          title={presentation.roleName}
        >
          {presentation.roleName}
        </span>
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="pointer-events-none opacity-0"
      />
    </div>
  );
}

const nodeTypes = {
  collaboration: CollaborationNode,
};

const FIT_VIEW_OPTIONS = { padding: 0.2, maxZoom: 1 };

const CollaborationFlowPreview: React.FC<CollaborationFlowPreviewProps> = ({
  graph,
  initialNodes,
  bindingViews,
  selectedNodeId,
  highlightedBinding,
  onNodeSelect,
}) => {
  const canvasRef = useRef<HTMLDivElement>(null);
  const flowInstanceRef =
    useRef<ReactFlowInstance<CollaborationFlowNode> | null>(null);
  const result = useMemo(() => {
    try {
      return {
        layout: buildCollaborationGraphLayout(
          graph,
          initialNodes,
          bindingViews,
        ),
        error: null,
      };
    } catch (error) {
      console.error('[CollaborationFlowPreview] Invalid graph preview:', error);
      return {
        layout: null,
        error:
          error instanceof CollaborationGraphLayoutError
            ? error
            : new CollaborationGraphLayoutError(
                'invalid_graph',
                '协作流程数据无效',
              ),
      };
    }
  }, [bindingViews, graph, initialNodes]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      void flowInstanceRef.current?.fitView(FIT_VIEW_OPTIONS);
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  if (!result.layout) {
    return (
      <Empty
        size="sm"
        className="flex-1"
        title={
          result.error?.code === 'unsupported_mode'
            ? '暂不支持该图模式'
            : '协作流程数据无效'
        }
        description={result.error?.message}
      />
    );
  }

  const nodes: CollaborationFlowNode[] = result.layout.nodes.map((node) => ({
    ...node,
    type: 'collaboration',
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    draggable: false,
    selectable: false,
    focusable: true,
    ariaLabel: buildNodeAriaLabel(node.data),
    style: { width: COLLABORATION_FLOW_NODE_WIDTH },
    data: {
      ...node.data,
      ...getCollaborationNodeInteractionState({
        nodeId: node.id,
        assigneeBinding: node.data.assigneeBinding,
        selectedNodeId,
        highlightedBinding,
      }),
      onSelect: onNodeSelect,
    },
  }));
  const edges: Edge[] = result.layout.edges.map((edge) => ({
    ...edge,
    type: 'default',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#3b82f6' },
    style: { stroke: '#3b82f6', strokeWidth: 2 },
    labelStyle: { fill: '#2563eb', fontSize: 11, fontWeight: 700 },
    labelBgStyle: { fill: '#ffffff', fillOpacity: 0.92 },
    labelBgPadding: [5, 3],
    labelBgBorderRadius: 5,
  }));
  const initialNodeNames = result.layout.nodes
    .filter((node) => node.data.isInitial)
    .map((node) => node.data.title);
  const finalOutputNames = result.layout.nodes
    .filter((node) => node.data.definition.final_output)
    .map((node) => node.data.title);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="sr-only">
        {graph.nodes.length} 个节点，{graph.edges.length} 条边。入口：
        {initialNodeNames.join('、') || '无'}。最终输出：
        {finalOutputNames.join('、') || '无'}。
      </div>
      <div
        ref={canvasRef}
        role="region"
        className="min-h-[300px] flex-1 bg-slate-50/50"
        aria-label="协同剧本协作流程"
      >
        <ReactFlow<CollaborationFlowNode>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={FIT_VIEW_OPTIONS}
          onInit={(instance) => {
            flowInstanceRef.current = instance;
          }}
          onNodeClick={(_, node) => onNodeSelect?.(node.id)}
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
    </div>
  );
};

export default CollaborationFlowPreview;
