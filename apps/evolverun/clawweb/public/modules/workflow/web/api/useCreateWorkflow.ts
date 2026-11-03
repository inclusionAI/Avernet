import { sameExecutionSpec } from '../editor/packCanvas'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { createWorkflowPack } from '../editor/newWorkflowSave'

/** Public callers never create through the legacy workflow upsert route. */
export function useCreateWorkflow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ workflowId, spec, facade }: { workflowId: string; spec: WorkflowSpec;
      facade?: { command?: string; remark?: string }; botOwnerId?: string; botId?: string }) => {
      if (spec.id !== workflowId) throw new Error('新建工作流身份不匹配')
      const candidate = { ...spec, ...(facade ? { facade } : {}) }
      const result = await createWorkflowPack(candidate)
      if (!sameExecutionSpec(candidate, result.requestedSpec)) throw new Error('原始新建请求已完成，但当前表单内容已变化；当前草稿仍保留，请处理已创建的版本。')
      return result
    },
    retry: false,
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
      void queryClient.invalidateQueries({ queryKey: ['editor-workflow-document', variables.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflow', variables.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['facade-bindings'] })
    },
  })
}
