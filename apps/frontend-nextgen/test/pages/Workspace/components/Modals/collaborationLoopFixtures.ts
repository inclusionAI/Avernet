import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';

export function fixture(): CollaborationDefinitionGraphPreview {
  const graph = JSON.parse(readFileSync(resolve(__dirname, '../../../../../../bcs/tests/fixtures/fixed_loop_logical_view.json'), 'utf8'));
  return { ...graph, graph_mode: 'hierarchical', execution_graph_mode: 'acyclic',
    nodes: graph.nodes.map((node: object) => ({ ...node, judge: 'execution' in node && (node as { execution?: { definition_node_id: string } }).execution?.definition_node_id === 'review' })) };
}
export function nestedFixture(): CollaborationDefinitionGraphPreview {
  const graph: CollaborationDefinitionGraphPreview = {
    graph_mode: 'hierarchical', execution_graph_mode: 'acyclic',
    loops: {
      outer: { display_name: 'Outer', max_iterations: 2, entry_node_id: 'prepare', result_node_id: 'assess',
        body_node_ids: ['prepare', 'inner', 'assess'], continue_outcomes: ['repeat'], break_outcomes: ['done'], exhausted_outcome: 'capped' },
      'outer/inner': { display_name: 'Inner', max_iterations: 2, entry_node_id: 'draft', result_node_id: 'review',
        body_node_ids: ['draft', 'review'], continue_outcomes: ['revise'], break_outcomes: ['approved'], exhausted_outcome: 'exhausted' },
    },
    nodes: [], edges: [],
  };
  const add = (node_id: string, definition_node_id: string, path?: Array<[string, number, number]>, final_output = false) => {
    graph.nodes.push({ node_id, display_name: definition_node_id, kind: 'bot_task',
      final_output, judge: ['review', 'assess'].includes(definition_node_id),
      ...(path ? { execution: { definition_node_id, loop_id: path.at(-1)![0], iteration: path.at(-1)![1],
        max_iterations: path.at(-1)![2], loop_path: path.map(([loop_id, iteration, max_iterations]) => ({ loop_id, iteration, max_iterations })) } } : {}) });
  };
  for (let outer = 1; outer <= 2; outer++) {
    const outerPath: Array<[string, number, number]> = [['outer', outer, 2]];
    add(`prepare-${outer}`, 'prepare', outerPath);
    add(`assess-${outer}`, 'assess', outerPath);
    graph.edges.push({ source: `prepare-${outer}`, target: `draft-${outer}-1`, outcome: 'complete' });
    for (let inner = 1; inner <= 2; inner++) {
      const path: Array<[string, number, number]> = [...outerPath, ['inner', inner, 2]];
      add(`draft-${outer}-${inner}`, 'draft', path);
      add(`review-${outer}-${inner}`, 'review', path);
      graph.edges.push({ source: `draft-${outer}-${inner}`, target: `review-${outer}-${inner}`, outcome: 'complete' });
      graph.edges.push(inner < 2
        ? { source: `review-${outer}-${inner}`, target: `draft-${outer}-${inner + 1}`, outcome: 'revise', loop_route: { kind: 'continue', logical_outcome: 'revise' } }
        : { source: `review-${outer}-${inner}`, target: `assess-${outer}`, outcome: 'revise', loop_route: { kind: 'exhausted', logical_outcome: 'exhausted' } });
      graph.edges.push({ source: `review-${outer}-${inner}`, target: `assess-${outer}`, outcome: 'approved', loop_route: { kind: 'break', logical_outcome: 'approved' } });
    }
  }
  add('publish', 'publish', undefined, true);
  graph.edges.push({ source: 'assess-1', target: 'prepare-2', outcome: 'repeat', loop_route: { kind: 'continue', logical_outcome: 'repeat' } },
    { source: 'assess-1', target: 'publish', outcome: 'done', loop_route: { kind: 'break', logical_outcome: 'done' } },
    { source: 'assess-2', target: 'publish', outcome: 'repeat', loop_route: { kind: 'exhausted', logical_outcome: 'capped' } },
    { source: 'assess-2', target: 'publish', outcome: 'done', loop_route: { kind: 'break', logical_outcome: 'done' } });
  return graph;
}
