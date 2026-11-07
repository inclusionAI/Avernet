import { collapseCollaborationLoops } from '@/pages/Workspace/components/Modals/collapseCollaborationLoops';
import { buildCollaborationLoopLayout } from '@/pages/Workspace/components/Modals/collaborationLoopLayout';
import { fixture, nestedFixture } from './collaborationLoopFixtures';

it('preserves separate break/exhausted outcomes and external connections when collapsing a loop', () => {
  const layout = buildCollaborationLoopLayout(fixture(), ['draft-1'], {});
  const before = JSON.stringify(layout);
  const group = layout.groups[0];
  const folded = collapseCollaborationLoops(layout, new Set([group.id]));
  expect(folded.nodes.every((node) => !node.parentId)).toBe(true);
  expect(folded.groups[0].height).toBeLessThan(group.height);
  const exits = folded.edges.filter((edge) => edge.source === group.id);
  expect(exits.map((edge) => edge.data?.loopRoute.kind).sort()).toEqual(['break', 'exhausted']);
  expect(exits.map((edge) => edge.outcome).sort()).toEqual(['approved', 'revise']);
  expect(exits.every((edge) => edge.sourceHandle === 'main')).toBe(true);
  expect(folded.edges.some((edge) => edge.source === edge.target)).toBe(false);
  expect(JSON.stringify(layout)).toBe(before);
});

it('reflows the parent and reconnects both ends of a nested collapsed body', () => {
  const layout = buildCollaborationLoopLayout(nestedFixture(), ['prepare-1'], {});
  const folded = collapseCollaborationLoops(layout, new Set(['loop-container:outer/inner']));
  expect(folded.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(['prepare-1', 'assess-1', 'publish']));
  expect(folded.nodes.find((node) => node.id === 'draft-1-1')).toBeUndefined();
  expect(folded.edges).toEqual(expect.arrayContaining([
    expect.objectContaining({ source: 'prepare-1', target: 'loop-container:outer/inner', targetHandle: 'main' }),
    expect.objectContaining({ source: 'loop-container:outer/inner', target: 'assess-1', outcome: 'approved', sourceHandle: 'main' }),
    expect.objectContaining({ source: 'loop-container:outer/inner', target: 'assess-1', outcome: 'revise', sourceHandle: 'main' }),
  ]));
  const outer = folded.groups.find((group) => group.loopId === 'outer')!;
  expect(outer.height).toBeLessThan(layout.groups.find((group) => group.loopId === 'outer')!.height);
  const returnEdge = folded.edges.find((edge) => edge.id === 'loop-container:outer:return')!;
  expect(returnEdge.data?.returnX).toBe(outer.position.x + outer.width - 12);
  const ids = new Set([...folded.nodes, ...folded.groups].map((item) => item.id));
  expect(folded.edges.every((edge) => ids.has(edge.source) && ids.has(edge.target))).toBe(true);
});

it('keeps ancestor collapse authoritative and does not change an expanded layout', () => {
  const layout = buildCollaborationLoopLayout(nestedFixture(), ['prepare-1'], {});
  expect(collapseCollaborationLoops(layout, new Set())).toBe(layout);
  const folded = collapseCollaborationLoops(layout, new Set(['loop-container:outer', 'loop-container:outer/inner']));
  expect(folded.groups.map((group) => group.loopId)).toEqual(['outer']);
  expect(folded.nodes.map((node) => node.id)).toEqual(['publish']);
  expect(folded.edges).toHaveLength(2);
});
