/**
 * GraphCanvas — SVG renderer for a TaskGraphView DAG.
 * Ported from bcsPanel.StateMachineRunView's SVG canvas (buildGraphLayout +
 * NodeGroup + EdgePath). Pure (graph, onNodeClick) → SVG.
 */
import React, { useMemo } from 'react';

import { EDGE_RENDER_ORDER } from '../constants';
import type { TaskGraphView } from '../types';
import { buildGraphLayout } from '../utils/layout';
import { EdgePath, NodeGroup } from '../utils/render';
import { getEdgeState, getNodeStatusLabel, getNodeStatusTone } from '../utils/statusTone';

export function GraphCanvas({
  graph,
  selectedNodeId,
  onNodeClick,
}: {
  graph: TaskGraphView;
  selectedNodeId?: string;
  onNodeClick?: (nodeId: string) => void;
}): React.ReactElement {
  const layout = useMemo(
    () => buildGraphLayout(graph.nodes, graph.edges),
    [graph.nodes, graph.edges],
  );

  const sortedEdges = useMemo(
    () =>
      [...layout.edges]
        .map((le, index) => ({
          le,
          index,
          state: getEdgeState(le.source.node.status, le.target.node.status),
        }))
        .sort(
          (a, b) =>
            (EDGE_RENDER_ORDER[a.state] ?? 0) -
              (EDGE_RENDER_ORDER[b.state] ?? 0) || a.index - b.index,
        ),
    [layout.edges],
  );

  return (
    <svg
      width={layout.width}
      height={layout.height}
      style={{ display: 'block', maxWidth: '100%' }}
    >
      <g>
        {sortedEdges.map(({ le, state, index }) => (
          <EdgePath key={`${le.edge.edge_id || index}`} layoutEdge={le} state={state} />
        ))}
        {layout.nodes.map((ln) => {
          const tone = getNodeStatusTone(ln.node.status);
          return (
            <NodeGroup
              key={ln.node.node_id}
              layoutNode={ln}
              tone={tone}
              label={getNodeStatusLabel(ln.node.status)}
              onClick={onNodeClick}
              isSelected={selectedNodeId === ln.node.node_id}
            />
          );
        })}
      </g>
    </svg>
  );
}
