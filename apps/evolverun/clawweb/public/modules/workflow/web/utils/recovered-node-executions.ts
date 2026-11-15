import type { FlowEvent, NodeExecution, NodeStatus, WorkflowNode } from '@avernet/clawweb-shared/web/types'

/** The current FlowEvent contract predates the db `attempt` column. */
type EventRecord = FlowEvent & { attempt?: number | null }

type EventStatus =
  | { kind: 'terminal'; status: NodeStatus }
  | { kind: 'state'; status: NodeStatus }
  | { kind: 'start' }
  | { kind: 'ready' }

const EVENT_STATUS: Record<string, EventStatus> = {
  node_succeeded: { kind: 'terminal', status: 'succeeded' },
  node_failed: { kind: 'terminal', status: 'failed' },
  node_cancelled: { kind: 'terminal', status: 'cancelled' },
  node_skipped: { kind: 'terminal', status: 'skipped' },
  node_rejected: { kind: 'terminal', status: 'blocked' },
  node_blocked: { kind: 'state', status: 'blocked' },
  node_waiting: { kind: 'state', status: 'waiting' },
  node_started: { kind: 'start' },
  node_retry: { kind: 'terminal', status: 'failed' },
  node_ready: { kind: 'ready' },
}

type EventEvidence = {
  nodeId: string
  firstAt: number
  explicitStartedAt: number | null
  readyAt: number | null
  lastStatus: NodeStatus | 'pending'
  terminalAt: number | null
  terminalStatus: NodeStatus | null
  terminalError: string | null
  executorType: string | null
  nodeTitle: string | null
  attempt: number
}

function eventTime(event: EventRecord): number {
  const value = Number(event.time)
  return Number.isFinite(value) ? value : 0
}

function parseDataJson(event: EventRecord): Record<string, unknown> {
  if (!event.data_json) return {}
  try {
    const parsed = JSON.parse(event.data_json)
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

function eventAttempt(event: EventRecord): number {
  const attempt = Number(event.attempt ?? 1)
  return Number.isFinite(attempt) && attempt > 0 ? Math.floor(attempt) : 1
}

function executionKey(nodeId: string, attempt: number): string {
  return `${nodeId}:${attempt}`
}

function updateEvidence(evidence: EventEvidence, event: EventRecord): void {
  const at = eventTime(event)
  const status = EVENT_STATUS[event.event_type]
  evidence.firstAt = Math.min(evidence.firstAt, at)
  const data = parseDataJson(event)

  if (typeof data.executor === 'string') evidence.executorType = data.executor
  if (typeof data.executor_type === 'string') evidence.executorType = data.executor_type
  if (typeof data.title === 'string') evidence.nodeTitle = data.title
  if (status?.kind === 'terminal') evidence.terminalError = event.error_text ?? null

  if (!status) return

  if (status.kind === 'start') {
    evidence.explicitStartedAt = evidence.explicitStartedAt ?? at
    evidence.lastStatus = 'running'
    return
  }

  if (status.kind === 'ready') {
    evidence.readyAt = evidence.readyAt ?? at
    evidence.lastStatus = 'pending'
    return
  }

  evidence.lastStatus = status.status
  if (status.kind === 'terminal') {
    evidence.terminalAt = at
    evidence.terminalStatus = status.status
  }
}

function syntheticNodeExecution(params: {
  flowId: string
  nodeId: string
  specNode: WorkflowNode
  evidence: EventEvidence
}): NodeExecution {
  const { evidence, specNode } = params
  const startedAt = evidence.explicitStartedAt ?? evidence.readyAt ?? evidence.firstAt
  const completedAt = evidence.terminalAt
  const durationMs = completedAt !== null ? Math.max(0, (completedAt - startedAt) * 1000) : null
  const status = evidence.terminalStatus ?? evidence.lastStatus

  return {
    flow_id: params.flowId,
    node_id: params.nodeId,
    node_title: evidence.nodeTitle ?? specNode.title ?? null,
    executor_type: evidence.executorType ?? specNode.executor.type,
    triggered_by: null,
    phase: specNode.phase ?? null,
    branch_id: specNode.branchId ?? null,
    session_key: null,
    session_id: null,
    embedded_session_key: null,
    status,
    started_at: String(startedAt),
    completed_at: completedAt === null ? null : String(completedAt),
    duration_ms: durationMs,
    input_json: null,
    output_json: null,
    error_text: evidence.terminalError,
    token_usage_json: null,
    system_context_json: null,
    progress_message: '执行记录缺失，本行状态由事件日志补齐',
    resolved_prompt: null,
    attempt: evidence.attempt,
  }
}

export function recoverNodesFromEvents(params: {
  flowId: string
  nodes: NodeExecution[]
  events: EventRecord[]
  specNodes: WorkflowNode[]
}): NodeExecution[] {
  const { flowId, nodes, events, specNodes } = params
  const persistedAttempts = new Set(nodes.map((node) => executionKey(node.node_id, node.attempt)))
  const specById = new Map(specNodes.map((node) => [node.id, node]))
  const evidenceByAttempt = new Map<string, EventEvidence>()

  for (const event of [...events].sort((a, b) => eventTime(a) - eventTime(b))) {
    const nodeId = event.node_id
    if (!nodeId || !specById.has(nodeId)) continue
    const attempt = eventAttempt(event)
    if (persistedAttempts.has(executionKey(nodeId, attempt))) continue
    if (!Object.hasOwn(EVENT_STATUS, event.event_type)) continue

    const key = executionKey(nodeId, attempt)
    const previous = evidenceByAttempt.get(key)
    const evidence = previous ?? {
      nodeId,
      firstAt: eventTime(event),
      explicitStartedAt: null,
      readyAt: null,
      lastStatus: 'pending' as const,
      terminalAt: null,
      terminalStatus: null,
      terminalError: null,
      executorType: null,
      nodeTitle: null,
      attempt,
    }
    updateEvidence(evidence, event)
    evidenceByAttempt.set(key, evidence)
  }

  // Show the latest execution attempt for each node. Earlier attempts can
  // legitimately appear as node_executions history, but a display-only
  // reconstruction must not turn an earlier failure into the state of a later
  // attempt that has already started.
  const latestEvidenceByNode = new Map<string, EventEvidence>()
  for (const evidence of evidenceByAttempt.values()) {
    const previous = latestEvidenceByNode.get(evidence.nodeId)
    if (!previous || previous.attempt < evidence.attempt) latestEvidenceByNode.set(evidence.nodeId, evidence)
  }

  // The run endpoint already exposes the latest persisted row for each node.
  // Retain at most one display row per node: replace a persisted row only when
  // event evidence proves a newer attempt that is itself missing.
  const persistedByNode = new Map(nodes.map((node) => [node.node_id, node]))
  const recovered = [...latestEvidenceByNode]
    .filter(([, evidence]) => {
      const persisted = persistedByNode.get(evidence.nodeId)
      return !persisted || persisted.attempt < evidence.attempt
    })
    .map(([nodeId, evidence]) => syntheticNodeExecution({
      flowId,
      nodeId,
      specNode: specById.get(nodeId) as WorkflowNode,
      evidence,
    }))
  const recoveredNodeIds = new Set(recovered.map((node) => node.node_id))
  const retainedNodes = nodes.filter((node) => !recoveredNodeIds.has(node.node_id))
  return [...retainedNodes, ...recovered].sort((a, b) => Number(a.started_at) - Number(b.started_at))
}
