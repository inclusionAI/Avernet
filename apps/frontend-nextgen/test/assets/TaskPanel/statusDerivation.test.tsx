/** @jest-environment node */
import type { NodeActionEventDto, TaskRelationDto } from '@/assets/TaskPanel/contract';
import { deriveNodeExecDag, deriveNodePresentation, derivePresentEdges } from '@/assets/TaskPanel/statusDerivation';

const ev = (
  seq: number,
  attempt: number,
  action = 'dispatch',
  patch: Partial<NodeActionEventDto> = {},
): NodeActionEventDto => ({
  seq,
  ts: 1700000000000 + seq * 1000,
  action,
  attempt,
  ...patch,
});

describe('deriveNodePresentation', () => {
  it('PENDING → pending;RUNNING 无返工征象 → running', () => {
    expect(
      deriveNodePresentation({ runtimeStatus: 'pending', actionLog: [], acceptanceVerdict: null, hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: false }).present,
    ).toBe('pending');
    expect(
      deriveNodePresentation({ runtimeStatus: 'running', actionLog: [ev(1, 1)], acceptanceVerdict: null, hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: false }).present,
    ).toBe('running');
  });

  it('actionLog 缺失/attempt 缺省不崩,退化为 runtime 直映,attemptCount=0', () => {
    const result = deriveNodePresentation({ runtimeStatus: 'running', actionLog: undefined, acceptanceVerdict: null, hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: false });
    expect(result.present).toBe('running');
    expect(result.attemptCount).toBe(0);
  });

  it('attempt 非正数时按第 1 次执行计算', () => {
    const invalid = deriveNodePresentation({
      runtimeStatus: 'running',
      actionLog: [ev(1, undefined as never, 'zero')],
      acceptanceVerdict: null,
      hasConsumedDownstream: false,
      childTaskDone: null,
      needsTaskAttention: false,
    });
    expect(invalid.attemptCount).toBe(1);
  });

  it('rework:执行中 + attempt>1 + 最近 acceptance 非 DONE', () => {
    const result = deriveNodePresentation({
      runtimeStatus: 'running',
      actionLog: [ev(1, 1), ev(2, 2, 'rework')],
      acceptanceVerdict: 'FAILED',
      hasConsumedDownstream: false,
      childTaskDone: null,
      needsTaskAttention: true,
    });
    expect(result.present).toBe('rework');
    expect(result.attemptCount).toBe(2);
  });

  it('rework 不成立:verdict DONE(返工已通过)→ running', () => {
    const result = deriveNodePresentation({
      runtimeStatus: 'running',
      actionLog: [ev(1, 1), ev(2, 2)],
      acceptanceVerdict: 'DONE',
      hasConsumedDownstream: false,
      childTaskDone: null,
      needsTaskAttention: true,
    });
    expect(result.present).toBe('running');
  });

  it('merged:DONE 且下游已消费,或 child_task_id 指向任务 DONE', () => {
    const input = { actionLog: [ev(1, 1)], acceptanceVerdict: 'DONE' as const, needsTaskAttention: false };
    expect(
      deriveNodePresentation({ ...input, runtimeStatus: 'done', hasConsumedDownstream: true, childTaskDone: null }).present,
    ).toBe('merged');
    expect(
      deriveNodePresentation({ ...input, runtimeStatus: 'done', hasConsumedDownstream: false, childTaskDone: true }).present,
    ).toBe('merged');
    expect(
      deriveNodePresentation({ ...input, runtimeStatus: 'done', hasConsumedDownstream: false, childTaskDone: null }).present,
    ).toBe('done');
  });

  it('failed/hung/cancelled 直接透传', () => {
    for (const status of ['failed', 'hung', 'cancelled'] as const) {
      expect(
        deriveNodePresentation({ runtimeStatus: status, actionLog: [ev(1, 1)], acceptanceVerdict: 'FAILED', hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: true }).present,
      ).toBe(status);
    }
  });

  it('attemptCount 取 action_log[].attempt 最大值;缺省 attempt 按 1 计;空日志为 0', () => {
    expect(deriveNodePresentation({ runtimeStatus: 'done', actionLog: [], acceptanceVerdict: null, hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: false }).attemptCount).toBe(0);
    expect(
      deriveNodePresentation({ runtimeStatus: 'done', actionLog: [ev(1, 3), ev(2, 1)], acceptanceVerdict: null, hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: false }).attemptCount,
    ).toBe(3);
  });

  it('needsHitl = 任务 needs_attention 且节点 running 且 acceptance verdict 非空', () => {
    const positive = deriveNodePresentation({ runtimeStatus: 'running', actionLog: [ev(1, 1)], acceptanceVerdict: 'FAILED', hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: true });
    expect(positive.needsHitl).toBe(true);
    const noAttention = deriveNodePresentation({ runtimeStatus: 'running', actionLog: [ev(1, 1)], acceptanceVerdict: 'FAILED', hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: false });
    expect(noAttention.needsHitl).toBe(false);
    const notRunning = deriveNodePresentation({ runtimeStatus: 'done', actionLog: [ev(1, 1)], acceptanceVerdict: 'FAILED', hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: true });
    expect(notRunning.needsHitl).toBe(false);
  });
});

