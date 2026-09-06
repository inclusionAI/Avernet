import type { EvolveTask } from '../../api/client'
import { evolveTaskRegistry } from './task-registry'

const INSIGHT_IMPROVEMENT_SOURCE = 'insight_improvement'

function taskInput(task: EvolveTask): Record<string, unknown> | null {
  const input = task.config.input
  return input && typeof input === 'object' && !Array.isArray(input)
    ? input as Record<string, unknown>
    : null
}

export function isGovernanceTask(task: EvolveTask): boolean {
  return task.source?.sourceType === INSIGHT_IMPROVEMENT_SOURCE
    || taskInput(task)?.type === INSIGHT_IMPROVEMENT_SOURCE
}

export function taskDisplayType(task: EvolveTask): { key: string; label: string } {
  if (isGovernanceTask(task)) return { key: 'governance', label: '治理优化' }
  return {
    key: task.task_type,
    label: evolveTaskRegistry[task.task_type as keyof typeof evolveTaskRegistry]?.label ?? task.task_type,
  }
}

export function governanceImprovementId(task: EvolveTask): number | null {
  if (!isGovernanceTask(task)) return null
  const inputId = taskInput(task)?.improvementId
  const parsedInputId = typeof inputId === 'number' ? inputId : Number(inputId)
  if (Number.isSafeInteger(parsedInputId) && parsedInputId > 0) return parsedInputId

  const match = task.source?.sourceId.match(/^improvement:(\d+)$/)
  if (!match) return null
  const parsedSourceId = Number(match[1])
  return Number.isSafeInteger(parsedSourceId) && parsedSourceId > 0 ? parsedSourceId : null
}
