import { useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type {
  AutoHealSaveRequest, AutoHealSaveResult, AutoHealDeployRequest, AutoHealDeployResult,
} from '@avernet/clawweb-shared/web/types'

export interface WorkflowAutoHealSaveRequest extends AutoHealSaveRequest {
  requestId?: string
  createOnly?: boolean
  sourceCommit?: string
  expectedSavedHead?: string | null
}
export interface WorkflowAutoHealDeployRequest extends AutoHealDeployRequest {
  requestId?: string
  candidateCommit?: string
  sourceFlowId?: string | null
}

/** The trusted host requires CSRF and uncompressed JSON; fetchJson gzips large strings. */
function postAutoHealWrite<T>(operation: 'save' | 'deploy', input: WorkflowAutoHealSaveRequest | WorkflowAutoHealDeployRequest): Promise<T> {
  return fetchJson<T>(`/api/auto-heal/${operation}`, {
    method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' },
    body: new Blob([JSON.stringify(input)], { type: 'application/json' }),
  })
}

export function useAutoHealSave() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: WorkflowAutoHealSaveRequest) => postAutoHealWrite<AutoHealSaveResult & { requestId?: string }>('save', input),
    // The caller owns the immutable candidate and requestId across explicit retries.
    retry: false,
    onSuccess: (_result, input) => {
      void queryClient.invalidateQueries({ queryKey: ['editor-workflow-document', input.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}

export function useAutoHealDeploy() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: WorkflowAutoHealDeployRequest) => postAutoHealWrite<AutoHealDeployResult & { requestId?: string }>('deploy', input),
    retry: false,
    onSuccess: (_result, input) => {
      void queryClient.invalidateQueries({ queryKey: ['editor-workflow-document', input.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}
