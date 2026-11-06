/** @jest-environment node */
import type { NodeActionEventDto, TaskRelationDto } from '@/assets/TaskPanel/contract';
import { derivePresentEdges } from '@/assets/TaskPanel/statusDerivation';
import { describe, expect, it } from '@jest/globals';

// rework 边 reason 兜底分支补充:返工事件无 payload.description 时的三种 transition 拼装路径。

const ev = (patch: Partial<NodeActionEventDto>): NodeActionEventDto =>
  ({ seq: 1, ts: 1700000000000, action: 'dispatch', attempt: 1, ...patch }) as NodeActionEventDto;

const singleEdge = (retryEvent: NodeActionEventDto) => {
  const relations: TaskRelationDto[] = [{ src_id: 'a', dst_id: 'b', type: 'DEPENDENCY' }];
  return derivePresentEdges({
    relations,
    nodes: [
      { id: 'a', presentation: { present: 'done', attemptCount: 1, needsHitl: false }, actionLog: [] },
      { id: 'b', presentation: { present: 'rework', attemptCount: 2, needsHitl: false }, actionLog: [retryEvent] },
    ],
    outDegree: { a: 1, b: 0 },
  })[0];
};

describe('rework 边 reason 兜底(返工事件无 description)', () => {
  it('带状态迁移事件 → reason = action + from → to', () => {
    const edge = singleEdge(ev({ seq: 9, attempt: 2, action: 'rerun', status_from: 'RUNNING', status_to: 'FAILED' }));
    expect(edge.semantics).toBe('rework');
    expect(edge.reason).toBe('rerun: RUNNING → FAILED');
  });

  it('仅带 status_to → from 侧以 — 占位', () => {
    const edge = singleEdge(ev({ seq: 9, attempt: 2, action: 'rerun', status_to: 'REWORK_RERUN' }));
    expect(edge.reason).toBe('rerun: — → REWORK_RERUN');
  });

  it('无状态迁移但 payload.node_id 为字符串 → reason = action + node_id', () => {
    const edge = singleEdge(ev({ seq: 9, attempt: 2, action: 'retry', payload: { node_id: 'n-inner' } }));
    expect(edge.reason).toBe('retry: n-inner');
  });

  it('无状态迁移、payload.node_id 非字符串 → transition 为空,reason 仅保留 action', () => {
    const edge = singleEdge(ev({ seq: 9, attempt: 2, action: 'bare', payload: { node_id: 42 } }));
    expect(edge.reason).toBe('bare');
    // 完全无 payload 时同样退化为 action
    const noPayload = singleEdge(ev({ seq: 9, attempt: 2, action: 'plain' }));
    expect(noPayload.reason).toBe('plain');
  });
});
