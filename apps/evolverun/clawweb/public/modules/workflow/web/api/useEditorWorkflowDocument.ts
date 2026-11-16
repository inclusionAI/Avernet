import { useSyncExternalStore } from 'react'
import { useQuery } from '@tanstack/react-query'
import { readEditorWorkflowSource } from './editorWorkflowSource'
import { editorWorkflowWriteSnapshot, editorWorkflowWriteState, subscribeEditorWorkflowWrites } from '../editor/workflowWriteGuard'

/** Never expose old cache or an in-flight read while a write is unresolved. */
export function useEditorWorkflowDocument(workflowId: string, sessionId?: number) {
  useSyncExternalStore(subscribeEditorWorkflowWrites, () => editorWorkflowWriteSnapshot(workflowId), () => 'clear:0')
  const { phase, epoch } = editorWorkflowWriteState(workflowId)
  const queryKey = epoch ? ['editor-workflow-document', workflowId, epoch, sessionId] : ['editor-workflow-document', workflowId]
  const query = useQuery({ queryKey, queryFn: () => readEditorWorkflowSource(workflowId), structuralSharing: false,
    placeholderData: () => undefined, enabled: !!workflowId && phase === 'clear', retry: false })
  const blocked = phase !== 'clear'
  const error = phase === 'unknown' ? new Error('数据库写入结果未终结，需要人工核对；不能将读取结果视为已确认基线。') : blocked ? null : query.error
  return { ...query, error, isLoading: phase === 'pending' || !blocked && query.isLoading,
    data: blocked ? undefined : query.data?.spec, databaseSpec: blocked ? undefined : query.data?.databaseSpec,
    activeDeployNumber: blocked ? undefined : query.data?.activeDeployNumber }
}
