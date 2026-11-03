import type { StateMachineNode } from '../../types';

/** Edge tuple as returned by the state-machine graph endpoint. */
export interface RunnerEdge { source: string; target: string; outcome?: string }

/**
 * Layered left-to-right layout of the live run graph, using the same
 * `(source, target)` edges returned by the state-machine graph endpoint.
 * Kahn layering keeps the workflow semantics visible:
 *  - a speech run is a transitive-closure serial chain (串行发言);
 *  - a vote run fans out `vote_N` in parallel into the referee's `tally` (计票).
 *
 * When the endpoint omits edges we still produce a deterministic diagram by
 * chaining nodes in `completed_at` then id order. Referee-like nodes
 * (collect / tally / judge / host) are always pushed to the final column so
 * counting reads as the 末节点.
 */

export type RunnerNodeKind = 'bot' | 'human' | 'host' | 'entry' | 'unknown';

export interface RunnerNode {
  id: string;
  label: string;
  actorId?: string;
  kind: RunnerNodeKind;
  status: string;
  attempt: number;
  layer: number;
  indexInLayer: number;
}

export interface RunnerColumn {
  index: number;
  nodes: RunnerNode[];
}

export interface RunnerGraphLayout {
  columns: RunnerColumn[];
  edges: Array<{ source: RunnerNode; target: RunnerNode }>;
  nodeById: Map<string, RunnerNode>;
}

const ENTRY_HINTS = ['vote_start', 'speak_open', 'open'];
const HOST_HINTS = ['collect', 'tally', 'judge', 'host', 'final'];

interface PlayerRef { actorId: string; seatNumber: number; displayName: string; isHuman?: boolean }

function labelFor(id: string, actorId: string | undefined, players: PlayerRef[]): string {
  if (!actorId) return id;
  const player = players.find((p) => p.actorId === actorId);
  return player ? `${player.seatNumber}号 ${player.displayName}` : actorId;
}

function kindFor(id: string, actorId: string | undefined, hostActorId: string | undefined, players: PlayerRef[]): RunnerNodeKind {
  if (actorId && actorId === hostActorId) return 'host';
  if (actorId) {
    const player = players.find((p) => p.actorId === actorId);
    return player?.isHuman ? 'human' : 'bot';
  }
  if (ENTRY_HINTS.some((hint) => id.includes(hint))) return 'entry';
  if (HOST_HINTS.some((hint) => id.includes(hint))) return 'host';
  return 'unknown';
}

export function buildRunnerGraph(
  nodes: StateMachineNode[],
  edges: RunnerEdge[] | undefined,
  nodeActorMap: Record<string, string>,
  hostActorId: string | undefined,
  players: PlayerRef[],
): RunnerGraphLayout {
  const mapped = nodes.filter((node) => Boolean(node?.node_id));

  // 1. Resolve edges (explicit, or deterministic fallback chain).
  const idSet = new Set(mapped.map((node) => node!.node_id));
  let edgePairs: Array<{ source: string; target: string }> = [];
  const explicit = (edges ?? []).filter((e) => idSet.has(e.source) && idSet.has(e.target) && e.source !== e.target);
  if (explicit.length) {
    edgePairs = explicit.map((e) => ({ source: e.source, target: e.target }));
  } else {
    const ordered = [...mapped].sort((a, b) =>
      (a!.completed_at ?? a!.started_at ?? Infinity) - (b!.completed_at ?? b!.started_at ?? Infinity) ||
      a!.node_id.localeCompare(b!.node_id));
    for (let i = 0; i < ordered.length - 1; i++) {
      edgePairs.push({ source: ordered[i]!.node_id, target: ordered[i + 1]!.node_id });
    }
  }

  // 2. Kahn layering over the pair set.
  const indegree = new Map<string, number>();
  for (const node of mapped) indegree.set(node!.node_id, 0);
  for (const edge of edgePairs) indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  const byLayer = new Map<string, number>();
  const queue: string[] = [];
  for (const [id, degree] of indegree) if (degree === 0) { byLayer.set(id, 0); queue.push(id); }
  let head = 0;
  while (head < queue.length) {
    const current = queue[head++];
    for (const edge of edgePairs) {
      if (edge.source !== current) continue;
      const nextLayer = (byLayer.get(current) ?? 0) + 1;
      if (!byLayer.has(edge.target) || byLayer.get(edge.target)! < nextLayer) {
        byLayer.set(edge.target, nextLayer);
        queue.push(edge.target);
      }
    }
  }
  let deepest = 0;
  for (const layer of byLayer.values()) deepest = Math.max(deepest, layer);

  // 3. Materialize nodes into layers.
  const baseLayer = new Map<string, number>();
  for (const node of mapped) baseLayer.set(node!.node_id, byLayer.get(node!.node_id) ?? (++deepest));

  const hasOutgoing = new Set(edgePairs.map((e) => e.source));
  const runnerNodes: RunnerNode[] = mapped.map((node) => {
    const id = node!.node_id;
    return {
      id,
      label: labelFor(id, nodeActorMap[id], players),
      actorId: nodeActorMap[id],
      kind: kindFor(id, nodeActorMap[id] ?? node!.assignee_bot_id, hostActorId, players),
      status: node!.status ?? 'pending',
      attempt: typeof node!.attempt === 'number' ? node!.attempt : 1,
      layer: baseLayer.get(id) ?? 0,
      indexInLayer: 0,
    };
  });

  // Referee-like exit nodes (计票/汇总) land in the final column.
  const maxLayer = runnerNodes.reduce((max, n) => Math.max(max, n.layer), 0);
  for (const node of runnerNodes) {
    if (HOST_HINTS.some((hint) => node.id.includes(hint)) && node.layer < maxLayer) node.layer = maxLayer;
    // true leaf nodes (no outgoing) that aren't referee also belong to last column,
    // but only when they're already deeper; keep them stable otherwise.
    if (!hasOutgoing.has(node.id) && node.layer < maxLayer && node.kind === 'unknown') node.layer = maxLayer;
  }

  // 4. Group into columns.
  const columnMap = new Map<number, RunnerNode[]>();
  for (const node of runnerNodes) {
    if (!columnMap.has(node.layer)) columnMap.set(node.layer, []);
    columnMap.get(node.layer)!.push(node);
  }
  for (const list of columnMap.values()) list.sort((a, b) => a.id.localeCompare(b.id));
  const columns: RunnerColumn[] = [];
  let columnIndex = 0;
  for (const [layer, list] of [...columnMap.entries()].sort((a, b) => a[0] - b[0])) {
    columns.push({ index: columnIndex++, nodes: list.map((node, i) => ({ ...node, indexInLayer: i, layer })) });
  }

  const finalById = new Map<string, RunnerNode>();
  for (const col of columns) for (const node of col.nodes) finalById.set(node.id, node);
  const layoutEdges = edgePairs
    .map((e) => ({ source: finalById.get(e.source)!, target: finalById.get(e.target)! }))
    .filter((e) => Boolean(e.source) && Boolean(e.target));

  return { columns, edges: layoutEdges, nodeById: finalById };
}