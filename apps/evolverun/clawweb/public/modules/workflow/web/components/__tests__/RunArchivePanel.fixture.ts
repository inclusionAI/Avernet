import type { RunArchiveData } from '@avernet/clawweb-shared/web/types'

export const ARCHIVE_FLOW_ID = 'archive-regression-flow'
export const ARCHIVE_TIMESTAMP_SECONDS = 1_795_651_200
export const ARCHIVE_TIMESTAMP_MS = ARCHIVE_TIMESTAMP_SECONDS * 1000
export const ARCHIVE_NODE_STATUSES = [
  'succeeded', 'failed', 'skipped', 'running', 'cancelled', 'pending', 'timeout', 'unknown',
] as const

/** Synthetic, fresh data shared by component regressions and the local UI preview. */
export function createArchiveFixture(overrides: Partial<RunArchiveData> = {}): RunArchiveData {
  const data: RunArchiveData = {
    archive: {
      flowId: ARCHIVE_FLOW_ID,
      archiveId: `archive-${'long-identifier-'.repeat(12)}end`,
      archiveVersion: '1',
      createdAt: new Date(ARCHIVE_TIMESTAMP_MS).toISOString(),
      status: 'completed',
      errors: [],
    },
    flowRun: {
      flow_id: ARCHIVE_FLOW_ID,
      workflow_id: 'archive-regression-workflow',
      workflow_title: '回归测试示例流程',
      status: 'failed',
      node_count: 12,
      started_at: ARCHIVE_TIMESTAMP_SECONDS,
      completed_at: ARCHIVE_TIMESTAMP_SECONDS + 2,
      duration_ms: 0,
      total_duration_ms: 0,
    },
    nodeExecutions: ARCHIVE_NODE_STATUSES.map((status, index) => ({
      id: index + 1,
      flow_id: ARCHIVE_FLOW_ID,
      node_id: `node-${status}`,
      node_title: `Example ${status} node`,
      executor_type: 'agent',
      status,
      attempt: 1,
      started_at: ARCHIVE_TIMESTAMP_SECONDS,
      completed_at: status === 'running' || status === 'pending' ? null : ARCHIVE_TIMESTAMP_SECONDS,
      duration_ms: 0,
      input_json: JSON.stringify({ request: `input-for-${status}` }),
      output_json: JSON.stringify({ result: `output-for-${status}` }),
      error_text: status === 'failed' ? 'Synthetic node failure' : null,
    })),
    flowEvents: [
      { id: 1, event_type: 'node_started', node_id: 'node-running', time: ARCHIVE_TIMESTAMP_SECONDS },
      { id: 2, event_type: 'node_failed', node_id: 'node-failed', time: ARCHIVE_TIMESTAMP_SECONDS + 1, error_text: 'Synthetic event failure' },
      { id: 3, event_type: 'node_completed', node_id: 'node-succeeded', time: ARCHIVE_TIMESTAMP_MS + 2000 },
    ],
    nodeStepTraces: [],
    executionStepLogs: [],
    runLogs: [
      { id: 1, flow_id: ARCHIVE_FLOW_ID, node_id: null, level: 'info', source: 'runtime', message: 'Synthetic startup marker', timestamp: ARCHIVE_TIMESTAMP_MS, seq: 1 },
      { id: 2, flow_id: ARCHIVE_FLOW_ID, node_id: 'node-running', level: 'warn', source: 'scheduler', message: 'Synthetic retry marker', timestamp: ARCHIVE_TIMESTAMP_MS + 1, seq: 2 },
      { id: 3, flow_id: ARCHIVE_FLOW_ID, node_id: 'node-failed', level: 'error', source: 'executor', message: 'Synthetic error marker', timestamp: ARCHIVE_TIMESTAMP_MS + 2, seq: 3 },
    ],
    langfuseTraces: [],
    langfuseObservations: [],
    failureSummary: {
      failedNodeCount: 1,
      failedNodes: [{ nodeId: 'node-failed', nodeTitle: 'Example failed node', executorType: 'agent', error: 'Synthetic node failure', attempt: 1, embeddedSessionKey: null }],
      rootCauseHints: ['node-failed: synthetic input mismatch'],
      errorTimeline: [{ timestamp: new Date(ARCHIVE_TIMESTAMP_MS).toISOString(), event: 'node-failed', detail: 'node-failed: synthetic event failure' }],
    },
    analysis: {
      source: 'reused',
      status: 'completed',
      analysisId: 'archive-regression-analysis',
      error: null,
      summary: 'Synthetic diagnosis summary',
      diagnoses: [{
        diagnosisId: 'archive-regression-diagnosis',
        flowIds: [ARCHIVE_FLOW_ID],
        nodeId: 'node-failed',
        failureSignature: 'synthetic-input-mismatch',
        failureMode: 'input-contract',
        severity: 'high',
        reasoning: 'Synthetic diagnosis reasoning',
        evidenceEventIds: ['synthetic-event-2'],
        suggestedFixSpec: 'Synthetic fix: update the input mapping',
        proposal: {
          schemaVersion: '1',
          workflowId: 'archive-regression-workflow',
          baseSpecDigest: null,
          summary: 'Synthetic patch summary',
          operations: [{ op: 'replace', path: '/nodes/node-failed/input', value: { request: 'updated' } }],
        },
      }],
    },
    suggestedYaml: {
      yamlContent: 'id: archive-regression-workflow\nnodes:\n  - id: node-failed\n    input:\n      request: updated\n',
      summary: 'Synthetic YAML suggestion',
      confidence: 'medium',
      patchProposal: {
        schemaVersion: '1',
        workflowId: 'archive-regression-workflow',
        operations: [{ op: 'replace', path: '/nodes/node-failed/input', value: { request: 'updated' } }],
      },
    },
  }
  return { ...data, ...overrides }
}
