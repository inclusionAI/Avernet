import type {
  CollaborationDefinitionGraphNode,
  CollaborationDefinitionGraphPreview,
} from '@/domain/collaboration/graphTypes';
import {
  buildCollaborationGraphLayout, COLLABORATION_FLOW_NODE_HEIGHT as HEIGHT,
  COLLABORATION_FLOW_NODE_WIDTH as WIDTH, CollaborationGraphLayoutError,
  type CollaborationBindingView, type CollaborationGraphLayout,
  type CollaborationGraphLayoutNode,
} from './collaborationGraphLayout';

export interface LoopContainerLayout {
  id: string;
  loopId: string;
  parentId?: string;
  title: string;
  maxIterations: number;
  position: { x: number; y: number };
  width: number;
  height: number;
}

export const LOOP_RETURN_LANE = 64;
const BODY_PADDING = 24;
const HEADER_HEIGHT = 48;

export function hasLogicalLoopDescriptors(graph: CollaborationDefinitionGraphPreview) {
  const ids = new Set(graph.nodes.flatMap((node) => node.execution ? [node.execution.loop_path
    ?.map((frame) => frame.loop_id).join('/') || node.execution.loop_id] : []));
  return ids.size > 0 && [...ids].every((id) => graph.loops?.[id]);
}

function buildSingleCollaborationLoopLayout(
  graph: CollaborationDefinitionGraphPreview,
  initialNodes: string[],
  bindings: Record<string, CollaborationBindingView>,
  expanded: CollaborationGraphLayout,
): CollaborationGraphLayout & { groups: LoopContainerLayout[] } {
  const byId = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const firstByLogical = new Map(graph.nodes.filter((node) => node.execution?.iteration === 1)
    .map((node) => [JSON.stringify([node.execution!.loop_id, node.execution!.definition_node_id]), node]));
  const first = (loopId: string, logicalId: string) => {
    const node = firstByLogical.get(JSON.stringify([loopId, logicalId]));
    if (!node) throw new CollaborationGraphLayoutError('invalid_graph', `Loop ${loopId} 缺少逻辑节点 ${logicalId}`);
    return node;
  };
  const groupId = (id: string) => `loop-container:${id}`;
  const unitId = (id: string) => {
    const meta = byId.get(id)?.execution;
    return meta ? groupId(meta.loop_id) : id;
  };
  const displayId = (id: string) => {
    const meta = byId.get(id)?.execution;
    return meta ? first(meta.loop_id, meta.definition_node_id).node_id : id;
  };
  const bodies = new Map<string, CollaborationGraphLayout>();
  const groups = Object.entries(graph.loops!).map(([loopId, loop]) => {
    const bodyNodes = loop.body_node_ids.map((id) => first(loopId, id));
    const ids = new Set(bodyNodes.map((node) => node.node_id));
    const body = buildCollaborationGraphLayout({ graph_mode: 'acyclic', nodes: bodyNodes,
      edges: graph.edges.filter((edge) => !edge.loop_route && ids.has(edge.source) && ids.has(edge.target)) },
      [first(loopId, loop.entry_node_id).node_id], bindings);
    bodies.set(loopId, body);
    const minX = Math.min(...body.nodes.map((node) => node.position.x));
    const maxX = Math.max(...body.nodes.map((node) => node.position.x + WIDTH));
    return { id: groupId(loopId), loopId, title: loop.display_name || loopId, maxIterations: loop.max_iterations,
      position: { x: 0, y: 0 }, width: maxX - minX + 2 * (BODY_PADDING + LOOP_RETURN_LANE),
      height: Math.max(...body.nodes.map((node) => node.position.y)) + HEIGHT + HEADER_HEIGHT + BODY_PADDING };
  });
  const units = [...graph.nodes.filter((node) => !node.execution), ...groups.map((group) => ({
    ...first(group.loopId, graph.loops![group.loopId].entry_node_id), node_id: group.id, execution: undefined,
  }))];
  const unitEdges = graph.edges.filter((edge) => unitId(edge.source) !== unitId(edge.target))
    .map((edge) => ({ ...edge, source: unitId(edge.source), target: unitId(edge.target) }));
  const outer = buildCollaborationGraphLayout({ graph_mode: 'acyclic', nodes: units, edges: unitEdges }, [], bindings);
  const groupsById = new Map(groups.map((group) => [group.id, group]));
  const positions = new Map<string, { x: number; y: number }>();
  const rows = new Map<number, typeof outer.nodes>();
  outer.nodes.forEach((node) => rows.set(node.position.y, [...(rows.get(node.position.y) ?? []), node]));
  let y = 0;
  [...rows].sort(([a], [b]) => a - b).forEach(([, row]) => {
    const width = row.reduce((sum, node) => sum + (groupsById.get(node.id)?.width ?? WIDTH), 0) + (row.length - 1) * 60;
    let x = -width / 2;
    row.forEach((node) => {
      positions.set(node.id, { x, y });
      x += (groupsById.get(node.id)?.width ?? WIDTH) + 60;
    });
    y += Math.max(...row.map((node) => groupsById.get(node.id)?.height ?? HEIGHT)) + 72;
  });
  groups.forEach((group) => { group.position = positions.get(group.id)!; });
  const nodes: CollaborationGraphLayoutNode[] = expanded.nodes.filter((node) => !node.data.definition.execution)
    .map((node) => ({ ...node, position: positions.get(node.id)! }));
  groups.forEach((group) => {
    const body = bodies.get(group.loopId)!;
    const minX = Math.min(...body.nodes.map((node) => node.position.x));
    body.nodes.forEach((node) => nodes.push({ ...node, parentId: group.id,
      position: { x: node.position.x - minX + BODY_PADDING + LOOP_RETURN_LANE, y: node.position.y + HEADER_HEIGHT },
      data: { ...node.data, title: node.data.definition.display_name || node.data.definition.execution!.definition_node_id,
        isInitial: initialNodes.includes(node.id), logicalLoopView: true } }));
  });
  const seen = new Set<string>();
  const labelLanes = new Map<string, number>();
  const edges = expanded.edges.filter((edge) => {
    const source = byId.get(edge.source)!;
    const target = byId.get(edge.target)!;
    if (edge.data?.loopRoute.kind === 'continue') return false;
    if (!edge.data?.loopRoute && source.execution && target.execution?.loop_id === source.execution.loop_id
      && source.execution.iteration !== 1) return false;
    return true;
  }).flatMap((edge) => {
    const source = displayId(edge.source), target = displayId(edge.target);
    const id = JSON.stringify([source, target, edge.data?.outcome, edge.data?.loopRoute.kind, edge.label]);
    if (seen.has(id)) return [];
    seen.add(id);
    const pair = JSON.stringify([source, target]);
    const labelLane = labelLanes.get(pair) ?? 0;
    labelLanes.set(pair, labelLane + 1);
    return [{ ...edge, id, source, target, ...(edge.data ? { data: { ...edge.data, labelLane, logicalView: true } } : {}) }];
  });
  groups.forEach((group) => {
    const loop = graph.loops![group.loopId];
    if (loop.max_iterations <= 1) return;
    edges.push({ id: `${group.id}:return`, outcome: loop.continue_outcomes.join(' / '), source: first(group.loopId, loop.result_node_id).node_id,
      target: first(group.loopId, loop.entry_node_id).node_id, sourceHandle: 'loop-return', targetHandle: 'loop-return',
      label: loop.continue_display_name ?? 'continue', data: { loopRoute: { kind: 'continue', logical_outcome: loop.continue_outcomes.join(' / ') },
        outcome: loop.continue_outcomes.join(' / '), labelLane: 0, logicalView: true, returnEdge: true,
        returnX: group.position.x + group.width - 12 } });
  });
  return { nodes, edges, groups };
}

