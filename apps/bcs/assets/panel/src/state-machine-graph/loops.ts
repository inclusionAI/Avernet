import type { StateMachineRunGraph, StateMachineNode, StateMachineEdge } from '../StateMachineRunView';
import { buildGraphLayout, PADDING, type GraphLayout, type LayoutNode, type LoopGroupLayout } from './layout';

const active = (node: StateMachineNode) => ['ready', 'running', 'retry_scheduled'].includes(node.status || '');
const entered = (node: StateMachineNode) => node.started_at != null || ['completed', 'failed'].includes(node.status || '') || (node.attempt || 0) > 0;
const BODY_PADDING = 24;
const RETURN_LANE = 64;
const HEADER_HEIGHT = 48;
const ROW_GAP = 64;

const executionPath = (node: StateMachineNode) => node.execution?.loop_path
  ?.map((frame) => frame.loop_id).join('/') || node.execution?.loop_id;

export function hasLoopDescriptors(graph: StateMachineRunGraph) {
  const ids = new Set(graph.nodes.flatMap((node) => node.execution ? [executionPath(node)!] : []));
  return ids.size > 0 && [...ids].every((id) => graph.loops?.[id]);
}

export function loopProgress(graph: StateMachineRunGraph, loopId: string, ancestorIterations: number[] = []) {
  const loop = graph.loops![loopId];
  const nodes = graph.nodes.filter((node) => executionPath(node) === loopId
    && ancestorIterations.every((iteration, index) => node.execution?.loop_path?.[index].iteration === iteration));
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

export function loopInstance(graph: StateMachineRunGraph, loopId: string, choices: Record<string, number>) {
  const parts = loopId.split('/');
  const ancestorIterations: number[] = [];
  for (let depth = 1; depth < parts.length; depth += 1) {
    const ancestorPath = parts.slice(0, depth).join('/');
    const ancestorKey = depth === 1 ? ancestorPath
      : `${ancestorPath}@${ancestorIterations.join('.')}`;
    const progress = loopProgress(graph, ancestorPath, ancestorIterations);
    ancestorIterations.push(progress.rounds.some((round) => round.iteration === choices[ancestorKey])
      ? choices[ancestorKey] : progress.iteration);
  }
  return {
    ancestorIterations,
    key: ancestorIterations.length ? `${loopId}@${ancestorIterations.join('.')}` : loopId,
  };
}

export function buildLoopLayout(graph: StateMachineRunGraph, choices: Record<string, number>, expanded = false): GraphLayout {
  if (!hasLoopDescriptors(graph)) return buildGraphLayout(graph.nodes, graph.edges, graph.definition.initial_nodes);
  if (Object.keys(graph.loops!).some((path) => path.includes('/'))) {
    return expanded ? buildExpandedNestedLoopLayout(graph)
      : buildNestedLoopLayout(graph, choices);
  }
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

function buildNestedLoopLayout(graph: StateMachineRunGraph, choices: Record<string, number>): GraphLayout {
  const loops = graph.loops!;
  const groupKey = (path: string) => `loop-container:${path}`;
  const selection = new Map(Object.keys(loops).map((path) => {
    const instance = loopInstance(graph, path, choices);
    const progress = loopProgress(graph, path, instance.ancestorIterations);
    const iteration = progress.rounds.some((round) => round.iteration === choices[instance.key])
      ? choices[instance.key] : progress.iteration;
    return [path, { ...instance, iteration }] as const;
  }));
  const byId = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const getNode = (path: string, definitionNodeId: string) => {
    const selected = selection.get(path)!;
    const node = graph.nodes.find((candidate) => executionPath(candidate) === path
      && candidate.execution?.definition_node_id === definitionNodeId
      && candidate.execution.iteration === selected.iteration
      && selected.ancestorIterations.every((iteration, index) =>
        candidate.execution?.loop_path?.[index].iteration === iteration));
    if (!node) throw new Error(`Loop ${path} #${selected.iteration} is missing logical node ${definitionNodeId}`);
    return node;
  };
  const canonicalId = (id: string) => {
    const node = byId.get(id);
    const path = node && executionPath(node);
    return node?.execution && path ? getNode(path, node.execution.definition_node_id).node_id : id;
  };
  const groupsByPath = new Map<string, LoopGroupLayout>();
  const relativeNodes: Array<{ layout: LayoutNode; parentPath: string }> = [];
  const descriptorPaths = Object.keys(loops).sort((left, right) =>
    right.split('/').length - left.split('/').length || left.localeCompare(right));

  descriptorPaths.forEach((path) => {
    const loop = loops[path];
    const units = loop.body_node_ids.map((id) => {
      const childPath = `${path}/${id}`;
      return loops[childPath]
        ? { ...getNode(childPath, loops[childPath].entry_node_id), node_id: groupKey(childPath), execution: undefined }
        : getNode(path, id);
    });
    const unitIds = new Set(units.map((node) => node.node_id));
    const unitId = (id: string) => {
      const canonical = canonicalId(id);
      const nodePath = executionPath(byId.get(canonical)!);
      if (nodePath === path) return canonical;
      if (nodePath?.startsWith(`${path}/`)) {
        return groupKey(`${path}/${nodePath.slice(path.length + 1).split('/')[0]}`);
      }
      return canonical;
    };
    const seen = new Set<string>();
    const unitEdges = graph.edges.flatMap((edge) => {
      if (edge.loop_route?.kind === 'continue') return [];
      const source = unitId(edge.source), target = unitId(edge.target);
      const key = JSON.stringify([source, target]);
      if (source === target || !unitIds.has(source) || !unitIds.has(target) || seen.has(key)) return [];
      seen.add(key);
      return [{ ...edge, source, target, loop_route: undefined }];
    });
    const body = buildGraphLayout(units, unitEdges, [getNode(path, loop.entry_node_id).node_id]);
    const rows = new Map<number, LayoutNode[]>();
    body.nodes.forEach((node) => rows.set(node.y, [...(rows.get(node.y) || []), node]));
    const widthOf = (node: LayoutNode) => groupsByPath.get(node.node.node_id.slice('loop-container:'.length))?.width ?? node.width;
    const heightOf = (node: LayoutNode) => groupsByPath.get(node.node.node_id.slice('loop-container:'.length))?.height ?? node.height;
    const entries = [...rows].sort(([left], [right]) => left - right);
    const rowWidth = (row: LayoutNode[]) => row.reduce((sum, node) => sum + widthOf(node), 0) + (row.length - 1) * 36;
    const contentWidth = Math.max(...entries.map(([, row]) => rowWidth(row)));
    const positions = new Map<string, { x: number; y: number }>();
    let y = HEADER_HEIGHT;
    entries.forEach(([, row]) => {
      let x = BODY_PADDING + RETURN_LANE + (contentWidth - rowWidth(row)) / 2;
      row.forEach((node) => {
        positions.set(node.node.node_id, { x, y });
        x += widthOf(node) + 36;
      });
      y += Math.max(...row.map(heightOf)) + ROW_GAP;
    });
    const selected = selection.get(path)!;
    groupsByPath.set(path, {
      loopId: path, title: loop.display_name || path, maxIterations: loop.max_iterations,
      iteration: selected.iteration, x: 0, y: 0,
      width: contentWidth + 2 * (BODY_PADDING + RETURN_LANE),
      height: y - ROW_GAP + BODY_PADDING,
    });
    body.nodes.forEach((node) => {
      const childPath = node.node.node_id.startsWith('loop-container:')
        ? node.node.node_id.slice('loop-container:'.length) : undefined;
      if (childPath) {
        Object.assign(groupsByPath.get(childPath)!, positions.get(node.node.node_id));
      } else {
        relativeNodes.push({ layout: { ...node, ...positions.get(node.node.node_id)! }, parentPath: path });
      }
    });
  });

  const rootGroups = descriptorPaths.filter((path) => !path.includes('/')).map((path) => groupsByPath.get(path)!);
  const topNodes = graph.nodes.filter((node) => !node.execution);
  const units = [...topNodes, ...rootGroups.map((group) => ({
    ...getNode(group.loopId, loops[group.loopId].entry_node_id), node_id: groupKey(group.loopId), execution: undefined,
  }))];
  const unitIds = new Set(units.map((node) => node.node_id));
  const topUnitId = (id: string) => {
    const canonical = canonicalId(id);
    const path = executionPath(byId.get(canonical)!);
    return path ? groupKey(path.split('/')[0]) : canonical;
  };
  const seenUnits = new Set<string>();
  const unitEdges = graph.edges.flatMap((edge) => {
    if (edge.loop_route?.kind === 'continue') return [];
    const source = topUnitId(edge.source), target = topUnitId(edge.target);
    const key = JSON.stringify([source, target]);
    if (source === target || !unitIds.has(source) || !unitIds.has(target) || seenUnits.has(key)) return [];
    seenUnits.add(key);
    return [{ ...edge, source, target, loop_route: undefined }];
  });
  const outer = buildGraphLayout(units, unitEdges);
  const rows = new Map<number, LayoutNode[]>();
  outer.nodes.forEach((node) => rows.set(node.y, [...(rows.get(node.y) || []), node]));
  const widthOf = (node: LayoutNode) => groupsByPath.get(node.node.node_id.slice('loop-container:'.length))?.width ?? node.width;
  const heightOf = (node: LayoutNode) => groupsByPath.get(node.node.node_id.slice('loop-container:'.length))?.height ?? node.height;
  const rowWidth = (row: LayoutNode[]) => row.reduce((sum, node) => sum + widthOf(node), 0) + (row.length - 1) * 36;
  const entries = [...rows].sort(([left], [right]) => left - right);
  const width = Math.max(...entries.map(([, row]) => rowWidth(row))) + PADDING * 2;
  const positions = new Map<string, { x: number; y: number }>();
  let y = PADDING;
  entries.forEach(([, row]) => {
    let x = (width - rowWidth(row)) / 2;
    row.forEach((node) => {
      positions.set(node.node.node_id, { x, y });
      x += widthOf(node) + 36;
    });
    y += Math.max(...row.map(heightOf)) + ROW_GAP;
  });
  rootGroups.forEach((group) => Object.assign(group, positions.get(groupKey(group.loopId))));
  const groupOrder = [...groupsByPath.entries()].sort(([left], [right]) =>
    left.split('/').length - right.split('/').length || left.localeCompare(right));
  groupOrder.forEach(([path, group]) => {
    if (!path.includes('/')) return;
    const parent = groupsByPath.get(path.slice(0, path.lastIndexOf('/')))!;
    group.x += parent.x;
    group.y += parent.y;
  });
  const nodes = outer.nodes.filter((node) => !node.node.node_id.startsWith('loop-container:'))
    .map((node) => ({ ...node, ...positions.get(node.node.node_id)! }));
  relativeNodes.forEach(({ layout, parentPath }) => {
    const parent = groupsByPath.get(parentPath)!;
    nodes.push({ ...layout, x: parent.x + layout.x, y: parent.y + layout.y });
  });

  const layouts = new Map(nodes.map((node) => [node.node.node_id, node]));
  const seen = new Set<string>();
  const edges: GraphLayout['edges'] = [];
  graph.edges.forEach((raw) => {
    if (raw.loop_route?.kind === 'continue') return;
    const source = canonicalId(raw.source), target = canonicalId(raw.target);
    if (!layouts.has(source) || !layouts.has(target)) return;
    const key = JSON.stringify([source, raw.outcome, target, raw.loop_route]);
    if (seen.has(key)) return;
    seen.add(key);
    const current = graph.edges.find((edge) => edge.source === source && edge.outcome === raw.outcome
      && edge.loop_route?.kind === raw.loop_route?.kind && canonicalId(edge.target) === target);
    edges.push({ edge: { ...raw, source, target }, source: layouts.get(source)!, target: layouts.get(target)!,
      evidence: current || raw, ...(!current ? { stateOverride: 'pending' as const } : {}) });
  });
  groupsByPath.forEach((group, path) => {
    const loop = loops[path];
    if (loop.max_iterations <= 1) return;
    const source = getNode(path, loop.result_node_id), target = getNode(path, loop.entry_node_id);
    const evidence = graph.edges.find((edge) => edge.source === source.node_id
      && edge.loop_route?.kind === 'continue' && edge.outcome === source.outcome)
      || graph.edges.find((edge) => edge.source === source.node_id && edge.loop_route?.kind === 'continue');
    edges.push({ edge: { display_name: loop.continue_display_name, source: source.node_id, target: target.node_id,
      outcome: loop.continue_outcomes.join(' / '), loop_route: { kind: 'continue', logical_outcome: loop.continue_outcomes.join(' / ') } },
      source: layouts.get(source.node_id)!, target: layouts.get(target.node_id)!, evidence,
      ...(!evidence ? { stateOverride: 'skipped' as const } : {}), returnX: group.x + group.width + RETURN_LANE - 12,
      label: loop.continue_display_name ?? 'continue' });
  });
  return { nodes, edges, groups: groupOrder.map(([, group]) => group), width, height: y - ROW_GAP + PADDING };
}

interface NestedLoopInstance {
  path: string;
  ancestorIterations: number[];
  iteration: number;
  key: string;
  parentKey?: string;
}

function buildExpandedNestedLoopLayout(graph: StateMachineRunGraph): GraphLayout {
  const loops = graph.loops!;
  const executionFrames = (node: StateMachineNode) => node.execution?.loop_path || [];
  const groupUnit = (key: string) => `loop-container:${key}`;
  const directChildren = (path: string) => Object.keys(loops).filter((candidate) =>
    candidate.startsWith(`${path}/`) && candidate.split('/').length === path.split('/').length + 1);
  const instances: NestedLoopInstance[] = [];
  const collect = (path: string, ancestorIterations: number[], parentKey?: string) => {
    const progress = loopProgress(graph, path, ancestorIterations);
    const iterations = progress.rounds.length
      ? progress.rounds.map((round) => round.iteration)
      : [progress.iteration];
    iterations.forEach((iteration) => {
      const key = `${path}@${[...ancestorIterations, iteration].join('.')}`;
      const instance = { path, ancestorIterations, iteration, key, parentKey };
      instances.push(instance);
      directChildren(path).forEach((child) => collect(child, [...ancestorIterations, iteration], key));
    });
  };
  Object.keys(loops).filter((path) => !path.includes('/')).sort()
    .forEach((path) => collect(path, []));

  const instanceByKey = new Map(instances.map((instance) => [instance.key, instance]));
  const groupByKey = new Map<string, LoopGroupLayout>();
  const relativeNodes: Array<{ layout: LayoutNode; parentKey: string }> = [];
  const nodeFor = (instance: NestedLoopInstance, definitionNodeId: string) => {
    const node = graph.nodes.find((candidate) => executionPath(candidate) === instance.path
      && candidate.execution?.definition_node_id === definitionNodeId
      && candidate.execution.iteration === instance.iteration
      && instance.ancestorIterations.every((iteration, index) =>
        executionFrames(candidate)[index]?.iteration === iteration));
    if (!node) throw new Error(`Loop ${instance.path} #${instance.iteration} is missing logical node ${definitionNodeId}`);
    return node;
  };
  const instanceForNode = (node: StateMachineNode) => {
    const path = executionPath(node);
    const frames = executionFrames(node);
    if (!path || frames.length === 0) return undefined;
    const key = `${path}@${frames.map((frame) => frame.iteration).join('.')}`;
    return instanceByKey.get(key);
  };

  [...instances].sort((left, right) =>
    right.path.split('/').length - left.path.split('/').length
      || left.key.localeCompare(right.key)).forEach((instance) => {
    const loop = loops[instance.path];
    const children = instances.filter((candidate) => candidate.parentKey === instance.key);
    const units = loop.body_node_ids.flatMap((id) => {
      const childPath = `${instance.path}/${id}`;
      const childInstances = children.filter((candidate) => candidate.path === childPath);
      return childInstances.length
        ? childInstances.map((child) => ({ ...nodeFor(child, loops[childPath].entry_node_id),
          node_id: groupUnit(child.key), execution: undefined }))
        : [nodeFor(instance, id)];
    });
    const unitIds = new Set(units.map((node) => node.node_id));
    const unitId = (id: string) => {
      const node = graph.nodes.find((candidate) => candidate.node_id === id);
      if (!node?.execution) return id;
      const targetInstance = instanceForNode(node);
      if (!targetInstance) return id;
      if (targetInstance.key === instance.key) return id;
      let direct = targetInstance;
      while (direct.parentKey && direct.parentKey !== instance.key) {
        direct = instanceByKey.get(direct.parentKey)!;
      }
      return direct.parentKey === instance.key ? groupUnit(direct.key) : id;
    };
    const seen = new Set<string>();
    const unitEdges = graph.edges.flatMap((edge) => {
      const source = unitId(edge.source), target = unitId(edge.target);
      const key = JSON.stringify([source, target]);
      if (source === target || !unitIds.has(source) || !unitIds.has(target) || seen.has(key)) return [];
      seen.add(key);
      return [{ ...edge, source, target, loop_route: undefined }];
    });
    const body = buildGraphLayout(units, unitEdges, [nodeFor(instance, loop.entry_node_id).node_id]);
    const rows = new Map<number, LayoutNode[]>();
    body.nodes.forEach((node) => rows.set(node.y, [...(rows.get(node.y) || []), node]));
    const widthOf = (node: LayoutNode) => groupByKey.get(node.node.node_id.slice('loop-container:'.length))?.width ?? node.width;
    const heightOf = (node: LayoutNode) => groupByKey.get(node.node.node_id.slice('loop-container:'.length))?.height ?? node.height;
    const entries = [...rows].sort(([left], [right]) => left - right);
    const rowWidth = (row: LayoutNode[]) => row.reduce((sum, node) => sum + widthOf(node), 0)
      + (row.length - 1) * 36;
    const contentWidth = Math.max(...entries.map(([, row]) => rowWidth(row)));
    const positions = new Map<string, { x: number; y: number }>();
    let y = HEADER_HEIGHT;
    entries.forEach(([, row]) => {
      let x = BODY_PADDING + RETURN_LANE + (contentWidth - rowWidth(row)) / 2;
      row.forEach((node) => {
        positions.set(node.node.node_id, { x, y });
        x += widthOf(node) + 36;
      });
      y += Math.max(...row.map(heightOf)) + ROW_GAP;
    });
    groupByKey.set(instance.key, {
      loopId: instance.path, instanceKey: instance.key,
      title: loop.display_name || instance.path, maxIterations: loop.max_iterations,
      iteration: instance.iteration, x: 0, y: 0,
      width: contentWidth + 2 * (BODY_PADDING + RETURN_LANE),
      height: y - ROW_GAP + BODY_PADDING,
    });
    body.nodes.forEach((node) => {
      const childKey = node.node.node_id.startsWith('loop-container:')
        ? node.node.node_id.slice('loop-container:'.length) : undefined;
      if (childKey) Object.assign(groupByKey.get(childKey)!, positions.get(node.node.node_id));
      else relativeNodes.push({ layout: { ...node, ...positions.get(node.node.node_id)! }, parentKey: instance.key });
    });
  });

  const roots = instances.filter((instance) => !instance.parentKey);
  const topNodes = graph.nodes.filter((node) => !node.execution && (active(node) || entered(node)));
  const units = [...topNodes, ...roots.map((instance) => ({
    ...nodeFor(instance, loops[instance.path].entry_node_id),
    node_id: groupUnit(instance.key), execution: undefined,
  }))];
  const unitIds = new Set(units.map((node) => node.node_id));
  const topUnitId = (id: string) => {
    const node = graph.nodes.find((candidate) => candidate.node_id === id);
    if (!node?.execution) return id;
    let instance = instanceForNode(node);
    while (instance?.parentKey) instance = instanceByKey.get(instance.parentKey);
    return instance ? groupUnit(instance.key) : id;
  };
  const seenUnits = new Set<string>();
  const unitEdges = graph.edges.flatMap((edge) => {
    const source = topUnitId(edge.source), target = topUnitId(edge.target);
    const key = JSON.stringify([source, target]);
    if (source === target || !unitIds.has(source) || !unitIds.has(target) || seenUnits.has(key)) return [];
    seenUnits.add(key);
    return [{ ...edge, source, target, loop_route: undefined }];
  });
  const outer = buildGraphLayout(units, unitEdges, (graph.definition.initial_nodes || []).map(topUnitId));
  const rows = new Map<number, LayoutNode[]>();
  outer.nodes.forEach((node) => rows.set(node.y, [...(rows.get(node.y) || []), node]));
  const widthOf = (node: LayoutNode) => groupByKey.get(node.node.node_id.slice('loop-container:'.length))?.width ?? node.width;
  const heightOf = (node: LayoutNode) => groupByKey.get(node.node.node_id.slice('loop-container:'.length))?.height ?? node.height;
  const entries = [...rows].sort(([left], [right]) => left - right);
  const rowWidth = (row: LayoutNode[]) => row.reduce((sum, node) => sum + widthOf(node), 0) + (row.length - 1) * 36;
  const width = Math.max(...entries.map(([, row]) => rowWidth(row))) + PADDING * 2 + 192;
  const positions = new Map<string, { x: number; y: number }>();
  let y = PADDING;
  entries.forEach(([, row]) => {
    let x = (width - rowWidth(row)) / 2;
    row.forEach((node) => {
      positions.set(node.node.node_id, { x, y });
      x += widthOf(node) + 36;
    });
    y += Math.max(...row.map(heightOf)) + ROW_GAP;
  });
  roots.forEach((instance) => Object.assign(groupByKey.get(instance.key)!, positions.get(groupUnit(instance.key))));
  [...instances].sort((left, right) => left.path.split('/').length - right.path.split('/').length)
    .forEach((instance) => {
      if (!instance.parentKey) return;
      const group = groupByKey.get(instance.key)!;
      const parent = groupByKey.get(instance.parentKey)!;
      group.x += parent.x;
      group.y += parent.y;
    });
  const nodes = outer.nodes.filter((node) => !node.node.node_id.startsWith('loop-container:'))
    .map((node) => ({ ...node, ...positions.get(node.node.node_id)! }));
  relativeNodes.forEach(({ layout, parentKey }) => {
    const parent = groupByKey.get(parentKey)!;
    nodes.push({ ...layout, x: parent.x + layout.x, y: parent.y + layout.y });
  });
  const layouts = new Map(nodes.map((node) => [node.node.node_id, node]));
  const edges = graph.edges.flatMap((edge) => {
    const source = layouts.get(edge.source), target = layouts.get(edge.target);
    return source && target ? [{ edge, source, target }] : [];
  });
  const groups = instances.map((instance) => groupByKey.get(instance.key)!);
  return { nodes, edges, groups, width, height: y - ROW_GAP + PADDING };
}
