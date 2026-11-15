import type { CollaborationGraphLayout, CollaborationGraphLayoutEdge } from './collaborationGraphLayout';
import { COLLABORATION_FLOW_NODE_HEIGHT, COLLABORATION_FLOW_NODE_WIDTH } from './collaborationGraphLayout';
import type { LoopContainerLayout } from './collaborationLoopLayout';
import { LOOP_RETURN_LANE } from './collaborationLoopLayout';

type Layout = CollaborationGraphLayout & { groups: LoopContainerLayout[] };

/** 折叠仅改变预览布局；跨循环边保留 outcome、出口类型及展示名称。 */
export function collapseCollaborationLoops(layout: Layout, collapsed: ReadonlySet<string>): Layout {
  if (!layout.groups.some((group) => collapsed.has(group.id))) return layout;
  const groups = layout.groups.map((group) => ({ ...group, position: { ...group.position } }));
  const nodes = layout.nodes.map((node) => ({ ...node, position: { ...node.position } }));
  const groupById = new Map(groups.map((group) => [group.id, group]));
  const units = [...groups, ...nodes];
  const byId = new Map(units.map((unit) => [unit.id, unit]));
  const visibleId = (id: string) => {
    let visible = id;
    let parentId = byId.get(id)?.parentId;
    while (parentId) {
      if (collapsed.has(parentId)) visible = parentId;
      parentId = groupById.get(parentId)?.parentId;
    }
    return visible;
  };
  const visibleGroups = groups.filter((group) => visibleId(group.id) === group.id);
  const visibleNodes = nodes.filter((node) => visibleId(node.id) === node.id);
  const arrange = (parentId?: string) => {
    const children = units.filter((unit) => unit.parentId === parentId && visibleId(unit.id) === unit.id);
    const rows = new Map<number, typeof children>();
    children.forEach((child) => rows.set(child.position.y, [...(rows.get(child.position.y) ?? []), child]));
    children.forEach((child) => {
      const group = groupById.get(child.id);
      if (!group) return;
      if (collapsed.has(group.id)) {
        group.width = 320;
        group.height = 104;
      } else arrange(group.id);
    });
    const widthOf = (id: string) => groupById.get(id)?.width ?? COLLABORATION_FLOW_NODE_WIDTH;
    const heightOf = (id: string) => groupById.get(id)?.height ?? COLLABORATION_FLOW_NODE_HEIGHT;
    const rowWidth = (row: typeof children) => row.reduce((sum, child) => sum + widthOf(child.id), 0) + Math.max(0, row.length - 1) * 56;
    const contentWidth = Math.max(0, ...[...rows.values()].map(rowWidth));
    const padding = 24 + LOOP_RETURN_LANE;
    let y = parentId ? 48 : 0;
    [...rows].sort(([a], [b]) => a - b).forEach(([, row]) => {
      row.sort((a, b) => a.position.x - b.position.x);
      let x = parentId ? padding + (contentWidth - rowWidth(row)) / 2 : -rowWidth(row) / 2;
      row.forEach((child) => {
        child.position = { x, y };
        x += widthOf(child.id) + 56;
      });
      y += Math.max(...row.map((child) => heightOf(child.id))) + 56;
    });
    if (parentId) {
      const group = groupById.get(parentId)!;
      group.width = contentWidth + padding * 2;
      group.height = y - 56 + 24;
    }
  };
  arrange();
  const absoluteX = (group: LoopContainerLayout): number => group.position.x
    + (group.parentId ? absoluteX(groupById.get(group.parentId)!) : 0);
  const seen = new Set<string>();
  const lanes = new Map<string, number>();
  const edges: CollaborationGraphLayoutEdge[] = layout.edges.flatMap((edge) => {
    const source = visibleId(edge.source), target = visibleId(edge.target);
    if (source === target && (source !== edge.source || target !== edge.target)) return [];
    const key = JSON.stringify([source, target, edge.outcome, edge.label, edge.data?.loopRoute.kind]);
    if (seen.has(key)) return [];
    seen.add(key);
    const pair = JSON.stringify([source, target]);
    const labelLane = lanes.get(pair) ?? 0;
    lanes.set(pair, labelLane + 1);
    const returnGroup = edge.data?.returnEdge ? groupById.get(edge.id.slice(0, -':return'.length)) : undefined;
    return [{ ...edge, source, target,
      sourceHandle: source !== edge.source && !edge.data?.returnEdge ? 'main' : edge.sourceHandle,
      targetHandle: target !== edge.target && !edge.data?.returnEdge ? 'main' : edge.targetHandle,
      ...(edge.data ? { data: { ...edge.data, labelLane,
        ...(returnGroup ? { returnX: absoluteX(returnGroup) + returnGroup.width - 12 } : {}) } } : {}),
    }];
  });
  return { nodes: visibleNodes, groups: visibleGroups, edges };
}
