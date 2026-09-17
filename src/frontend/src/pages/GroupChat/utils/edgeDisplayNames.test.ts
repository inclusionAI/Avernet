import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { CollaborationDefinitionGraphPreview } from '@/services/backend-api/BcnController';
import { buildCollaborationGraphLayout } from './collaborationGraphLayout';
import { buildCollaborationLoopLayout } from './collaborationLoopLayout';

function fixture(): CollaborationDefinitionGraphPreview {
  const graph = JSON.parse(readFileSync(resolve(__dirname, '../../../../../bcs/tests/fixtures/fixed_loop_logical_view.json'), 'utf8'));
  return { ...graph, graph_mode: 'hierarchical', execution_graph_mode: 'acyclic' };
}

it('uses explicit names for ordinary edges, logical returns and real exhausted edges', () => {
  const graph = fixture();
  graph.loops!.revision_rounds.continue_display_name = '根据意见修订';
  for (const edge of graph.edges) {
    edge.display_name = edge.loop_route
      ? { continue: '根据意见修订', break: '评审通过', exhausted: '转入重写' }[edge.loop_route.kind]
      : '提交评审';
  }
  const before = JSON.stringify(graph);
  for (const layout of [buildCollaborationGraphLayout(graph, ['draft-1'], {}), buildCollaborationLoopLayout(graph, ['draft-1'], {})]) {
    for (const edge of layout.edges) {
      expect(edge.label).toBe(edge.data?.loopRoute
        ? { continue: '根据意见修订', break: '评审通过', exhausted: '转入重写' }[edge.data.loopRoute.kind]
        : '提交评审');
    }
    expect(layout.edges.filter(edge => edge.data?.loopRoute.kind === 'exhausted').map(edge => edge.data!.outcome)).toEqual(['revise']);
  }
  expect(JSON.stringify(graph)).toBe(before);
});

it('shows an explicit complete label even when an ordinary edge would otherwise be unlabelled', () => {
  const graph = fixture();
  delete graph.loops;
  graph.nodes = graph.nodes.filter(node => !node.execution);
  graph.edges = graph.edges.filter(edge => ['polish', 'rewrite'].includes(edge.source));
  expect(buildCollaborationGraphLayout(graph, [], {}).edges.every(edge => edge.label === undefined)).toBe(true);
  graph.edges[0].display_name = '提交汇总';
  expect(buildCollaborationGraphLayout(graph, [], {}).edges[0].label).toBe('提交汇总');
});
