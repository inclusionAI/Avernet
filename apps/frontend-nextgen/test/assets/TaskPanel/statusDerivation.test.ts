/** @jest-environment node */
import type { NodeActionEventDto, TaskRelationDto } from '@/assets/TaskPanel/contract';
import {
  deriveNodeExecDag,
  deriveNodePresentation,
  derivePresentEdges,
} from '@/assets/TaskPanel/statusDerivation';

const ev = (seq: number, attempt: number, action = 'dispatch', patch: Partial<NodeActionEventDto> = {}): NodeActionEventDto => ({
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

  it('Review Focus #1:actionLog 缺失/attempt 缺省不崩,退化为 runtime 直映,attemptCount=0', () => {
    const r = deriveNodePresentation({ runtimeStatus: 'running', actionLog: undefined, acceptanceVerdict: null, hasConsumedDownstream: false, childTaskDone: null, needsTaskAttention: false });
    expect(r.present).toBe('running');
    expect(r.attemptCount).toBe(0);
  });

  it('rework:执行中 + attempt>1 + 最近 acceptance 非 DONE', () => {
    const r = deriveNodePresentation({
      runtimeStatus: 'running',
      actionLog: [ev(1, 1), ev(2, 2, 'rework')],
      acceptanceVerdict: 'FAILED',
      hasConsumedDownstream: false,
      childTaskDone: null,
      needsTaskAttention: true,
    });
    expect(r.present).toBe('rework');
    expect(r.attemptCount).toBe(2);
  });

  it('rework 不成立:verdict DONE(返工已通过)→ running', () => {
    const r = deriveNodePresentation({
      runtimeStatus: 'running',
      actionLog: [ev(1, 1), ev(2, 2)],
      acceptanceVerdict: 'DONE',
      hasConsumedDownstream: false,
      childTaskDone: null,
      needsTaskAttention: true,
    });
    expect(r.present).toBe('running');
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

  it('failed/hung/cancelled 直接透传(不在 PRD 五词表内,渲染层有既有词表)', () => {
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
  { src_id: 'n1', dst_id: 'n2' },
  { src_id: 'n1', dst_id: 'n3' }, // outDegree(n1)=2 → split_work
  { src_id: 'n2', dst_id: 'n4' }, // n4 attemptCount>1 → rework
  { src_id: 'n3', dst_id: 'n5' }, // 普通 → none
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
    const byKey = new Map(edges.map((e) => [`${e.from}->${e.to}`, e]));
    expect(edges).toHaveLength(4);
    expect(byKey.get('n1->n2')?.semantics).toBe('split_work');
    expect(byKey.get('n1->n3')?.semantics).toBe('split_work');
    expect(byKey.get('n2->n4')?.semantics).toBe('rework');
    expect(byKey.get('n2->n4')?.reason).toContain('revise');
    expect(byKey.get('n3->n5')?.semantics).toBe('none');
  });

  it('rework 与 split_work 同时命中时 rework 优先(返工事实 > 并行形态)', () => {
    const edges = derivePresentEdges({
      relations: [
        { src_id: 'n1', dst_id: 'n2' },
        { src_id: 'n1', dst_id: 'n3' },
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
      relations: [{ src_id: 'a', dst_id: 'b' }],
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
    expect(dag.nodes.map((n) => n.id)).toEqual(['ev-1', 'ev-2', 'ev-3']);
    expect(dag.edges).toEqual([
      { from: 'ev-1', to: 'ev-2', semantics: 'none' },
      { from: 'ev-2', to: 'ev-3', semantics: 'none' },
    ]);
  });

  it('label = action + payload.description 优先,摘要截断 20 字', () => {
    const dag = deriveNodeExecDag([ev(1, 1, 'dispatch', { payload: { description: '这是一个非常长的执行步骤描述需要被截断处理掉超过二十个字符' } })]);
    // 规则:摘要截 20 字(截断加省略号),label = action + ' ' + 摘要
    expect(dag.nodes[0].label).toContain('dispatch ');
    expect(dag.nodes[0].label.endsWith('…')).toBe(true);
    expect(dag.nodes[0].label.length).toBeLessThanOrEqual('dispatch '.length + 21);
    // 无 payload 描述时退化为 action
    const plain = deriveNodeExecDag([ev(1, 1, 'tool')]);
    expect(plain.nodes[0].label).toBe('tool');
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
    expect(dag.nodes.map((n) => n.id)).toEqual(['ev-1', 'ev-2', 'ev-3']);
    expect(deriveNodeExecDag(undefined)).toEqual({ nodes: [], edges: [] });
    expect(deriveNodeExecDag([])).toEqual({ nodes: [], edges: [] });
  });
});