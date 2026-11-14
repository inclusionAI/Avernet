import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec, WorkflowValidationResult } from '@avernet/clawweb-shared/web/types'

export type WorkflowAiMode = 'create' | 'modify'
export type WorkflowAiStatus = 'pending' | 'running' | 'dispatch_uncertain' | 'completed' | 'failed' | 'cancelled'
export type WorkflowAiCapabilities = { available: boolean; reason?: string }
export type WorkflowAiInput = { requestId: string; mode: WorkflowAiMode; prompt: string; currentYaml: string }
export type WorkflowAiSubmission = { draftId: string; status: WorkflowAiStatus }
export type WorkflowAiTask = WorkflowAiSubmission & {
  workflowId: string
  inputSpecDigest: string
  error?: string
  result?: { yaml: string; summary: string }
}
const draftsPath = (workflowId: string) => `/api/workflows/${encodeURIComponent(workflowId)}/ai-drafts`
const taskPath = (workflowId: string, draftId: string) => `${draftsPath(workflowId)}/${encodeURIComponent(draftId)}`

/** Dedicated workflow AI draft contract; the browser supplies no provider target. */
export const workflowAiApi = {
  capabilities(workflowId: string, signal: AbortSignal) {
    return fetchJson<WorkflowAiCapabilities>(`${draftsPath(workflowId)}/capabilities`, { signal })
  },
  generate(workflowId: string, input: WorkflowAiInput, signal: AbortSignal) {
    return fetchJson<WorkflowAiSubmission>(draftsPath(workflowId), {
      method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' }, body: JSON.stringify(input), signal,
    })
  },
  poll(workflowId: string, draftId: string, signal: AbortSignal) {
    return fetchJson<WorkflowAiTask>(taskPath(workflowId, draftId), { signal })
  },
  cancel(workflowId: string, draftId: string, signal: AbortSignal) {
    return fetchJson<WorkflowAiTask>(`${taskPath(workflowId, draftId)}/cancel`, { method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' }, signal })
  },
  validate(spec: WorkflowSpec, signal: AbortSignal) {
    return fetchJson<WorkflowValidationResult>('/api/workflows/validate', {
      method: 'POST', body: JSON.stringify({ spec }), signal,
    })
  },
}
