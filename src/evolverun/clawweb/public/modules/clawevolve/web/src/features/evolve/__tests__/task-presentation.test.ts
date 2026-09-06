import { describe, expect, it } from 'vitest'
import type { EvolveTask } from '../../../api/client'
import {
  governanceImprovementId,
  isGovernanceTask,
  taskDisplayType,
} from '../task-presentation'

function task(overrides: Partial<EvolveTask> = {}): EvolveTask {
  return {
    task_id: 'EV-DEMO', task_type: 'full', task_name: null, remark: null,
    user_id: '2088', bot_id: 'bot-1', status: 'running', config: {},
    error_message: null, created_by: '2088', gmt_create: 1, gmt_modified: 1,
    ...overrides,
  }
}

describe('Evolve task presentation', () => {
  it('presents an Insight improvement task as governance optimization', () => {
    const value = task({
      config: { input: { type: 'insight_improvement', improvementId: 35 } },
      source: {
        sourceType: 'insight_improvement', sourceId: 'improvement:35',
        schemaVersion: 'plan-source/v2', adapterVersion: 'insight-to-plan-source/v2',
        status: 'ready', digest: 'sha256:demo', evidenceCount: 3, error: null, resolvedAt: 1,
      },
    })

    expect(isGovernanceTask(value)).toBe(true)
    expect(taskDisplayType(value)).toEqual({ key: 'governance', label: '治理优化' })
    expect(governanceImprovementId(value)).toBe(35)
  })

  it('falls back to the frozen source id when old config has no improvement id', () => {
    const value = task({
      source: {
        sourceType: 'insight_improvement', sourceId: 'improvement:34',
        schemaVersion: 'plan-source/v2', adapterVersion: null, status: 'pending',
        digest: null, evidenceCount: 1, error: null, resolvedAt: null,
      },
    })
    expect(governanceImprovementId(value)).toBe(34)
  })

  it('keeps the original label for a normal full task', () => {
    const value = task()
    expect(isGovernanceTask(value)).toBe(false)
    expect(taskDisplayType(value)).toEqual({ key: 'full', label: 'Bot自进化' })
    expect(governanceImprovementId(value)).toBeNull()
  })
})
