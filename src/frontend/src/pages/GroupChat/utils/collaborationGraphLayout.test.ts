import type { CollaborationDefinitionGraphPreview } from '@/services/backend-api/BcnController';
import {
  buildCollaborationBindingViews,
  buildCollaborationGraphLayout,
  buildCollaborationNodePresentation,
  CollaborationGraphLayoutError,
  getCollaborationNodeInteractionState,
  getCollaborationNodeTone,
} from './collaborationGraphLayout';

const graph = (
  nodes: CollaborationDefinitionGraphPreview['nodes'],
  edges: CollaborationDefinitionGraphPreview['edges'] = [],
): CollaborationDefinitionGraphPreview => ({
  graph_mode: 'acyclic',
  nodes,
  edges,
});

const node = (
  nodeId: string,
  options: Partial<CollaborationDefinitionGraphPreview['nodes'][number]> = {},
): CollaborationDefinitionGraphPreview['nodes'][number] => ({
  node_id: nodeId,
  display_name: nodeId,
  kind: 'bot_task',
  assignee: { type: 'bot_binding', binding: 'writer' },
  final_output: false,
  judge: false,
  ...options,
});

describe('buildCollaborationGraphLayout', () => {
  it('marks a single node from initial_nodes and its own final_output field', () => {
    const layout = buildCollaborationGraphLayout(
      graph([node('answer', { final_output: true })]),
      ['answer'],
    );

    expect(layout.nodes).toHaveLength(1);
    expect(layout.nodes[0].data.isInitial).toBe(true);
    expect(layout.nodes[0].data.definition.final_output).toBe(true);
    expect(layout.edges).toEqual([]);
  });

  it('places fan-out and join nodes into deterministic ranks', () => {
    const layout = buildCollaborationGraphLayout(
      graph(
        [node('finish'), node('beta'), node('start'), node('alpha')],
        [
          { source: 'start', target: 'beta', outcome: 'complete' },
          { source: 'start', target: 'alpha', outcome: 'complete' },
          { source: 'alpha', target: 'finish', outcome: 'complete' },
          { source: 'beta', target: 'finish', outcome: 'complete' },
        ],
      ),
      ['start'],
    );
    const positions = Object.fromEntries(
      layout.nodes.map(({ id, position }) => [id, position]),
    );

    expect(positions.start.y).toBeLessThan(positions.alpha.y);
    expect(positions.alpha.y).toBe(positions.beta.y);
    expect(positions.alpha.x).toBeLessThan(positions.beta.x);
    expect(positions.finish.y).toBeGreaterThan(positions.beta.y);
  });

  it('shows judge outcomes and keeps duplicate edge ids unique', () => {
    const layout = buildCollaborationGraphLayout(
      graph(
        [node('judge', { judge: true }), node('publish')],
        [
          { source: 'judge', target: 'publish', outcome: 'approved' },
          { source: 'judge', target: 'publish', outcome: 'approved' },
        ],
      ),
      ['judge'],
    );

    expect(layout.edges.map((edge) => edge.label)).toEqual([
      'approved',
      'approved',
    ]);
    expect(new Set(layout.edges.map((edge) => edge.id)).size).toBe(2);
    expect(layout.edges.map((edge) => edge.id)).toEqual([
      'judge:approved:publish:0',
      'judge:approved:publish:1',
    ]);
  });

  it('uses display_name and falls back to the node id for the compact title', () => {
    const layout = buildCollaborationGraphLayout(
      graph([
        node('draft', { display_name: '  生成初稿  ' }),
        node('review', { display_name: '   ' }),
      ]),
      ['draft'],
    );

    expect(layout.nodes.map(({ data }) => data.title)).toEqual([
      '生成初稿',
      'review',
    ]);
  });

  it('joins the logical role and bound Bot into node presentation data', () => {
    const bindingViews = buildCollaborationBindingViews(
      [
        {
          key: 'writer',
          name: 'writer',
          displayName: '撰稿角色',
        },
      ],
      { writer: ['bot-writer'] },
      (botId) => (botId === 'bot-writer' ? '写作助手' : undefined),
    );
    const layout = buildCollaborationGraphLayout(
      graph([node('draft')]),
      ['draft'],
      bindingViews,
    );

    expect(layout.nodes[0].data).toMatchObject({
      assigneeBinding: 'writer',
      assigneeLabel: '撰稿角色',
      assigneeBotId: 'bot-writer',
      assigneeBotName: '写作助手',
    });
    expect(buildCollaborationNodePresentation(layout.nodes[0].data)).toEqual({
      botName: '写作助手',
      kindLabel: 'Bot 任务',
      roleName: '撰稿角色',
      title: 'draft',
    });
  });

  it('falls back to binding names and preserves bound state when a Bot name is unavailable', () => {
    const bindingViews = buildCollaborationBindingViews(
      [{ key: 'editor', name: 'editor' }],
      { editor: ['bot-editor'] },
      () => undefined,
    );
    const layout = buildCollaborationGraphLayout(
      graph([
        node('review', {
          assignee: { type: 'bot_binding', binding: 'editor' },
        }),
      ]),
      ['review'],
      bindingViews,
    );

    expect(layout.nodes[0].data).toMatchObject({
      assigneeLabel: 'editor',
      assigneeBotId: 'bot-editor',
      assigneeBotName: undefined,
    });
    expect(buildCollaborationNodePresentation(layout.nodes[0].data)).toEqual({
      botName: '已绑定 Bot',
      kindLabel: 'Bot 任务',
      roleName: 'editor',
      title: 'review',
    });
  });

  it('uses an explicit placeholder when assignee is missing', () => {
    const layout = buildCollaborationGraphLayout(
      graph([
        node('review', {
          kind: 'human_input',
          assignee: undefined,
        }),
      ]),
      ['review'],
    );

    expect(layout.nodes[0].data.assigneeLabel).toBe('无固定执行者');
    expect(buildCollaborationNodePresentation(layout.nodes[0].data)).toEqual({
      botName: '未分配 Bot',
      kindLabel: '人工输入',
      roleName: '无固定角色',
      title: 'review',
    });
  });

  it('shows an unbound Bot placeholder next to the participant display name', () => {
    const bindingViews = buildCollaborationBindingViews(
      [
        {
          key: 'writer',
          name: 'writer',
          displayName: '写作者',
        },
      ],
      {},
      () => undefined,
    );
    const layout = buildCollaborationGraphLayout(
      graph([node('draft', { display_name: '生成初稿' })]),
      ['draft'],
      bindingViews,
    );

    expect(buildCollaborationNodePresentation(layout.nodes[0].data)).toEqual({
      botName: '未绑定 Bot',
      kindLabel: 'Bot 任务',
      roleName: '写作者',
      title: '生成初稿',
    });
  });

  it('uses blue for Bot tasks and green for human input nodes', () => {
    expect(getCollaborationNodeTone('bot_task')).toBe('blue');
    expect(getCollaborationNodeTone('human_input')).toBe('green');
    expect(getCollaborationNodeTone('tool_action')).toBe('neutral');
  });

  it('only highlights the clicked node when its role is also activated', () => {
    expect(
      getCollaborationNodeInteractionState({
        nodeId: 'draft',
        assigneeBinding: 'writer',
        selectedNodeId: 'draft',
        highlightedBinding: 'writer',
      }),
    ).toEqual({ selected: true, highlighted: false });
    expect(
      getCollaborationNodeInteractionState({
        nodeId: 'revise',
        assigneeBinding: 'writer',
        selectedNodeId: 'draft',
        highlightedBinding: 'writer',
      }),
    ).toEqual({ selected: false, highlighted: false });
  });

  it('highlights all related nodes when a role is selected directly', () => {
    expect(
      getCollaborationNodeInteractionState({
        nodeId: 'draft',
        assigneeBinding: 'writer',
        highlightedBinding: 'writer',
      }),
    ).toEqual({ selected: false, highlighted: true });
  });

  it.each([
    {
      name: 'a dangling edge',
      value: graph(
        [node('start')],
        [{ source: 'start', target: 'missing', outcome: 'complete' }],
      ),
      initialNodes: ['start'],
    },
    {
      name: 'a cycle',
      value: graph(
        [node('one'), node('two')],
        [
          { source: 'one', target: 'two', outcome: 'complete' },
          { source: 'two', target: 'one', outcome: 'complete' },
        ],
      ),
      initialNodes: ['one'],
    },
  ])('rejects $name', ({ value, initialNodes }) => {
    expect(() => buildCollaborationGraphLayout(value, initialNodes)).toThrow(
      CollaborationGraphLayoutError,
    );
  });

  it('rejects graph modes that the preview does not support', () => {
    const value = graph([node('start')]);
    value.graph_mode = 'cyclic';

    expect(() => buildCollaborationGraphLayout(value, ['start'])).toThrow(
      '暂不支持 cyclic 模式的协作流程预览',
    );
  });

  it('lays out a representative 500-node graph without truncation', () => {
    const nodes = Array.from({ length: 500 }, (_, index) =>
      node(`node-${String(index).padStart(3, '0')}`, {
        final_output: index === 499,
      }),
    );
    const edges = nodes.slice(1).map((current, index) => ({
      source: nodes[index].node_id,
      target: current.node_id,
      outcome: 'complete',
    }));

    const layout = buildCollaborationGraphLayout(graph(nodes, edges), [
      nodes[0].node_id,
    ]);

    expect(layout.nodes).toHaveLength(500);
    expect(layout.edges).toHaveLength(499);
    expect(layout.nodes[499].position.y).toBeGreaterThan(
      layout.nodes[0].position.y,
    );
  });
});
