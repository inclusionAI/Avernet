import { useContext } from 'react'
import { QueryClient, QueryClientContext, useQuery } from '@tanstack/react-query'
import { api } from '@avernet/clawweb-shared/web/api/client'

/** Editor publishes a complete Pack tree; only explicit host canDeploy authorizes it. */
export interface WorkflowEditorAccess { canView: boolean; canEdit: boolean; canExecute?: boolean; canDeploy?: boolean }
// A disabled observer keeps hook ordering stable without a provider. Isolated
// consumers must supply explicit access; unknown existing-workflow access denies writes.
const isolatedClient = new QueryClient()

export function useEditorPermissions(workflowId: string | null, supplied?: WorkflowEditorAccess) {
  const client = useContext(QueryClientContext)
  const query = useQuery<WorkflowEditorAccess>({
    queryKey: ['workflow-access', workflowId],
    queryFn: () => api.workflows.getAccess(workflowId!),
    enabled: !!client && !!workflowId && supplied === undefined,
    retry: false,
    placeholderData: () => undefined,
  }, client ?? isolatedClient)
  const access = supplied ?? (client && !query.isError ? query.data : undefined)
  return {
    canEdit: !workflowId || access?.canEdit === true,
    canDeploy: !!workflowId && access?.canDeploy === true,
    pending: !!workflowId && !!client && supplied === undefined && query.isPending,
  }
}
