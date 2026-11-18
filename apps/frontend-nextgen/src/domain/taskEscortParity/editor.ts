import { dump } from 'js-yaml';
import type { EditorNode } from './editorTypes';

export function editorYaml(value: unknown): string {
  return dump(value, { noRefs: true, lineWidth: -1, sortKeys: false });
}
export function editorSnapshotText(text: string): string {
  try {
    const value: unknown = JSON.parse(text);
    if (
      value &&
      typeof value === 'object' &&
      'content' in value &&
      typeof value.content === 'string' &&
      !('nodes' in value)
    )
      return value.content;
    return JSON.stringify(value, null, 2);
  } catch {
    return text;
  }
}

/** Small deterministic dependency layout; coordinates are presentation only, never persisted. */
export function buildEditorGraph(nodes: EditorNode[]) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const ranks = new Map<string, number>();
  const visiting = new Set<string>();
  const warnings = new Set<string>();
  const edges: Array<{ source: string; target: string }> = [];
  for (const node of nodes)
    for (const source of new Set(node.dependsOn ?? [])) {
      if (byId.has(source)) edges.push({ source, target: node.id });
      else warnings.add(`节点 ${node.id} 的依赖 ${source} 不存在，未绘制该连线。`);
    }
  // Iterative topological layout avoids recursion limits on large or cyclic specs.
  const pending = new Map(
    nodes.map((node) => [node.id, new Set((node.dependsOn ?? []).filter((id) => byId.has(id))).size]),
  );
  const outgoing = new Map<string, string[]>();
  for (const edge of edges) outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
  const queue = nodes.filter((node) => pending.get(node.id) === 0).map((node) => node.id);
  for (let index = 0; index < queue.length; index += 1) {
    const id = queue[index];
    visiting.add(id);
    for (const target of outgoing.get(id) ?? []) {
      ranks.set(target, Math.max(ranks.get(target) ?? 0, (ranks.get(id) ?? 0) + 1));
      pending.set(target, pending.get(target)! - 1);
      if (pending.get(target) === 0) queue.push(target);
    }
  }
  if (visiting.size < nodes.length) warnings.add('规格包含循环依赖；环内节点并列展示，未修改原规格。');
  const rows = new Map<number, number>();
  const positioned = nodes.map((node) => {
    const rank = ranks.get(node.id) ?? 0;
    const row = rows.get(rank) ?? 0;
    rows.set(rank, row + 1);
    return { id: node.id, x: 24 + rank * 272, y: 24 + row * 128 };
  });
  return { nodes: positioned, edges, warnings: [...warnings] };
}

/** Exact contiguous text change, intentionally not a semantic or minimal patch. */
export function editorTextDiff(from: string, to: string): Array<{ kind: 'removed' | 'added'; text: string }> {
  if (from === to) return [];
  const a = from.split('\n'),
    b = to.split('\n');
  let start = 0,
    endA = a.length,
    endB = b.length;
  while (start < endA && start < endB && a[start] === b[start]) start += 1;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  return [
    ...a.slice(start, endA).map((text) => ({ kind: 'removed' as const, text })),
    ...b.slice(start, endB).map((text) => ({ kind: 'added' as const, text })),
  ];
}
