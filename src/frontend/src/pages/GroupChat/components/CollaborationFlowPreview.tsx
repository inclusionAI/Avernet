import { Empty } from '@/components';
import type { CollaborationDefinitionGraphPreview } from '@/services/backend-api/BcnController';
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import React, { useEffect, useMemo, useRef } from 'react';
import {
  buildCollaborationGraphLayout,
  COLLABORATION_FLOW_NODE_WIDTH,
  CollaborationGraphLayoutError,
  getCollaborationNodeInteractionState,
  type CollaborationBindingView,
} from '../utils/collaborationGraphLayout';

import CollaborationLoopEdge from './CollaborationLoopEdge';
import CollaborationTransitionEdge from './CollaborationTransitionEdge';
import CollaborationLoopContainer from './CollaborationLoopContainer';
import { buildCollaborationLoopLayout, hasLogicalLoopDescriptors } from '../utils/collaborationLoopLayout';
import CollaborationPreviewMetadata from './CollaborationPreviewMetadata';
import CollaborationNode, {
  buildNodeAriaLabel,
} from './CollaborationPreviewNode';

interface CollaborationFlowPreviewProps {
  graph: CollaborationDefinitionGraphPreview;
  initialNodes: string[];
  bindingViews: Record<string, CollaborationBindingView>;
  selectedNodeId?: string;
  highlightedBinding?: string;
  onNodeSelect?: (nodeId: string) => void;
}

const nodeTypes = {
  collaboration: CollaborationNode,
  loopContainer: CollaborationLoopContainer,
};

const edgeTypes = { loop: CollaborationLoopEdge, transition: CollaborationTransitionEdge };

const FIT_VIEW_OPTIONS = { padding: 0.2, maxZoom: 1 };
const LOOP_FIT_VIEW_OPTIONS = { padding: 0.05, maxZoom: 1 };

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
    useRef<ReactFlowInstance<Node> | null>(null);
  const hasLoops = hasLogicalLoopDescriptors(graph);
  const fitViewOptions = hasLoops ? LOOP_FIT_VIEW_OPTIONS : FIT_VIEW_OPTIONS;
  const result = useMemo(() => {
    try {
      return {
        layout: hasLoops
          ? buildCollaborationLoopLayout(graph, initialNodes, bindingViews)
          : { ...buildCollaborationGraphLayout(graph, initialNodes, bindingViews), groups: [] },
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
  }, [bindingViews, graph, initialNodes, hasLoops]);

  // Binding/selection updates rebuild node data, but should preserve the user's viewport.
  const viewportLayoutKey = result.layout && JSON.stringify({
    nodes: result.layout.nodes.map(({ id, parentId, position }) => [id, parentId, position.x, position.y]),
    groups: result.layout.groups.map(({ id, position, width, height }) => [id, position.x, position.y, width, height]),
    edges: result.layout.edges.map(({ source, target }) => [source, target]),
  });
  useEffect(() => {
    const frame = requestAnimationFrame(() => { void flowInstanceRef.current?.fitView(fitViewOptions); });
    return () => cancelAnimationFrame(frame);
  }, [viewportLayoutKey, fitViewOptions]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      void flowInstanceRef.current?.fitView(fitViewOptions);
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [fitViewOptions]);

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

  const nodes: Node[] = result.layout.nodes.map((node) => ({
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
    sourceHandle: edge.sourceHandle || 'main',
    targetHandle: edge.targetHandle || 'main',
    type: edge.data?.loopRoute ? 'loop' : 'transition',
    data: edge.data ?? { outcome: edge.outcome },
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
      <CollaborationPreviewMetadata graph={graph} />
      <div className="sr-only">
        {result.layout.nodes.length} 个节点，{result.layout.edges.length} 条边。入口：
        {initialNodeNames.join('、') || '无'}。最终输出：
        {finalOutputNames.join('、') || '无'}。
      </div>
      <div
        ref={canvasRef}
        role="region"
        className="min-h-[300px] flex-1 bg-slate-50/50"
        aria-label="协同剧本协作流程"
      >
        <ReactFlow<Node>
          nodes={[...result.layout.groups.map((group) => ({ id: group.id, type: 'loopContainer',
            position: group.position, data: { title: group.title, maxIterations: group.maxIterations, loopId: group.loopId },
            style: { width: group.width, height: group.height }, draggable: false, selectable: false, focusable: false })), ...nodes]}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={fitViewOptions}
          onInit={(instance) => {
            flowInstanceRef.current = instance;
          }}
          onNodeClick={(_, node) => { if (node.type === 'collaboration') onNodeSelect?.(node.id); }}
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
