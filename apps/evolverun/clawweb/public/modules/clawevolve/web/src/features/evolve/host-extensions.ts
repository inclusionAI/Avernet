import type { ReactNode } from 'react'
import type { EvolveStep, EvolveTask } from '../../api/client'

export type EvolveTaskPresentationExtension = {
  id: string
  renderStepResult?: (context: { task: EvolveTask; step: EvolveStep }) => ReactNode
  suppressDefaultStepDeliverables?: (context: { task: EvolveTask; step: EvolveStep }) => boolean
}

export function taskPresentationExtension(
  task: EvolveTask,
  extensions: readonly EvolveTaskPresentationExtension[],
): EvolveTaskPresentationExtension | undefined {
  const presentation = task.config.presentation
  if (!presentation || typeof presentation !== 'object' || Array.isArray(presentation)) return undefined
  const extensionId = (presentation as { extensionId?: unknown }).extensionId
  return typeof extensionId === 'string' && extensionId.trim()
    ? extensions.find((extension) => extension.id === extensionId)
    : undefined
}
