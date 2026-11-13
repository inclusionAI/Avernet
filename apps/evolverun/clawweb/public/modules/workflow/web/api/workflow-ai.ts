import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type {
  SmartOnboardingGenerateRequest,
  SmartOnboardingGenerateResult,
  SmartOnboardingGenerationStatus,
  WorkflowSpec,
  WorkflowValidationResult,
} from '@avernet/clawweb-shared/web/types'

/** Reuses the intelligent onboarding wire contract and shared auth/gzip transport. */
export const workflowAiApi = {
  generate(input: SmartOnboardingGenerateRequest, signal: AbortSignal) {
    return fetchJson<SmartOnboardingGenerateResult>('/api/smart-onboarding/generate', {
      method: 'POST', body: JSON.stringify(input), signal,
    })
  },
  poll(generationId: string, signal: AbortSignal) {
    return fetchJson<SmartOnboardingGenerationStatus>(
      `/api/smart-onboarding/generations/${encodeURIComponent(generationId)}`, { signal },
    )
  },
  validate(spec: WorkflowSpec, signal: AbortSignal) {
    return fetchJson<WorkflowValidationResult>('/api/workflows/validate', {
      method: 'POST', body: JSON.stringify({ spec }), signal,
    })
  },
}
