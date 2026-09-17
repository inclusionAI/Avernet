import type { StateMachineRunGraph, StateMachineNode, StateMachineEdge } from '../StateMachineRunView';
import { buildGraphLayout, PADDING, type GraphLayout, type LayoutNode, type LoopGroupLayout } from './layout';

const active = (node: StateMachineNode) => ['ready', 'running', 'retry_scheduled'].includes(node.status || '');
const entered = (node: StateMachineNode) => node.started_at != null || ['completed', 'failed'].includes(node.status || '') || (node.attempt || 0) > 0;
const BODY_PADDING = 24;
const RETURN_LANE = 64;
const HEADER_HEIGHT = 48;
const ROW_GAP = 64;

export function hasLoopDescriptors(graph: StateMachineRunGraph) {
  const ids = new Set(graph.nodes.flatMap((node) => node.execution ? [node.execution.loop_id] : []));
  return ids.size > 0 && [...ids].every((id) => graph.loops?.[id]);
}

export function loopProgress(graph: StateMachineRunGraph, loopId: string) {
  const loop = graph.loops![loopId];
  const nodes = graph.nodes.filter((node) => node.execution?.loop_id === loopId);
  const running = nodes.filter(active);
  const started = nodes.filter(entered);
  const iteration = Math.max(1, ...(running.length ? running : started).map((node) => node.execution!.iteration));
  const results = nodes.filter((node) => node.execution!.definition_node_id === loop.result_node_id && node.status === 'completed');
  const exit = results.find((node) => loop.break_outcomes.includes(node.outcome || ''));
  const exhausted = results.find((node) => node.execution!.iteration === loop.max_iterations && loop.continue_outcomes.includes(node.outcome || ''));
  const status = exit ? exit.outcome : exhausted ? loop.exhausted_outcome
    : graph.run.status === 'aborted' ? '已取消'
    : nodes.some((node) => node.status === 'failed') && graph.run.status === 'failed' ? '执行失败'
    : running.some((node) => node.kind === 'human_input') ? '等待人工'
    : running.length || started.length ? '执行中' : nodes.every((node) => node.status === 'skipped') ? '未执行' : '未开始';
  const iterations = [...new Set(nodes.filter((node) => active(node) || entered(node)).map((node) => node.execution!.iteration))].sort((a, b) => a - b);
  const rounds = iterations.map((round) => {
    const members = nodes.filter((node) => node.execution!.iteration === round);
    const result = members.find((node) => node.execution!.definition_node_id === loop.result_node_id);
    const label = result?.status === 'completed' ? '已完成'
      : graph.run.status === 'aborted' && members.some((node) => entered(node) || active(node)) ? '已取消'
      : members.some(active) ? '进行中'
      : members.some((node) => node.status === 'failed') ? '失败'
      : members.every((node) => node.status === 'skipped') ? '已跳过' : '未开始';
    return { iteration: round, label };
  });
  return { iteration, status, rounds };
}

