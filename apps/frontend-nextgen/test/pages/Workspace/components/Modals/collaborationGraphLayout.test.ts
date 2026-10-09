import {
  buildCollaborationBindingViews,
  buildCollaborationGraphLayout,
} from '@/pages/Workspace/components/Modals/collaborationGraphLayout';
import type { CollaborationDefinitionGraphPreview } from '@/services/backendApi/collaboration/collaborationGraphTypes';
import { describe, expect, it } from '@jest/globals';

const graph: CollaborationDefinitionGraphPreview = {
  graph_mode: 'acyclic',
  nodes: [
    {
      node_id: 'n1',
      kind: 'bot_task',
      display_name: '问诊',
      final_output: false,
      judge: false,
      assignee: { type: 'bot_binding', binding: 'doctor' },
    },
    {
      node_id: 'n2',
      kind: 'bot_task',
      display_name: '出报告',
      final_output: true,
      judge: false,
      assignee: { type: 'bot_binding', binding: 'writer' },
    },
  ],
  edges: [{ source: 'n1', target: 'n2', outcome: 'complete' }],
};

describe('buildCollaborationGraphLayout', () => {
  it('generates nodes with positions and edges for acyclic graph', () => {
    const layout = buildCollaborationGraphLayout(graph, ['n1'], {});
    expect(layout.nodes).toHaveLength(2);
    expect(layout.edges).toHaveLength(1);
    const n1 = layout.nodes.find((n) => n.id === 'n1');
    expect(n1?.data.isInitial).toBe(true);
    expect(n1?.position.y).toBe(0);
  });

  it('throws on cycle', () => {
    const cyclic: CollaborationDefinitionGraphPreview = {
      graph_mode: 'acyclic',
      nodes: [
        { node_id: 'a', kind: 'bot_task', display_name: 'A', final_output: false, judge: false },
        { node_id: 'b', kind: 'bot_task', display_name: 'B', final_output: false, judge: false },
      ],
      edges: [
        { source: 'a', target: 'b', outcome: 'complete' },
        { source: 'b', target: 'a', outcome: 'complete' },
      ],
    };
    expect(() => buildCollaborationGraphLayout(cyclic, [], {})).toThrow('环');
  });
});

describe('buildCollaborationBindingViews', () => {
  it('maps participant definitions + bindings to role/bot info', () => {
    const views = buildCollaborationBindingViews(
      [{ key: 'doctor', required: true, displayName: '医生' }],
      { doctor: ['bot-1'] },
      (id) => (id === 'bot-1' ? 'Bot One' : undefined),
    );
    expect(views.doctor.roleName).toBe('医生');
    expect(views.doctor.botId).toBe('bot-1');
    expect(views.doctor.botName).toBe('Bot One');
  });
});