function buildNestedCollaborationLoopLayout(
  graph: CollaborationDefinitionGraphPreview,
  initialNodes: string[],
  bindings: Record<string, CollaborationBindingView>,
  expanded: CollaborationGraphLayout,
): CollaborationGraphLayout & { groups: LoopContainerLayout[] } {
  const loops = graph.loops!;
  const groupId = (path: string) => `loop-container:${path}`;
  const pathOf = (node: CollaborationDefinitionGraphNode) => node.execution?.loop_path
    ?.map((frame) => frame.loop_id).join('/') || node.execution?.loop_id;
  const firstByLogical = new Map(graph.nodes.filter((node) => node.execution
    && (node.execution.loop_path?.every((frame) => frame.iteration === 1)
      ?? node.execution.iteration === 1))
    .map((node) => [JSON.stringify([pathOf(node), node.execution!.definition_node_id]), node]));
  const first = (path: string, logicalId: string) => {
    const node = firstByLogical.get(JSON.stringify([path, logicalId]));
    if (!node) throw new CollaborationGraphLayoutError('invalid_graph', `Loop ${path} 缺少逻辑节点 ${logicalId}`);
    return node;
  };
  const byId = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const expandedById = new Map(expanded.nodes.map((node) => [node.id, node]));
  const canonicalId = (id: string) => {
    const node = byId.get(id);
    return node?.execution ? first(pathOf(node)!, node.execution.definition_node_id).node_id : id;
  };
  const groupsByPath = new Map<string, LoopContainerLayout>();
  const nodes: CollaborationGraphLayoutNode[] = [];

  const descriptorPaths = Object.keys(loops).sort((left, right) =>
    right.split('/').length - left.split('/').length || left.localeCompare(right));
  descriptorPaths.forEach((path) => {
    const loop = loops[path];
    const directUnits: CollaborationDefinitionGraphNode[] = loop.body_node_ids.map((id) => {
      const childPath = `${path}/${id}`;
      return loops[childPath]
        ? { ...first(childPath, loops[childPath].entry_node_id), node_id: groupId(childPath), execution: undefined }
        : first(path, id);
    });
    const unitIds = new Set(directUnits.map((node) => node.node_id));
    const unitId = (id: string) => {
      const canonical = canonicalId(id);
      const node = byId.get(canonical);
      const nodePath = node && pathOf(node);
      if (nodePath === path) return canonical;
      if (nodePath?.startsWith(`${path}/`)) {
        return groupId(`${path}/${nodePath.slice(path.length + 1).split('/')[0]}`);
      }
      return canonical;
    };
    const seenUnits = new Set<string>();
    const unitEdges = graph.edges.flatMap((edge) => {
      if (edge.loop_route?.kind === 'continue') return [];
      const source = unitId(edge.source), target = unitId(edge.target);
      const key = JSON.stringify([source, target]);
      if (source === target || !unitIds.has(source) || !unitIds.has(target) || seenUnits.has(key)) return [];
      seenUnits.add(key);
      return [{ ...edge, source, target, loop_route: undefined }];
    });
    const body = buildCollaborationGraphLayout(
      { graph_mode: 'acyclic', nodes: directUnits, edges: unitEdges },
      [first(path, loop.entry_node_id).node_id], bindings,
    );
    const rows = new Map<number, typeof body.nodes>();
    body.nodes.forEach((node) => rows.set(node.position.y, [...(rows.get(node.position.y) || []), node]));
    const widthOf = (id: string) => groupsByPath.get(id.slice('loop-container:'.length))?.width ?? WIDTH;
    const heightOf = (id: string) => groupsByPath.get(id.slice('loop-container:'.length))?.height ?? HEIGHT;
    const rowEntries = [...rows].sort(([left], [right]) => left - right);
    const rowWidth = (row: typeof body.nodes) => row.reduce((sum, node) => sum + widthOf(node.id), 0)
      + (row.length - 1) * 60;
    const contentWidth = Math.max(...rowEntries.map(([, row]) => rowWidth(row)));
    const positions = new Map<string, { x: number; y: number }>();
    let y = HEADER_HEIGHT;
    rowEntries.forEach(([, row]) => {
      let x = BODY_PADDING + LOOP_RETURN_LANE + (contentWidth - rowWidth(row)) / 2;
      row.forEach((node) => {
        positions.set(node.id, { x, y });
        x += widthOf(node.id) + 60;
      });
      y += Math.max(...row.map((node) => heightOf(node.id))) + 72;
    });
    const parentPath = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : undefined;
    const group: LoopContainerLayout = {
      id: groupId(path), loopId: path, title: loop.display_name || path,
      maxIterations: loop.max_iterations, position: { x: 0, y: 0 },
      width: contentWidth + 2 * (BODY_PADDING + LOOP_RETURN_LANE),
      height: y - 72 + BODY_PADDING,
      ...(parentPath ? { parentId: groupId(parentPath) } : {}),
    };
    groupsByPath.set(path, group);
    body.nodes.forEach((unit) => {
      const childPath = unit.id.startsWith('loop-container:')
        ? unit.id.slice('loop-container:'.length) : undefined;
      if (childPath) {
        const child = groupsByPath.get(childPath)!;
        child.position = positions.get(unit.id)!;
        return;
      }
      const node = expandedById.get(unit.id)!;
      nodes.push({ ...node, parentId: group.id, position: positions.get(unit.id)!,
        data: { ...node.data, title: node.data.definition.display_name || node.data.definition.execution!.definition_node_id,
          isInitial: initialNodes.includes(node.id), logicalLoopView: true } });
    });
  });

  const rootGroups = descriptorPaths.filter((path) => !path.includes('/')).map((path) => groupsByPath.get(path)!);
  const topNodes = graph.nodes.filter((node) => !node.execution);
  const topUnits: CollaborationDefinitionGraphNode[] = [
    ...topNodes,
    ...rootGroups.map((group) => ({ ...first(group.loopId, loops[group.loopId].entry_node_id),
      node_id: group.id, execution: undefined })),
  ];
  const topIds = new Set(topUnits.map((node) => node.node_id));
  const topUnitId = (id: string) => {
    const canonical = canonicalId(id);
    const node = byId.get(canonical);
    const nodePath = node && pathOf(node);
    return nodePath ? groupId(nodePath.split('/')[0]) : canonical;
  };
  const seenTop = new Set<string>();
  const topEdges = graph.edges.flatMap((edge) => {
    if (edge.loop_route?.kind === 'continue') return [];
    const source = topUnitId(edge.source), target = topUnitId(edge.target);
    const key = JSON.stringify([source, target]);
    if (source === target || !topIds.has(source) || !topIds.has(target) || seenTop.has(key)) return [];
    seenTop.add(key);
    return [{ ...edge, source, target, loop_route: undefined }];
  });
  const outer = buildCollaborationGraphLayout(
    { graph_mode: 'acyclic', nodes: topUnits, edges: topEdges }, [], bindings,
  );
  const rows = new Map<number, typeof outer.nodes>();
  outer.nodes.forEach((node) => rows.set(node.position.y, [...(rows.get(node.position.y) || []), node]));
  const widthOf = (id: string) => groupsByPath.get(id.slice('loop-container:'.length))?.width ?? WIDTH;
  const heightOf = (id: string) => groupsByPath.get(id.slice('loop-container:'.length))?.height ?? HEIGHT;
  const positions = new Map<string, { x: number; y: number }>();
  let y = 0;
  [...rows].sort(([left], [right]) => left - right).forEach(([, row]) => {
    const width = row.reduce((sum, node) => sum + widthOf(node.id), 0) + (row.length - 1) * 60;
    let x = -width / 2;
    row.forEach((node) => {
      positions.set(node.id, { x, y });
      x += widthOf(node.id) + 60;
    });
    y += Math.max(...row.map((node) => heightOf(node.id))) + 72;
  });
  rootGroups.forEach((group) => { group.position = positions.get(group.id)!; });
  topNodes.forEach((definition) => {
    const node = expandedById.get(definition.node_id)!;
    nodes.push({ ...node, position: positions.get(node.id)! });
  });

  const visibleIds = new Set(nodes.map((node) => node.id));
  const seenEdges = new Set<string>();
  const labelLanes = new Map<string, number>();
  const edges = expanded.edges.flatMap((edge) => {
    if (edge.data?.loopRoute.kind === 'continue') return [];
    const source = canonicalId(edge.source), target = canonicalId(edge.target);
    if (!visibleIds.has(source) || !visibleIds.has(target)) return [];
    const id = JSON.stringify([source, target, edge.data?.outcome, edge.data?.loopRoute.kind, edge.label]);
    if (seenEdges.has(id)) return [];
    seenEdges.add(id);
    const pair = JSON.stringify([source, target]);
    const labelLane = labelLanes.get(pair) ?? 0;
    labelLanes.set(pair, labelLane + 1);
    return [{ ...edge, id, source, target,
      ...(edge.data ? { data: { ...edge.data, labelLane, logicalView: true } } : {}) }];
  });
  const groupById = new Map([...groupsByPath.values()].map((group) => [group.id, group]));
  const absoluteX = (group: LoopContainerLayout): number => group.position.x
    + (group.parentId ? absoluteX(groupById.get(group.parentId)!) : 0);
  groupsByPath.forEach((group, path) => {
    const loop = loops[path];
    if (loop.max_iterations <= 1) return;
    edges.push({ id: `${group.id}:return`, outcome: loop.continue_outcomes.join(' / '),
      source: first(path, loop.result_node_id).node_id, target: first(path, loop.entry_node_id).node_id,
      sourceHandle: 'loop-return', targetHandle: 'loop-return', label: loop.continue_display_name ?? 'continue',
      data: { loopRoute: { kind: 'continue', logical_outcome: loop.continue_outcomes.join(' / ') },
        outcome: loop.continue_outcomes.join(' / '), labelLane: 0, logicalView: true,
        returnEdge: true, returnX: absoluteX(group) + group.width - 12 } });
  });
  const groups = [...groupsByPath.values()].sort((left, right) =>
    left.loopId.split('/').length - right.loopId.split('/').length || left.loopId.localeCompare(right.loopId));
  return { nodes, edges, groups };
}

export function buildCollaborationLoopLayout(
  graph: CollaborationDefinitionGraphPreview,
  initialNodes: string[],
  bindings: Record<string, CollaborationBindingView>,
): CollaborationGraphLayout & { groups: LoopContainerLayout[] } {
  // Validate the execution graph first. Display back edges never enter DAG ranking.
  const expanded = buildCollaborationGraphLayout(graph, initialNodes, bindings);
  if (!hasLogicalLoopDescriptors(graph)) return { ...expanded, groups: [] };
  return Object.keys(graph.loops!).some((path) => path.includes('/'))
    ? buildNestedCollaborationLoopLayout(graph, initialNodes, bindings, expanded)
    : buildSingleCollaborationLoopLayout(graph, initialNodes, bindings, expanded);
}
