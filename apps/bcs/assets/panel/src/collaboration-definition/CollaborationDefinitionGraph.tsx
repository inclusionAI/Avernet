import React, { useId, useMemo, useState } from 'react';
import styled from 'styled-components';
import type { StateMachineEdge, StateMachineNode } from '../StateMachineRunView';
import {
  buildGraphLayout,
  type GraphLayout,
} from '../state-machine-graph/layout';
import type {
  CollaborationDefinitionGraph as Graph,
  CollaborationDefinitionGraphEdge as GraphEdge,
  CollaborationDefinitionGraphNode as GraphNode,
} from './types';
import { buildDefinitionLoopLayout, definitionLoopPath } from './loopLayout';

const Shell = styled.section`
  overflow: hidden;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #ffffff;
`;

const Toolbar = styled.div`
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e2e8f0;
  padding: 9px 12px;
  color: #475569;
  background: #f8fafc;
  font-size: 12px;
`;

const Controls = styled.div`
  display: flex;
  gap: 6px;

  button {
    min-width: 30px;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    padding: 4px 8px;
    color: #334155;
    background: #ffffff;
    cursor: pointer;
    font: inherit;
  }

  button:disabled {
    cursor: default;
    opacity: 0.45;
  }
`;

const Viewport = styled.div`
  min-height: 320px;
  max-height: 520px;
  overflow: auto;
  background: radial-gradient(circle at 50% 50%, #ffffff 0%, transparent 72%),
    linear-gradient(#eef2f7 1px, transparent 1px),
    linear-gradient(90deg, #eef2f7 1px, transparent 1px), #f8fafc;
  background-size: auto, 24px 24px, 24px 24px, auto;
`;

const Empty = styled.div`
  display: flex;
  min-height: 260px;
  align-items: center;
  justify-content: center;
  color: #64748b;
  font-size: 13px;
`;

const NodeGroup = styled.g`
  cursor: pointer;
  outline: none;

  &:hover rect,
  &:focus-visible rect {
    stroke: #2563eb;
    stroke-width: 2;
  }
`;

const Detail = styled.div`
  border-top: 1px solid #e2e8f0;
  padding: 12px;
  color: #475569;
  background: #ffffff;
  font-size: 12px;
  line-height: 1.6;

  strong {
    color: #0f172a;
  }
`;

function createsCycle(
  adjacency: Map<string, Set<string>>,
  source: string,
  target: string,
): boolean {
  const pending = [target];
  const visited = new Set<string>();
  while (pending.length) {
    const current = pending.pop()!;
    if (current === source) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    adjacency.get(current)?.forEach((next) => pending.push(next));
  }
  return false;
}

function acyclicLayoutEdges(edges: GraphEdge[]): GraphEdge[] {
  const adjacency = new Map<string, Set<string>>();
  return edges.filter((edge) => {
    if (createsCycle(adjacency, edge.source, edge.target)) return false;
    const targets = adjacency.get(edge.source) || new Set<string>();
    targets.add(edge.target);
    adjacency.set(edge.source, targets);
    return true;
  });
}

function previewLayout(graph: Graph, initialNodes: string[]): GraphLayout {
  const logical = buildDefinitionLoopLayout(graph);
  if (logical) return logical;
  const nodes = graph.nodes as unknown as StateMachineNode[];
  const layout = buildGraphLayout(
    nodes,
    acyclicLayoutEdges(graph.edges) as unknown as StateMachineEdge[],
    initialNodes,
  );
  const byId = new Map(layout.nodes.map((entry) => [entry.node.node_id, entry]));
  return {
    ...layout,
    edges: graph.edges.flatMap((edge) => {
      const source = byId.get(edge.source);
      const target = byId.get(edge.target);
      return source && target
        ? [
            {
              edge: edge as unknown as StateMachineEdge,
              source,
              target,
            },
          ]
        : [];
    }),
  };
}

function edgePath(
  source: GraphLayout['nodes'][number],
  target: GraphLayout['nodes'][number],
  returnLane: number,
  returnX?: number,
  labelOffset = 0,
): string {
  if (returnX !== undefined) {
    const sourceX = source.x + source.width;
    const sourceY = source.y + source.height / 2;
    const targetX = target.x + target.width;
    const targetY = target.y + target.height / 2;
    const top = source === target ? target.y - 14 : targetY;
    return `M ${sourceX} ${sourceY} H ${returnX - 8} Q ${returnX} ${sourceY}, ${returnX} ${sourceY - 8} V ${top + 8} Q ${returnX} ${top}, ${returnX - 8} ${top} H ${targetX + 12} Q ${targetX + 4} ${top}, ${targetX + 4} ${targetY} H ${targetX}`;
  }
  const sourceX = source.x + source.width / 2;
  const sourceY = source.y + source.height;
  const targetX = target.x + target.width / 2;
  const targetY = target.y;
  if (targetY <= sourceY) {
    const laneX = Math.max(source.x + source.width, target.x + target.width) +
      42 + returnLane * 18;
    return `M ${sourceX} ${sourceY} C ${laneX} ${sourceY + 18}, ${laneX} ${targetY - 18}, ${targetX} ${targetY}`;
  }
  const midY = sourceY + (targetY - sourceY) / 2;
  if (labelOffset) {
    const midX = (sourceX + targetX) / 2 + labelOffset;
    return `M ${sourceX} ${sourceY} C ${sourceX} ${sourceY + 16}, ${midX} ${sourceY + 16}, ${midX} ${midY} S ${targetX} ${targetY - 16}, ${targetX} ${targetY}`;
  }
  return `M ${sourceX} ${sourceY} C ${sourceX} ${midY}, ${targetX} ${midY}, ${targetX} ${targetY}`;
}