export function buildLoopLayout(graph: StateMachineRunGraph, choices: Record<string, number>, expanded = false): GraphLayout {
  if (!hasLoopDescriptors(graph)) return buildGraphLayout(graph.nodes, graph.edges, graph.definition.initial_nodes);
  const byId = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const loopIds = Object.keys(graph.loops!);
  const selected = new Map(loopIds.map((id) => {
    const progress = loopProgress(graph, id);
    return [id, progress.rounds.some((round) => round.iteration === choices[id]) ? choices[id] : progress.iteration];
  }));
  // A never-entered loop has one structural placeholder; future executions are not history.
  const scopes = loopIds.flatMap((loopId) => {
    const history = loopProgress(graph, loopId).rounds;
    const iterations = expanded && history.length ? history.map((round) => round.iteration) : [selected.get(loopId)!];
    return iterations.map((iteration) => ({ loopId, iteration }));
  });
  const chosen = new Map(graph.nodes.filter((node) => node.execution).map((node) =>
    [JSON.stringify([node.execution!.loop_id, node.execution!.iteration, node.execution!.definition_node_id]), node]));
  const getNode = (loopId: string, iteration: number, id: string) => {
    const node = chosen.get(JSON.stringify([loopId, iteration, id]));
    if (!node) throw new Error(`Loop ${loopId} #${iteration} is missing logical node ${id}`);
    return node;
  };
  const groupKey = (loopId: string, iteration: number) => JSON.stringify(['loop-container', loopId, iteration]);
  const visibleScopes = new Set(scopes.map(({ loopId, iteration }) => groupKey(loopId, iteration)));
  const visibleNodes = graph.nodes.filter((node) => node.execution
    ? visibleScopes.has(groupKey(node.execution.loop_id, node.execution.iteration))
    : !expanded || active(node) || entered(node));
  const visibleIds = new Set(visibleNodes.map((node) => node.node_id));
  const graphEdges = expanded ? graph.edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)) : graph.edges;
  const unitId = (id: string) => {
    const meta = byId.get(id)?.execution;
    return meta ? groupKey(meta.loop_id, expanded ? meta.iteration : selected.get(meta.loop_id)!) : id;
  };
  const displayId = (id: string) => {
    const meta = byId.get(id)?.execution;
    return meta && !expanded ? getNode(meta.loop_id, selected.get(meta.loop_id)!, meta.definition_node_id).node_id : id;
  };
  const bodies = new Map<string, GraphLayout>();
  const groups: LoopGroupLayout[] = scopes.map(({ loopId, iteration }) => {
    const loop = graph.loops![loopId];
    const bodyNodes = loop.body_node_ids.map((id) => getNode(loopId, iteration, id));
    const ids = new Set(bodyNodes.map((node) => node.node_id));
    const body = buildGraphLayout(bodyNodes, graph.edges.filter((edge) => !edge.loop_route && ids.has(edge.source) && ids.has(edge.target)), [getNode(loopId, iteration, loop.entry_node_id).node_id]);
    bodies.set(groupKey(loopId, iteration), body);
    const minX = Math.min(...body.nodes.map((node) => node.x));
    const minY = Math.min(...body.nodes.map((node) => node.y));
    return { loopId, title: loop.display_name || loopId, maxIterations: loop.max_iterations, iteration, x: 0, y: 0,
      width: Math.max(...body.nodes.map((node) => node.x + node.width)) - minX + BODY_PADDING * 2,
      height: Math.max(...body.nodes.map((node) => node.y + node.height)) - minY + HEADER_HEIGHT + BODY_PADDING };
  });
  const groupByKey = new Map(groups.map((group) => [groupKey(group.loopId, group.iteration), group]));
  const units = [...visibleNodes.filter((node) => !node.execution), ...groups.map((group) => ({ node_id: groupKey(group.loopId, group.iteration) }))];
  const unitEdges = graphEdges.filter((edge) => unitId(edge.source) !== unitId(edge.target))
    .map((edge) => ({ ...edge, source: unitId(edge.source), target: unitId(edge.target) }));
  const outer = buildGraphLayout(units, unitEdges);
  const bodyNodeWidth = Math.max(...[...bodies.values()].flatMap((body) => body.nodes.map((node) => node.width)));
  outer.nodes.forEach((node) => { node.width = bodyNodeWidth; });
  const rows = new Map<number, LayoutNode[]>();
  outer.nodes.forEach((node) => rows.set(node.y, [...(rows.get(node.y) || []), node]));
  const widthOf = (node: LayoutNode) => {
    const group = groupByKey.get(node.node.node_id);
    return group ? group.width + RETURN_LANE * 2 : node.width;
  };
  const heightOf = (node: LayoutNode) => groupByKey.get(node.node.node_id)?.height ?? node.height;
  const width = Math.max(...[...rows.values()].map((row) => row.reduce((sum, node) => sum + widthOf(node), 0) + (row.length - 1) * 36)) + PADDING * 2 + (expanded ? 192 : 0);
  const positions = new Map<string, { x: number; y: number }>();
  let y = PADDING;
  [...rows].sort(([a], [b]) => a - b).forEach(([, row]) => {
    const rowWidth = row.reduce((sum, node) => sum + widthOf(node), 0) + (row.length - 1) * 36;
    let x = (width - rowWidth) / 2;
    row.forEach((node) => { positions.set(node.node.node_id, { x, y }); x += widthOf(node) + 36; });
    y += Math.max(...row.map(heightOf)) + ROW_GAP;
  });
  const nodes: LayoutNode[] = outer.nodes.filter((node) => !groupByKey.has(node.node.node_id))
    .map((node) => ({ ...node, ...positions.get(node.node.node_id)! }));
  groups.forEach((group) => {
    Object.assign(group, positions.get(groupKey(group.loopId, group.iteration)));
    group.x += RETURN_LANE;
    const body = bodies.get(groupKey(group.loopId, group.iteration))!;
    const minX = Math.min(...body.nodes.map((node) => node.x));
    const minY = Math.min(...body.nodes.map((node) => node.y));
    body.nodes.forEach((node) => nodes.push({ ...node, x: group.x + BODY_PADDING + node.x - minX, y: group.y + HEADER_HEIGHT + node.y - minY }));
  });
  const layouts = new Map(nodes.map((node) => [node.node.node_id, node]));
  const seen = new Set<string>();
  const edges: GraphLayout['edges'] = [];
  const add = (raw: StateMachineEdge) => {
    const source = displayId(raw.source), target = displayId(raw.target);
    const key = JSON.stringify([source, raw.outcome, target, raw.loop_route]);
    if (seen.has(key)) return;
    seen.add(key);
    const meta = byId.get(raw.source)?.execution;
    const current = meta && !expanded ? graph.edges.find((edge) => edge.source === source && edge.outcome === raw.outcome
      && edge.loop_route?.kind === raw.loop_route?.kind && displayId(edge.target) === target) : raw;
    edges.push({ edge: { ...raw, source, target }, source: layouts.get(source)!, target: layouts.get(target)!,
      evidence: current || raw, ...(!current ? { stateOverride: 'pending' as const } : {}) });
  };
  graphEdges.forEach((edge) => {
    if (expanded) { add(edge); return; }
    if (edge.loop_route?.kind === 'continue') return;
    const meta = byId.get(edge.source)?.execution;
    if (!edge.loop_route && meta && byId.get(edge.target)?.execution?.loop_id === meta.loop_id
      && meta.iteration !== selected.get(meta.loop_id)) return;
    add(edge);
  });
  groups.forEach((group) => {
    const loop = graph.loops![group.loopId];
    if (expanded || loop.max_iterations <= 1) return;
    const source = getNode(group.loopId, group.iteration, loop.result_node_id), target = getNode(group.loopId, group.iteration, loop.entry_node_id);
    const evidence = graph.edges.find((edge) => edge.source === source.node_id && edge.loop_route?.kind === 'continue'
      && edge.outcome === source.outcome) || graph.edges.find((edge) => edge.source === source.node_id && edge.loop_route?.kind === 'continue');
    edges.push({ edge: { display_name: loop.continue_display_name, source: source.node_id, target: target.node_id, outcome: loop.continue_outcomes.join(' / '),
      loop_route: { kind: 'continue', logical_outcome: loop.continue_outcomes.join(' / ') } },
      source: layouts.get(source.node_id)!, target: layouts.get(target.node_id)!, evidence,
      ...(!evidence ? { stateOverride: 'skipped' as const } : {}), returnX: group.x + group.width + RETURN_LANE - 12,
      label: loop.continue_display_name ?? 'continue' });
  });
  return { nodes, edges, groups, width, height: y - ROW_GAP + PADDING };
}
