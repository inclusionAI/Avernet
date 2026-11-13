import { describe, expect, it } from 'vitest'
import { recoverNodesFromEvents } from '../recovered-node-executions'
import type { FlowEvent, NodeExecution, WorkflowNode } from '@avernet/clawweb-shared/web/types'

type FlowEventInput = FlowEvent & { attempt?: number | null }

const baseNode: Omit<NodeExecution, 'node_id' | 'status' | 'started_at' | 'completed_at' | 'attempt'> = {
  flow_id: 'flow-1',
  node_title: '已存在',
  executor_type: 'cli-script',
  triggered_by: null,
  phase: 'P1',
  branch_id: null,
  session_key: null,
  session_id: null,
  embedded_session_key: null,
  duration_ms: null,
  input_json: null,
  output_json: '{}',
  error_text: null,
  token_usage_json: null,
  system_context_json: null,
  progress_message: null,
  resolved_prompt: null,
}

function event(params: Partial<FlowEventInput> & Pick<FlowEventInput, 'event_type' | 'node_id' | 'time'>): FlowEvent {
  return {
    event_id: `${params.event_type}-${params.node_id}-${params.time}`,
    flow_id: 'flow-1',
    data_json: null,
    error_text: null,
    ...params,
  }
}

const specNodes: WorkflowNode[] = [
  { id: 'missing-node', title: '缺失节点', executor: { type: 'cli-script' } },
  { id: 'persisted-node', title: '已存在节点', executor: { type: 'cli-script' } },
]

describe('recoverNodesFromEvents', () => {
  it('recovers a persisted-node gap from terminal and start events', () => {
    const recovered = recoverNodesFromEvents({
      flowId: 'flow-1',
      nodes: [{ ...baseNode, node_id: 'persisted-node', status: 'succeeded', started_at: '130', completed_at: '140', attempt: 1 }],
      events: [
        event({
          event_type: 'node_succeeded',
          node_id: 'missing-node',
          time: 120,
          data_json: JSON.stringify({ executor: 'cli-script' }),
        }),
        event({
          event_type: 'node_started',
          node_id: 'missing-node',
          time: 100,
          data_json: JSON.stringify({ executor: 'cli-script', title: '缺失节点' }),
        }),
      ],
      specNodes,
    })

    expect(recovered).toHaveLength(2)
    expect(recovered[0].node_id).toBe('missing-node')
    expect(recovered[0].status).toBe('succeeded')
    expect(recovered[0].started_at).toBe('100')
    expect(recovered[0].completed_at).toBe('120')
    expect(recovered[0].duration_ms).toBe(20_000)
    expect(recovered[0].executor_type).toBe('cli-script')
    expect(recovered[0].node_title).toBe('缺失节点')
    expect(recovered[0].progress_message).toContain('事件日志')
  })

  it('shows the latest attempt without letting an earlier failure become its status', () => {
    const recovered = recoverNodesFromEvents({
      flowId: 'flow-1',
      nodes: [],
      events: [
        event({ event_type: 'node_started', node_id: 'missing-node', attempt: 1, time: 100 }),
        event({ event_type: 'node_failed', node_id: 'missing-node', attempt: 1, time: 110, error_text: 'first failure' }),
        event({ event_type: 'node_retry', node_id: 'missing-node', attempt: 1, time: 111, error_text: 'first failure' }),
        event({ event_type: 'node_started', node_id: 'missing-node', attempt: 2, time: 120 }),
      ],
      specNodes,
    })

    expect(recovered).toHaveLength(1)
    expect(recovered[0]).toMatchObject({
      attempt: 2,
      status: 'running',
      started_at: '120',
      completed_at: null,
      duration_ms: null,
      error_text: null,
    })
  })

  it('replaces an earlier persisted attempt with one event-backed latest row', () => {
    const recovered = recoverNodesFromEvents({
      flowId: 'flow-1',
      nodes: [{ ...baseNode, node_id: 'missing-node', status: 'failed', started_at: '100', completed_at: '110', attempt: 1 }],
      events: [
        event({ event_type: 'node_started', node_id: 'missing-node', attempt: 2, time: 120 }),
        event({ event_type: 'node_succeeded', node_id: 'missing-node', attempt: 2, time: 130 }),
      ],
      specNodes,
    })

    expect(recovered).toHaveLength(1)
    expect(recovered[0]).toMatchObject({ node_id: 'missing-node', attempt: 2, status: 'succeeded' })
  })

  it('does not count business rejection as a system failure', () => {
    const recovered = recoverNodesFromEvents({
      flowId: 'flow-1',
      nodes: [],
      events: [
        event({ event_type: 'node_started', node_id: 'missing-node', attempt: 1, time: 100 }),
        event({ event_type: 'node_rejected', node_id: 'missing-node', attempt: 1, time: 120, error_text: '业务驳回' }),
      ],
      specNodes,
    })

    expect(recovered).toHaveLength(1)
    expect(recovered[0]).toMatchObject({ status: 'blocked', error_text: '业务驳回' })
  })

  it('uses readiness evidence only as an event-backed pending state', () => {
    const recovered = recoverNodesFromEvents({
      flowId: 'flow-1',
      nodes: [],
      events: [event({ event_type: 'node_ready', node_id: 'missing-node', attempt: 1, time: 100 })],
      specNodes,
    })

    expect(recovered).toHaveLength(1)
    expect(recovered[0]).toMatchObject({ status: 'pending', started_at: '100', completed_at: null })
  })

  it('does not synthesize dropped events or nodes that already have a row', () => {
    const recovered = recoverNodesFromEvents({
      flowId: 'flow-1',
      nodes: [{ ...baseNode, node_id: 'persisted-node', status: 'succeeded', started_at: '100', completed_at: '120', attempt: 1 }],
      events: [
        event({ event_type: 'node_started', node_id: 'not-in-spec', time: 100 }),
        event({
          event_type: 'node_failed',
          node_id: 'persisted-node',
          time: 140,
          error_text: 'failed',
        }),
      ],
      specNodes: [
        { id: 'persisted-node', title: '已存在节点', executor: { type: 'cli-script' } },
      ],
    })

    expect(recovered).toHaveLength(1)
    expect(recovered[0].node_id).toBe('persisted-node')
  })

  it('uses the latest state event before a terminal event', () => {
    const recovered = recoverNodesFromEvents({
      flowId: 'flow-1',
      nodes: [],
      events: [
        event({ event_type: 'node_started', node_id: 'missing-node', time: 100 }),
        event({ event_type: 'node_blocked', node_id: 'missing-node', time: 120 }),
        event({ event_type: 'node_failed', node_id: 'missing-node', time: 130, error_text: 'boom' }),
      ],
      specNodes,
    })

    expect(recovered).toHaveLength(1)
    expect(recovered[0]).toMatchObject({ status: 'failed', started_at: '100', completed_at: '130', error_text: 'boom' })
  })
})