function nodeRole(node: GraphNode): string {
  if (node.assignee?.type === 'bot_binding') return node.assignee.binding || '';
  if (node.assignee?.type === 'runtime_actor') return node.assignee.actor || '';
  return '';
}

function edgeLabel(edge: GraphEdge): string {
  return edge.display_name || edge.loop_route?.logical_outcome || edge.outcome;
}

export interface CollaborationDefinitionGraphProps {
  graph: Graph;
  initialNodes?: string[];
}

export default function CollaborationDefinitionGraph({
  graph,
  initialNodes = [],
}: CollaborationDefinitionGraphProps) {
  const [zoom, setZoom] = useState(1);
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const markerId = `collaboration-arrow-${useId()}`.replace(/[^a-zA-Z0-9_-]/g, '');
  const { layout, layoutError } = useMemo(() => {
    try { return { layout: previewLayout(graph, initialNodes), layoutError: '' }; }
    catch (error) {
      return { layout: { nodes: [], edges: [], width: 0, height: 0 } as GraphLayout,
        layoutError: error instanceof Error ? error.message : '无法生成逻辑流程图' };
    }
  }, [graph, initialNodes]);
  const logicalLoops = Boolean(layout.groups?.length);
  const returnLanes = useMemo(() => {
    let nextLane = 0;
    return layout.edges.map(({ source, target }) => {
      if (target.y > source.y + source.height) return 0;
      const lane = nextLane;
      nextLane += 1;
      return lane;
    });
  }, [layout.edges]);
  const labelOffsets = useMemo(() => {
    const pairs = new Map<string, number[]>();
    layout.edges.forEach(({ edge, returnX }, index) => {
      if (returnX !== undefined) return;
      const key = JSON.stringify([edge.source, edge.target]);
      pairs.set(key, [...(pairs.get(key) || []), index]);
    });
    const offsets = new Map<number, number>();
    pairs.forEach((indices) => indices.forEach((index, lane) => offsets.set(index, (lane - (indices.length - 1) / 2) * 120)));
    return offsets;
  }, [layout.edges]);
  const selectedNode = graph.nodes.find(
    (node) => node.node_id === selectedNodeId,
  );

  return (
    <Shell>
      <Toolbar>
        <span>
          流程图 · {graph.graph_mode}
          {graph.loops ? ` · ${Object.keys(graph.loops).length} 个 Loop` : ''}
        </span>
        <Controls aria-label="流程图缩放">
          <button
            type="button"
            aria-label="缩小流程图"
            disabled={zoom <= 0.65}
            onClick={() => setZoom((value) => Math.max(0.65, value - 0.15))}
          >
            −
          </button>
          <button type="button" onClick={() => setZoom(1)}>
            {Math.round(zoom * 100)}%
          </button>
          <button
            type="button"
            aria-label="放大流程图"
            disabled={zoom >= 1.6}
            onClick={() => setZoom((value) => Math.min(1.6, value + 0.15))}
          >
            +
          </button>
        </Controls>
      </Toolbar>
      {layout.nodes.length ? (
        <Viewport aria-label="可滚动流程图视口">
          <svg
            aria-label="Collaboration definition graph"
            role="img"
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            width={layout.width * zoom}
            height={layout.height * zoom}
            style={{ display: 'block', margin: '0 auto', width: `${zoom * 100}%`,
              maxWidth: layout.width * zoom, height: 'auto' }}
          >
            <defs>
              <marker
                id={markerId}
                markerHeight="8"
                markerWidth="8"
                orient="auto"
                refX="7"
                refY="4"
                viewBox="0 0 8 8"
              >
                <path d="M0,0 L8,4 L0,8 Z" fill="#94a3b8" />
              </marker>
            </defs>
            {(layout.groups || []).map((group) => (
              <g key={group.loopId} data-loop-container={group.loopId}>
                <rect
                  x={group.x}
                  y={group.y}
                  width={group.width}
                  height={group.height}
                  rx="14"
                  fill="#f5f3ff"
                  fillOpacity="0.55"
                  stroke="#c4b5fd"
                  strokeDasharray="6 4"
                />
                <text
                  x={group.x + 12}
                  y={group.y + 18}
                  fill="#6d28d9"
                  fontSize="11"
                  fontWeight="650"
                >
                  Loop · {group.title} · {group.maxIterations} 轮
                </text>
              </g>
            ))}
            {layout.edges.map(({ edge, source, target, returnX, label: layoutLabel }, index) => {
              const previewEdge = edge as unknown as GraphEdge;
              const label = layoutLabel || edgeLabel(previewEdge);
              const isContinue = previewEdge.loop_route?.kind === 'continue';
              const isReturn = returnX !== undefined || target.y <= source.y + source.height;
              const sourceX = source.x + source.width / 2;
              const targetX = target.x + target.width / 2;
              const sourceY = source.y + source.height;
              const targetY = target.y;
              return (
                <g
                  key={`${previewEdge.source}:${previewEdge.outcome}:${previewEdge.target}:${index}`}
                  data-edge-source={previewEdge.source}
                  data-edge-target={previewEdge.target}
                  data-edge-route={previewEdge.loop_route?.kind || 'ordinary'}
                  data-edge-return={isReturn ? 'true' : 'false'}
                >
                  <path
                    d={edgePath(source, target, returnLanes[index], returnX, labelOffsets.get(index))}
                    fill="none"
                    markerEnd={`url(#${markerId})`}
                    stroke={isContinue ? '#8b5cf6' : '#94a3b8'}
                    strokeDasharray={isContinue ? '5 4' : undefined}
                    strokeWidth="1.4"
                  />
                  <text
                    x={returnX === undefined ? (sourceX + targetX) / 2 + (labelOffsets.get(index) || 0) : returnX - 6}
                    y={returnX !== undefined && source === target ? source.y - 18 : (sourceY + targetY) / 2 - 6}
                    fill="#64748b"
                    fontSize="11"
                    textAnchor={returnX === undefined ? 'middle' : 'end'}
                  >
                    <title>{label} · outcome: {previewEdge.outcome}</title>
                    {label.length > 24 ? `${label.slice(0, 23)}…` : label}
                  </text>
                </g>
              );
            })}
            {layout.nodes.map(({ node: rawNode, x, y, width, height }) => {
              const node = rawNode as unknown as GraphNode;
              const selected = node.node_id === selectedNodeId;
              const role = nodeRole(node);
              return (
                <NodeGroup
                  key={node.node_id}
                  data-node-id={node.node_id}
                  role="button"
                  tabIndex={0}
                  aria-label={`节点 ${node.display_name}`}
                  onClick={() => setSelectedNodeId(node.node_id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setSelectedNodeId(node.node_id);
                    }
                  }}
                >
                  <rect
                    x={x}
                    y={y}
                    width={width}
                    height={height}
                    rx="10"
                    fill={selected ? '#eff6ff' : '#ffffff'}
                    stroke={selected ? '#2563eb' : '#cbd5e1'}
                    strokeWidth={selected ? 2 : 1.2}
                  />
                  <text
                    x={x + width / 2}
                    y={y + 23}
                    fill="#0f172a"
                    fontSize="13"
                    fontWeight="650"
                    textAnchor="middle"
                  >
                    {(node.display_name || node.node_id).length > 25
                      ? `${(node.display_name || node.node_id).slice(0, 24)}…`
                      : node.display_name || node.node_id}
                  </text>
                  <text
                    x={x + width / 2}
                    y={y + 44}
                    fill="#64748b"
                    fontSize="10"
                    textAnchor="middle"
                  >
                    {[node.kind, role].filter(Boolean).join(' · ').slice(0, 35)}
                  </text>
                </NodeGroup>
              );
            })}
          </svg>
        </Viewport>
      ) : (
        <Empty role={layoutError ? 'alert' : undefined}>{layoutError || '流程图没有节点'}</Empty>
      )}
      {selectedNode ? (
        <Detail>
          <strong>{selectedNode.display_name || selectedNode.node_id}</strong>
          <div>节点 ID：{logicalLoops ? selectedNode.execution?.definition_node_id || selectedNode.node_id : selectedNode.node_id}</div>
          <div>类型：{selectedNode.kind}</div>
          {nodeRole(selectedNode) ? <div>逻辑角色：{nodeRole(selectedNode)}</div> : null}
          {selectedNode.execution ? (
            <div>
              Loop：{definitionLoopPath(selectedNode)} · {logicalLoops ? '' : `第 ${selectedNode.execution.iteration}/`}
              {selectedNode.execution.max_iterations} 轮
            </div>
          ) : null}
        </Detail>
      ) : null}
    </Shell>
  );
}
