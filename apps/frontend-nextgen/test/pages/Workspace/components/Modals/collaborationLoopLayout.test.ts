import { buildCollaborationLoopLayout, hasLogicalLoopDescriptors } from '@/pages/Workspace/components/Modals/collaborationLoopLayout';
import { fixture, nestedFixture } from './collaborationLoopFixtures';

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

  it('builds recursive containers from full paths without merging the child into its parent', () => {
    const graph = nestedFixture();
    expect(hasLogicalLoopDescriptors(graph)).toBe(true);
    const layout = buildCollaborationLoopLayout(graph, ['prepare-1'], {});
    expect(layout.groups.map((group) => group.loopId)).toEqual(['outer', 'outer/inner']);
    const outer = layout.groups[0], inner = layout.groups[1];
    expect(inner.parentId).toBe(outer.id);
    expect(inner.position.x).toBeGreaterThan(0);
    expect(inner.position.x + inner.width).toBeLessThan(outer.width);
    expect(layout.nodes.map((node) => node.id).sort()).toEqual([
      'assess-1', 'draft-1-1', 'prepare-1', 'publish', 'review-1-1',
    ]);
    expect(layout.nodes.find((node) => node.id === 'draft-1-1')!.parentId).toBe(inner.id);
    expect(layout.nodes.find((node) => node.id === 'prepare-1')!.parentId).toBe(outer.id);
    expect(layout.edges.filter((edge) => edge.data?.returnEdge)).toHaveLength(2);
    expect(layout.edges.some((edge) => edge.source === 'review-1-1' && edge.target === 'assess-1'
      && edge.data?.loopRoute.kind === 'exhausted')).toBe(true);
  });

  it('keeps identical parent and child Loop IDs distinct by their full descriptor paths', () => {
    const graph = nestedFixture();
    graph.loops!['outer/outer'] = graph.loops!['outer/inner'];
    delete graph.loops!['outer/inner'];
    graph.loops!.outer.body_node_ids = graph.loops!.outer.body_node_ids
      .map((id) => id === 'inner' ? 'outer' : id);
    graph.nodes.forEach((node) => {
      if (node.execution?.loop_path?.length === 2) {
        node.execution.loop_id = 'outer';
        node.execution.loop_path[1].loop_id = 'outer';
      }
    });
    const layout = buildCollaborationLoopLayout(graph, ['prepare-1'], {});
    expect(layout.groups.map((group) => group.loopId)).toEqual(['outer', 'outer/outer']);
    expect(layout.groups[1].parentId).toBe(layout.groups[0].id);
    expect(layout.nodes.find((node) => node.id === 'draft-1-1')!.parentId).toBe(layout.groups[1].id);
  });

  it('lays out a third-level HumanInput Loop inside the selected parent instances', () => {
    const graph = nestedFixture();
    graph.loops!['outer/inner'].body_node_ids = ['draft', 'approval', 'review'];
    graph.loops!['outer/inner/approval'] = {
      display_name: 'Human approval', max_iterations: 2,
      entry_node_id: 'human', result_node_id: 'human', body_node_ids: ['human'],
      continue_outcomes: ['again'], break_outcomes: ['done'], exhausted_outcome: 'timed_out',
    };
    for (let outer = 1; outer <= 2; outer++) {
      for (let inner = 1; inner <= 2; inner++) {
        const draft = `draft-${outer}-${inner}`, review = `review-${outer}-${inner}`;
        graph.edges = graph.edges.filter((edge) => !(edge.source === draft && edge.target === review));
        for (let human = 1; human <= 2; human++) {
          graph.nodes.push({
            node_id: `human-${outer}-${inner}-${human}`, display_name: 'Human approval',
            kind: 'human_input', final_output: false, judge: false,
            execution: {
              definition_node_id: 'human', loop_id: 'approval', iteration: human, max_iterations: 2,
              loop_path: [
                { loop_id: 'outer', iteration: outer, max_iterations: 2 },
                { loop_id: 'inner', iteration: inner, max_iterations: 2 },
                { loop_id: 'approval', iteration: human, max_iterations: 2 },
              ],
            },
          });
        }
        graph.edges.push(
          { source: draft, target: `human-${outer}-${inner}-1`, outcome: 'complete' },
          { source: `human-${outer}-${inner}-1`, target: `human-${outer}-${inner}-2`, outcome: 'again',
            loop_route: { kind: 'continue', logical_outcome: 'again' } },
          { source: `human-${outer}-${inner}-1`, target: review, outcome: 'done',
            loop_route: { kind: 'break', logical_outcome: 'done' } },
          { source: `human-${outer}-${inner}-2`, target: review, outcome: 'again',
            loop_route: { kind: 'exhausted', logical_outcome: 'timed_out' } },
          { source: `human-${outer}-${inner}-2`, target: review, outcome: 'done',
            loop_route: { kind: 'break', logical_outcome: 'done' } },
        );
      }
    }
    const layout = buildCollaborationLoopLayout(graph, ['prepare-1'], {});
    expect(layout.groups.map((group) => group.loopId)).toEqual([
      'outer', 'outer/inner', 'outer/inner/approval',
    ]);
    expect(layout.groups[2].parentId).toBe(layout.groups[1].id);
    const human = layout.nodes.find((node) => node.id === 'human-1-1-1')!;
    expect(human.parentId).toBe(layout.groups[2].id);
    expect(human.data.definition.kind).toBe('human_input');
    expect(layout.edges.filter((edge) => edge.data?.returnEdge)).toHaveLength(3);
  });

  it('keeps sibling child Loops separate inside one parent container', () => {
    const graph = nestedFixture();
    graph.loops!['outer/inner_b'] = { ...graph.loops!['outer/inner'], display_name: 'Inner B' };
    graph.loops!.outer.body_node_ids = ['prepare', 'inner', 'inner_b', 'assess'];
    const childIds = new Set(graph.nodes.filter((node) => node.execution?.loop_path?.length === 2)
      .map((node) => node.node_id));
    const siblingId = (id: string) => childIds.has(id) ? `sibling-${id}` : id;
    graph.nodes.push(...graph.nodes.filter((node) => childIds.has(node.node_id)).map((node) => ({
      ...node, node_id: siblingId(node.node_id),
      execution: { ...node.execution!, loop_id: 'inner_b', loop_path: node.execution!.loop_path!
        .map((frame, index) => index === 1 ? { ...frame, loop_id: 'inner_b' } : { ...frame }) },
    })));
    graph.edges.push(...graph.edges.filter((edge) => childIds.has(edge.source) || childIds.has(edge.target))
      .map((edge) => ({ ...edge, source: siblingId(edge.source), target: siblingId(edge.target) })));
    const layout = buildCollaborationLoopLayout(graph, ['prepare-1'], {});
    const outer = layout.groups.find((group) => group.loopId === 'outer')!;
    const siblings = ['outer/inner', 'outer/inner_b']
      .map((path) => layout.groups.find((group) => group.loopId === path)!);
    expect(siblings.every((group) => group.parentId === outer.id)).toBe(true);
    expect(siblings[0].position.y).toBe(siblings[1].position.y);
    const [left, right] = siblings.slice().sort((a, b) => a.position.x - b.position.x);
    expect(left.position.x + left.width).toBeLessThan(right.position.x);
  });
});
