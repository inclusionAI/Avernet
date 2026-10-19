import { useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { AutoHealRunRequest, AutoHealRunResult } from '@avernet/clawweb-shared/web/types'

export interface SourceAutoHealRunRequest extends AutoHealRunRequest {
  sourceFlowId: string
}

export function useAutoHealSourceRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ workflowId, sourceFlowId, params }: SourceAutoHealRunRequest) => {
      if (!sourceFlowId?.trim()) {
        throw new Error('缺少 sourceFlowId（原运行 flow_id），无法重新运行。')
      }
      // Only the server may resolve bot/lifecycle from the original flow.
      return fetchJson<AutoHealRunResult>('/api/auto-heal/run', {
        method: 'POST',
        body: JSON.stringify({ workflowId, sourceFlowId, params }),
      })
    },
    // Retrying a task submission must be a deliberate user action.
    retry: false,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}
