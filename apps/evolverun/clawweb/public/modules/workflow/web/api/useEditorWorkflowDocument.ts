import { useQuery } from '@tanstack/react-query'
import { readEditorWorkflowSource } from './editorWorkflowSource'

/** Cache both the displayed source and the independent DB optimistic baseline. */
export function useEditorWorkflowDocument(workflowId: string) {
  const query = useQuery({ queryKey: ['editor-workflow-document', workflowId], queryFn: () => readEditorWorkflowSource(workflowId), enabled: !!workflowId })
  return { ...query, data: query.data?.spec, databaseSpec: query.data?.databaseSpec, activeDeployNumber: query.data?.activeDeployNumber }
}
