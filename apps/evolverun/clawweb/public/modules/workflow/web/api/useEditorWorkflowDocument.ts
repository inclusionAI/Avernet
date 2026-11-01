import { useQuery } from '@tanstack/react-query'
import { readWorkflowDocument } from './workflowDocument'

/** Normalize before caching so initial Editor loads and imperative switches agree. */
export function useEditorWorkflowDocument(workflowId: string) {
  return useQuery({ queryKey: ['editor-workflow-document', workflowId], queryFn: () => readWorkflowDocument(workflowId), enabled: !!workflowId })
}