const relations: TaskRelationDto[] = [
  { src_id: 'n1', dst_id: 'n2', type: 'DEPENDENCY' },
  { src_id: 'n1', dst_id: 'n3', type: 'DEPENDENCY' },
  { src_id: 'n2', dst_id: 'n4', type: 'DEPENDENCY' },
  { src_id: 'n3', dst_id: 'n5', type: 'DEPENDENCY' },
];

describe('derivePresentEdges', () => {
  it('relations 全 DEPENDENCY 基线 → none;fan-out → split_work;返工节点入边 → rework 带reason', () => {
    const reworkLog: NodeActionEventDto[] = [
      ev(1, 1, 'dispatch'),
      { ...ev(2, 2, 'dispatch'), status_from: 'RUNNING', status_to: 'REWORK_RERUN', payload: { description: 'Owner Bot 裁决 revise: 返工' } },
    ];
    const edges = derivePresentEdges({
      relations,
      nodes: [
        { id: 'n1', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
        { id: 'n2', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
        { id: 'n3', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
        { id: 'n4', presentation: { present: 'rework', attemptCount: 2, needsHitl: false }, actionLog: reworkLog },
        { id: 'n5', presentation: { present: 'pending', attemptCount: 0, needsHitl: false }, actionLog: [] },
      ],
      outDegree: { n1: 2, n2: 1, n3: 1, n4: 0, n5: 0 },
    });
    const byKey = new Map(edges.map((edge) => [`${edge.from}->${edge.to}`, edge]));
    expect(edges).toHaveLength(4);
    expect(byKey.get('n1->n2')?.semantics).toBe('split_work');
    expect(byKey.get('n1->n3')?.semantics).toBe('split_work');
    expect(byKey.get('n2->n4')?.semantics).toBe('rework');
    expect(byKey.get('n2->n4')?.reason).toContain('revise');
    expect(byKey.get('n3->n5')?.semantics).toBe('none');
  });

  it('rework 与 split_work 同时命中时 rework 优先', () => {
    const edges = derivePresentEdges({
      relations: [
        { src_id: 'n1', dst_id: 'n2', type: 'DEPENDENCY' },
        { src_id: 'n1', dst_id: 'n3', type: 'DEPENDENCY' },
      ],
      nodes: [
        { id: 'n1', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
        { id: 'n2', presentation: { present: 'rework', attemptCount: 2, needsHitl: false }, actionLog: [{ ...ev(9, 2, 'rework_run'), payload: { description: '第 2 次返工' } }] },
        { id: 'n3', presentation: { present: 'pending', attemptCount: 0, needsHitl: false }, actionLog: [] },
      ],
      outDegree: { n1: 2, n2: 0, n3: 0 },
    });
    expect(edges[0].semantics).toBe('rework');
    expect(edges[1].semantics).toBe('split_work');
  });

  it('outDegree=1 不产 split_work', () => {
    const edges = derivePresentEdges({
      relations: [{ src_id: 'a', dst_id: 'b', type: 'DEPENDENCY' }],
      nodes: [
        { id: 'a', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
        { id: 'b', presentation: { present: 'pending', attemptCount: 0, needsHitl: false }, actionLog: [] },
      ],
      outDegree: { a: 1, b: 0 },
    });
    expect(edges[0].semantics).toBe('none');
  });
});

describe('deriveNodeExecDag', () => {
  it('单 attempt:按 seq 排成线性链,边为 none', () => {
    const dag = deriveNodeExecDag([ev(1, 1, 'accept_context'), ev(2, 1, 'dispatch'), ev(3, 1, 'self_check')]);
    expect(dag.nodes.map((node) => node.id)).toEqual(['ev-1', 'ev-2', 'ev-3']);
    expect(dag.edges).toEqual([
      { from: 'ev-1', to: 'ev-2', semantics: 'none' },
      { from: 'ev-2', to: 'ev-3', semantics: 'none' },
    ]);
  });

  it('label = action + payload.description 优先,摘要截断 20 字', () => {
    const dag = deriveNodeExecDag([ev(1, 1, 'dispatch', { payload: { description: '这是一个非常长的执行步骤描述需要被截断处理掉超过二十个字符' } })]);
    expect(dag.nodes[0].label).toContain('dispatch ');
    expect(dag.nodes[0].label.endsWith('…')).toBe(true);
    expect(dag.nodes[0].label.length).toBeLessThanOrEqual('dispatch '.length + 21);
    const plain = deriveNodeExecDag([ev(1, 1, 'tool')]);
    expect(plain.nodes[0].label).toBe('tool');
  });

  it('短描述不截断', () => {
    const dag = deriveNodeExecDag([ev(1, 1, 'dispatch', { payload: { description: '短描述' } })]);
    expect(dag.nodes[0].label).toBe('dispatch 短描述');
  });

  it('多 attempt:attempt N+1 首节点从 attempt N 尾连 rework 边', () => {
    const dag = deriveNodeExecDag([ev(1, 1, 'a'), ev(2, 1, 'b'), ev(3, 2, 'c'), ev(4, 2, 'd')]);
    expect(dag.edges).toEqual([
      { from: 'ev-1', to: 'ev-2', semantics: 'none' },
      { from: 'ev-2', to: 'ev-3', semantics: 'rework' },
      { from: 'ev-3', to: 'ev-4', semantics: 'none' },
    ]);
  });

  it('乱序输入按 seq 稳定排序;actionLog 空 → 空图', () => {
    const dag = deriveNodeExecDag([ev(3, 1, 'c'), ev(1, 1, 'a'), ev(2, 1, 'b')]);
    expect(dag.nodes.map((node) => node.id)).toEqual(['ev-1', 'ev-2', 'ev-3']);
    expect(deriveNodeExecDag(undefined)).toEqual({ nodes: [], edges: [] });
    expect(deriveNodeExecDag([])).toEqual({ nodes: [], edges: [] });
  });
});

describe('derivePresentEdges reason fallbacks', () => {
  const link = (nodeId: string, actionLog: NodeActionEventDto[]) =>
    derivePresentEdges({
      relations: [{ src_id: 'root', dst_id: nodeId, type: 'DEPENDENCY' }],
      nodes: [
        { id: 'root', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
        { id: nodeId, presentation: { present: 'rework', attemptCount: 2, needsHitl: false }, actionLog },
      ],
      outDegree: {},
    })[0];

  it('空返工日志、空描述与状态值兜底', () => {
    expect(link('empty-log', []).reason).toBeUndefined();
    expect(link('undefined-log', undefined).reason).toBeUndefined();

    expect(link('empty-description', [{ ...ev(1, 2, 'redo'), payload: { description: '' } }]).reason).toBe('redo');
  });

  it('缺失的一侧状态使用占位符，节点 ID 可作为退化的描述', () => {
    expect(link('from-only', [{ ...ev(1, 2, 'retry'), status_from: 'RUNNING' }]).reason).toBe('retry: RUNNING → —');
    expect(link('to-only', [{ ...ev(1, 2, 'retry'), status_to: 'DONE' }]).reason).toBe('retry: — → DONE');
    expect(link('node-id', [{ ...ev(1, 2, 'retry'), payload: { node_id: 'node-9' } }]).reason).toBe('retry: node-9');
  });

  it('非返工目标且缺 outDegree 时不误产 split_work', () => {
    const edges = derivePresentEdges({
      relations: [
        { src_id: 'missing', dst_id: 'missing-to', type: 'DEPENDENCY' },
        { src_id: 'root', dst_id: 'normal', type: 'DEPENDENCY' },
      ],
      nodes: [
        { id: 'root', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
        { id: 'normal', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
      ],
      outDegree: {},
    });
    expect(edges[0]).toEqual({ from: 'missing', to: 'missing-to', semantics: 'none' });
    expect(edges[1].semantics).toBe('none');
  });
});
