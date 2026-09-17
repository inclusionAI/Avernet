import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { CollaborationDefinitionGraphPreview } from '@/services/backend-api/BcnController';
import { buildCollaborationLoopLayout, hasLogicalLoopDescriptors } from './collaborationLoopLayout';

function fixture(): CollaborationDefinitionGraphPreview {
  const graph = JSON.parse(readFileSync(resolve(__dirname, '../../../../../bcs/tests/fixtures/fixed_loop_logical_view.json'), 'utf8'));
  return { ...graph, graph_mode: 'hierarchical', execution_graph_mode: 'acyclic',
    nodes: graph.nodes.map((node: object) => ({ ...node, judge: 'execution' in node && (node as { execution?: { definition_node_id: string } }).execution?.definition_node_id === 'review' })) };
}

describe('Logical Loop layout', () => {
  it('shows one two-node body, one back edge, an approval exit and a fallback exit without editing execution facts', () => {
    const graph = fixture(), before = JSON.stringify(graph);
    const layout = buildCollaborationLoopLayout(graph, ['draft-1'], {});
    expect(layout.groups).toHaveLength(1);
    expect(layout.nodes.map((node) => node.id).sort()).toEqual(['draft-1', 'finalize', 'polish', 'review-1', 'rewrite']);
    expect(layout.edges).toHaveLength(6);
    const back = layout.edges.find((edge) => edge.data?.returnEdge)!;
    expect([back.source, back.target]).toEqual(['review-1', 'draft-1']);
    expect(back.label).toBe('continue');
    expect(layout.edges.find((edge) => edge.data?.loopRoute.kind === 'exhausted')).toMatchObject({ source: 'review-1', target: 'rewrite' });
    expect(layout.edges.find((edge) => edge.data?.loopRoute.kind === 'break')).toMatchObject({ source: 'review-1', target: 'polish', data: { outcome: 'approved' } });
    expect(layout.edges.filter((edge) => edge.target === 'finalize').map((edge) => edge.source).sort()).toEqual(['polish', 'rewrite']);
    expect(JSON.stringify(graph)).toBe(before);
    const group = layout.groups[0];
    const center = group.position.x + group.width / 2;
    for (const node of layout.nodes.filter((node) => node.parentId || node.id === 'finalize')) {
      expect(node.position.x + (node.parentId ? group.position.x : 0) + 105).toBe(center);
    }
    expect(back.data!.returnX).toBeLessThan(group.position.x + group.width);
    for (const node of layout.nodes.filter((node) => node.parentId)) {
      const group = layout.groups.find((group) => group.id === node.parentId)!;
      expect(node.position.x).toBeGreaterThan(0);
      expect(node.position.x + 210).toBeLessThan(group.width);
      expect(node.position.y + 84).toBeLessThan(group.height);
    }
  });

  it('previews a 100-execution limit as one logical body without execution badges', () => {
    const graph = fixture();
    const bodies = graph.nodes.filter((node) => node.execution?.iteration === 1);
    graph.nodes = graph.nodes.filter((node) => !node.execution);
    graph.edges = graph.edges.filter((edge) => ['polish', 'rewrite'].includes(edge.source));
    graph.loops!.revision_rounds.max_iterations = 100;
    for (let iteration = 1; iteration <= 100; iteration++) {
      graph.nodes.push(...bodies.map((node) => ({ ...node, node_id: `${node.execution!.definition_node_id}-${iteration}`,
        execution: { ...node.execution!, iteration, max_iterations: 100 } })));
      graph.edges.push({ source: `draft-${iteration}`, target: `review-${iteration}`, outcome: 'complete' },
        { source: `review-${iteration}`, target: 'polish', outcome: 'approved', loop_route: { kind: 'break', logical_outcome: 'approved' } },
        { source: `review-${iteration}`, target: iteration < 100 ? `draft-${iteration + 1}` : 'rewrite', outcome: 'revise',
          loop_route: { kind: iteration < 100 ? 'continue' : 'exhausted', logical_outcome: iteration < 100 ? 'revise' : 'exhausted' } });
    }
    const layout = buildCollaborationLoopLayout(graph, ['draft-1'], {});
    expect(layout.nodes).toHaveLength(5);
    expect(layout.groups).toHaveLength(1);
    expect(layout.edges.filter((edge) => edge.data?.loopRoute).map((edge) => edge.label).sort()).toEqual(['approved', 'continue', 'exhausted']);
    expect(layout.nodes.every((node) => !/第|轮|100/.test(node.data.title))).toBe(true);
  });

  it('does not draw a continuation for a single iteration', () => {
    const graph = fixture();
    graph.nodes = graph.nodes.filter((node) => !node.execution || node.execution.iteration === 1);
    graph.nodes.forEach((node) => { if (node.execution) node.execution.max_iterations = 1; });
    graph.loops!.revision_rounds.max_iterations = 1;
    graph.edges = [{ source: 'draft-1', target: 'review-1', outcome: 'complete' },
      { source: 'review-1', target: 'finalize', outcome: 'complete', loop_route: { kind: 'exhausted', logical_outcome: 'exhausted' } }];
    expect(buildCollaborationLoopLayout(graph, ['draft-1'], {}).edges.some((edge) => edge.data?.returnEdge)).toBe(false);
  });

  it('keeps legacy previews expanded and fails clearly for inconsistent descriptors', () => {
    const graph = fixture();
    delete graph.loops;
    expect(hasLogicalLoopDescriptors(graph)).toBe(false);
    expect(buildCollaborationLoopLayout(graph, ['draft-1'], {}).nodes).toHaveLength(9);
    const broken = fixture();
    broken.loops!.revision_rounds.entry_node_id = 'missing';
    expect(() => buildCollaborationLoopLayout(broken, [], {})).toThrow('缺少逻辑节点');
  });

  it('keeps independent Loop containers apart on the same outer rank', () => {
    const graph = fixture();
    const second = fixture();
    graph.loops!.other = { ...second.loops!.revision_rounds };
    graph.nodes.push(...second.nodes.filter((node) => node.execution).map((node) => ({ ...node,
      node_id: `other-${node.node_id}`, execution: { ...node.execution!, loop_id: 'other' } })));
    graph.edges.push(...second.edges.filter((edge) => edge.source.startsWith('draft-') || edge.source.startsWith('review-')).map((edge) => ({ ...edge, source: `other-${edge.source}`,
      target: ['polish', 'rewrite'].includes(edge.target) ? edge.target : `other-${edge.target}` })));
    const layout = buildCollaborationLoopLayout(graph, [], {});
    expect(layout.groups).toHaveLength(2);
    const [left, right] = layout.groups.slice().sort((a, b) => a.position.x - b.position.x);
    expect(left.position.x + left.width).toBeLessThan(right.position.x);
    expect(layout.nodes.filter((node) => node.data.logicalLoopView)).toHaveLength(4);
  });

  it('preserves parallel branches and their join inside the Loop', () => {
    const graph = fixture();
    graph.loops!.revision_rounds.body_node_ids.push('check_a', 'check_b');
    for (let iteration = 1; iteration <= 3; iteration++) {
      const draft = graph.nodes.find((node) => node.node_id === `draft-${iteration}`)!;
      for (const branch of ['check_a', 'check_b']) {
        graph.nodes.push({ ...draft, node_id: `${branch}-${iteration}`,
          execution: { ...draft.execution!, definition_node_id: branch } });
        graph.edges.push({ source: draft.node_id, target: `${branch}-${iteration}`, outcome: 'complete' },
          { source: `${branch}-${iteration}`, target: `review-${iteration}`, outcome: 'complete' });
      }
      graph.edges = graph.edges.filter((edge) => !(edge.source === draft.node_id && edge.target === `review-${iteration}`));
    }
    const layout = buildCollaborationLoopLayout(graph, ['draft-1'], {});
    const [a, b] = ['check_a-1', 'check_b-1'].map((id) => layout.nodes.find((node) => node.id === id)!);
    expect(a.position.y).toBe(b.position.y);
    expect(Math.abs(a.position.x - b.position.x)).toBeGreaterThan(210);
    expect(layout.nodes.find((node) => node.id === 'review-1')!.position.y).toBeGreaterThan(a.position.y);
    expect(layout.edges.filter((edge) => edge.target === 'review-1')).toHaveLength(2);
  });
});
