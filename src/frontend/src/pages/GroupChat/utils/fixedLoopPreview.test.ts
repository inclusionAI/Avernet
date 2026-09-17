import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { CollaborationDefinitionValidationResponse } from '@/services/backend-api/BcnController';
import { buildCollaborationGraphLayout } from './collaborationGraphLayout';
import { canExecuteValidatedCollaboration, formatCollaborationValidationErrors } from './collaborationValidation';

const response: CollaborationDefinitionValidationResponse = JSON.parse(readFileSync(
  resolve(__dirname, '../../../../../bcs/tests/fixtures/fixed_loop_preview.json'), 'utf8',
));

describe('Fixed Loop Definition preview', () => {
  it('lays out the saved acyclic projection while retaining hierarchical authoring and opaque IDs', () => {
    const graph = response.graph!;
    const before = JSON.stringify(graph);
    const layout = buildCollaborationGraphLayout(graph, response.summary.initial_nodes);
    expect(layout.nodes.map((node) => node.id)).toEqual(graph.nodes.map((node) => node.node_id));
    expect(layout.nodes.map((node) => node.data.title)).toEqual(['Work', 'Work', 'Work', 'Publish']);
    expect(layout.nodes[0].data.isInitial).toBe(true);
    expect(layout.nodes[2].data.definition.execution).toEqual(graph.nodes[2].execution);
    layout.edges.forEach((edge) => {
      expect(layout.nodes.find((node) => node.id === edge.source)!.position.y)
        .toBeLessThan(layout.nodes.find((node) => node.id === edge.target)!.position.y);
    });
    expect(JSON.stringify(graph)).toBe(before);
  });

  it('distinguishes exhausted from break for the same target and keeps the actual outcome', () => {
    const layout = buildCollaborationGraphLayout(response.graph!, response.summary.initial_nodes);
    expect(layout.edges.map((edge) => edge.label)).toEqual([
      'continue', 'done', 'continue', 'done',
      'exhausted', 'done',
    ]);
    expect(layout.edges[4].data).toMatchObject({ outcome: 'again', loopRoute: { kind: 'exhausted', logical_outcome: 'exhausted' } });
    expect(layout.edges[5].data!.labelLane).toBeGreaterThan(layout.edges[4].data!.labelLane);
  });

  it('requires an explicit execution mode for a hierarchical preview', () => {
    const graph = { ...response.graph!, execution_graph_mode: undefined };
    expect(() => buildCollaborationGraphLayout(graph, response.summary.initial_nodes)).toThrow('暂不支持 hierarchical');
  });

  it('keeps server diagnostics, resource limits and authoring paths visible', () => {
    expect(formatCollaborationValidationErrors([{
      code: 'INVALID_DEFINITION', path: '$.runtime.state_machine.nodes.rounds.loop.max_iterations',
      message: 'must be positive and within max_fixed_loop_iterations', hint: 'Reduce max_iterations',
    }])).toBe('[INVALID_DEFINITION] $.runtime.state_machine.nodes.rounds.loop.max_iterations: must be positive and within max_fixed_loop_iterations（Reduce max_iterations）');
    expect(canExecuteValidatedCollaboration(response.warnings)).toBe(false);
    expect(canExecuteValidatedCollaboration([])).toBe(true);
    expect(canExecuteValidatedCollaboration([{ code: 'NOTICE', path: '$', message: 'advisory' }])).toBe(true);
  });
});
