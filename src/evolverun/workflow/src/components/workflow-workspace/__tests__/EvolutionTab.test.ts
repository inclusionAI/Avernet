import { describe, expect, it } from 'vitest'

import type { EvolveRunDiagnosis } from '../../../api/client'
import { aggregateDiagnoses, diffWorkflowPatchOperations } from '../evolution-utils'

function diagnosis(overrides: Partial<EvolveRunDiagnosis>): EvolveRunDiagnosis {
  return {
    id: 1,
    diagnosis_id: 'diagnosis-1',
    flow_id: 'run-1',
    workflow_id: 'workflow-1',
    run_id: null,
    node_id: 'fetch-data',
    failure_signature: 'timeout:fetch-data',
    failure_mode: 'timeout',
    executor_type: null,
    weak_node_id: null,
    suggested_fix_kind: null,
    lesson_id_hit: null,
    error_text: 'request timed out',
    created_by: 'owner-1',
    gmt_create: 100,
    gmt_modified: 100,
    ...overrides,
  }
}

describe('aggregateDiagnoses', () => {
  it('groups repeated diagnoses across runs by signature and keeps latest evidence', () => {
    const clusters = aggregateDiagnoses([
      diagnosis({ diagnosis_id: 'd1', flow_id: 'run-1', gmt_create: 100 }),
      diagnosis({ diagnosis_id: 'd2', flow_id: 'run-2', gmt_create: 300, error_text: 'latest timeout' }),
      diagnosis({ diagnosis_id: 'd3', flow_id: 'run-2', gmt_create: 200 }),
      diagnosis({ diagnosis_id: 'd4', flow_id: 'run-3', failure_signature: 'bad-output:parse', failure_mode: 'bad_output', gmt_create: 250 }),
    ])

    expect(clusters).toHaveLength(2)
    expect(clusters[0]).toMatchObject({ signature: 'timeout:fetch-data', runIds: ['run-2', 'run-1'] })
    expect(clusters[0].diagnoses).toHaveLength(3)
    expect(clusters[0].latest.error_text).toBe('latest timeout')
    expect(clusters[0].instances).toEqual(expect.arrayContaining([
      expect.objectContaining({ analysisId: 'legacy', diagnosisId: 'd1', flowId: 'run-1' }),
      expect.objectContaining({ analysisId: 'legacy', diagnosisId: 'd2', flowId: 'run-2' }),
    ]))
  })

  it('can limit aggregation to one source run', () => {
    const clusters = aggregateDiagnoses([
      diagnosis({ flow_id: 'run-1' }),
      diagnosis({ flow_id: 'run-2' }),
    ], 'run-2')

    expect(clusters).toHaveLength(1)
    expect(clusters[0].runIds).toEqual(['run-2'])
    expect(clusters[0].diagnoses).toHaveLength(1)
  })

  it('does not merge the same signature across workflows and preserves analysis identity', () => {
    const clusters = aggregateDiagnoses([
      diagnosis({ workflow_id: 'workflow-1', analysis_id: 'AN-1', diagnosis_id: 'd1' }),
      diagnosis({ workflow_id: 'workflow-2', analysis_id: 'AN-2', diagnosis_id: 'd2' }),
    ])

    expect(clusters).toHaveLength(2)
    expect(clusters.map((cluster) => cluster.workflowId).sort()).toEqual(['workflow-1', 'workflow-2'])
    expect(clusters.map((cluster) => cluster.instances[0].analysisId).sort()).toEqual(['AN-1', 'AN-2'])
  })
})

describe('diffWorkflowPatchOperations', () => {
  it('shows added, changed and removed repair operations', () => {
    const previous = { operations: [
      { op: 'replace', nodeId: 'report', path: '/outputContract/type', value: 'string' },
      { op: 'add', nodeId: 'search', path: '/retry/maxAttempts', value: 2 },
    ] }
    const current = { operations: [
      { op: 'replace', nodeId: 'report', path: '/outputContract/type', value: 'object' },
      { op: 'add', nodeId: 'report', path: '/outputContract/properties', value: { output: { type: 'object' } } },
    ] }

    expect(diffWorkflowPatchOperations(previous, current)).toMatchObject({
      added: [expect.objectContaining({ nodeId: 'report', path: '/outputContract/properties' })],
      changed: [expect.objectContaining({ nodeId: 'report', path: '/outputContract/type' })],
      removed: [expect.objectContaining({ nodeId: 'search', path: '/retry/maxAttempts' })],
    })
  })
})
