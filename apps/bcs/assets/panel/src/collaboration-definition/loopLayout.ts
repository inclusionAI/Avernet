import type { StateMachineEdge, StateMachineNode } from '../StateMachineRunView';
import { buildGraphLayout, type GraphLayout, type LoopGroupLayout } from '../state-machine-graph/layout';
import type { CollaborationDefinitionGraph as Graph, CollaborationDefinitionGraphNode as Node } from './types';

const PADDING = 24;
const HEADER = 48;
const RETURN_LANE = 64;
const NODE_WIDTH = 240;
const NODE_HEIGHT = 58;

interface Unit {
  node: Node;
  width: number;
  height: number;
  body?: GraphLayout;
  group?: LoopGroupLayout;
}

export const definitionLoopPath = (node: Node) => node.execution?.loop_path
  ?.map((frame) => frame.loop_id).join('/') || node.execution?.loop_id;

/** Project execution instances onto the authoring structure, without changing the graph. */
export function buildDefinitionLoopLayout(graph: Graph): GraphLayout | undefined {
  const paths = new Set(graph.nodes.flatMap((node) => node.execution ? [definitionLoopPath(node)!] : []));
  if (!paths.size || ![...paths].every((path) => graph.loops?.[path])) return undefined;
  const loops = graph.loops!;
  const byId = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const firstNodes = new Map(graph.nodes.filter((node) => node.execution &&
    (node.execution.loop_path?.every((frame) => frame.iteration === 1) ?? node.execution.iteration === 1))
    .map((node) => [JSON.stringify([definitionLoopPath(node), node.execution!.definition_node_id]), node]));
  const first = (path: string, id: string): Node => {
    const node = firstNodes.get(JSON.stringify([path, id]));
    if (!node) throw new Error(`Loop ${path} 缺少逻辑节点 ${id}`);
    return node;
  };
  const canonical = (id: string) => {
    const node = byId.get(id);
    return node?.execution ? first(definitionLoopPath(node)!, node.execution.definition_node_id).node_id : id;
  };
  const groupId = (path: string) => JSON.stringify(['loop-container', path]);
  const groups = new Map<string, Unit>();
  const nodeUnit = (node: Node): Unit => ({ node, width: NODE_WIDTH, height: NODE_HEIGHT });

  // Rank each scope as a DAG, then account for the real size of its Loop containers.
  const arrange = (units: Unit[], scope?: string): GraphLayout => {
    const unitIds = new Set(units.map(({ node }) => node.node_id));
    const unitId = (id: string) => {
      const path = definitionLoopPath(byId.get(id)!);
      if (!path || path === scope) return canonical(id);
      if (scope && !path.startsWith(`${scope}/`)) return canonical(id);
      const child = scope ? `${scope}/${path.slice(scope.length + 1).split('/')[0]}` : path.split('/')[0];
      return groupId(child);
    };
    const seen = new Set<string>();
    const edges = graph.edges.flatMap((edge) => {
      if (edge.loop_route?.kind === 'continue') return [];
      const source = unitId(edge.source), target = unitId(edge.target);
      const key = JSON.stringify([source, target]);
      if (source === target || !unitIds.has(source) || !unitIds.has(target) || seen.has(key)) return [];
      seen.add(key);
      return [{ ...edge, source, target }];
    });
    const ranked = buildGraphLayout(units.map(({ node }) => node) as unknown as StateMachineNode[],
      edges as unknown as StateMachineEdge[]);
    const unitById = new Map(units.map((unit) => [unit.node.node_id, unit]));
    const rows = new Map<number, Unit[]>();
    ranked.nodes.forEach(({ node, y }) => rows.set(y, [...(rows.get(y) || []), unitById.get(node.node_id)!]));
    const ordered = [...rows].sort(([a], [b]) => a - b).map(([, row]) => row);
    const rowWidth = (row: Unit[]) => row.reduce((sum, unit) => sum + unit.width, 0) + (row.length - 1) * 32;
    const width = Math.max(0, ...ordered.map(rowWidth)) + PADDING * 2;
    const result: GraphLayout = { nodes: [], edges: [], groups: [], width, height: 0 };
    let y = PADDING;
    ordered.forEach((row) => {
      let x = (width - rowWidth(row)) / 2;
      row.forEach((unit) => {
        if (unit.body && unit.group) {
          result.groups!.push({ ...unit.group, x, y });
          result.groups!.push(...(unit.body.groups || []).map((group) => ({ ...group,
            x: group.x + x + RETURN_LANE, y: group.y + y + HEADER })));
          result.nodes.push(...unit.body.nodes.map((node) => ({ ...node,
            x: node.x + x + RETURN_LANE, y: node.y + y + HEADER })));
        } else {
          result.nodes.push({ node: unit.node as unknown as StateMachineNode, x, y,
            width: unit.width, height: unit.height });
        }
        x += unit.width + 32;
      });
      y += Math.max(...row.map((unit) => unit.height)) + 56;
    });
    result.height = y - (ordered.length ? 56 : 0) + PADDING;
    return result;
  };

  Object.keys(loops).sort((a, b) => b.split('/').length - a.split('/').length).forEach((path) => {
    const loop = loops[path];
    const body = arrange(loop.body_node_ids.map((id) => groups.get(`${path}/${id}`) || nodeUnit(first(path, id))), path);
    const width = body.width + RETURN_LANE * 2;
    const height = body.height + HEADER;
    groups.set(path, {
      node: { node_id: groupId(path), display_name: loop.display_name, kind: 'loop', final_output: false, judge: false },
      width, height, body,
      group: { loopId: path, title: loop.display_name || path, maxIterations: loop.max_iterations,
        iteration: 1, x: 0, y: 0, width, height },
    });
  });
  const layout = arrange([
    ...graph.nodes.filter((node) => !node.execution).map(nodeUnit),
    ...[...groups].filter(([path]) => !path.includes('/')).map(([, unit]) => unit),
  ]);
  const positions = new Map(layout.nodes.map((node) => [node.node.node_id, node]));
  const position = (id: string) => {
    const node = positions.get(id);
    if (!node) throw new Error(`流程图缺少逻辑节点 ${id}`);
    return node;
  };
  const seen = new Set<string>();
  layout.edges = graph.edges.flatMap((edge) => {
    if (edge.loop_route?.kind === 'continue') return [];
    const source = canonical(edge.source), target = canonical(edge.target);
    const key = JSON.stringify([source, target, edge.outcome, edge.loop_route, edge.display_name]);
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ edge: { ...edge, source, target } as StateMachineEdge,
      source: position(source), target: position(target) }];
  });
  layout.groups!.forEach((group) => {
    const loop = loops[group.loopId];
    if (loop.max_iterations <= 1) return;
    const source = first(group.loopId, loop.result_node_id).node_id;
    const target = first(group.loopId, loop.entry_node_id).node_id;
    const outcome = loop.continue_outcomes.join(' / ');
    layout.edges.push({
      edge: { source, target, outcome, display_name: loop.continue_display_name,
        loop_route: { kind: 'continue', logical_outcome: outcome } },
      source: position(source), target: position(target),
      returnX: group.x + group.width - 16,
      label: loop.continue_display_name || 'continue',
    });
  });
  return layout;
}
