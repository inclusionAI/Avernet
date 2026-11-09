import { carryActiveDraftBaseline, editorWorkflowSourceIsCurrent } from '../api/editorWorkflowSource'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { prepareWorkflowSession, useEditorStore, type EditorState } from './store'
import { hasAnyPackVersionRisk, hasAnyPackVersionUpload, packVersionsRevision } from './packVersionSession'

export type WorkflowTransition = { readonly state: EditorState; readonly packRevision: number }

/** Authorize only the state the user saw; this never clears a draft. */
export function beginWorkflowTransition(): WorkflowTransition | null {
  if (hasAnyPackVersionUpload()) { window.alert('版本文件正在上传，请等待完成后再切换。'); return null }
  const state = useEditorStore.getState()
  const packRevision = packVersionsRevision()
  const canvasRisk = state.isDirty || state.isLayoutDirty || Object.keys(state.pendingEdits).length > 0
  const packRisk = hasAnyPackVersionRisk()
  if ((canvasRisk || packRisk) && !window.confirm('当前画布或 Pack 有未保存内容。切换成功后画布草稿将被放弃，Pack 文件仅保留在页面内存中。确定继续？')) return null
  return { state, packRevision }
}

export function workflowTransitionIsCurrent(approval: WorkflowTransition): boolean {
  return !hasAnyPackVersionUpload() && packVersionsRevision() === approval.packRevision && useEditorStore.getState() === approval.state
}

export function prepareWorkflowTransition(approval: WorkflowTransition, target: WorkflowSpec, expectedId: string): WorkflowSpec | null {
  if (!target || target.id !== expectedId || !workflowTransitionIsCurrent(approval)) return null
  const prepared = prepareWorkflowSession(target)?.spec ?? null
  if (prepared) carryActiveDraftBaseline(target, prepared)
  return prepared
}

/** Reject stale approvals, malformed or mismatched targets before changing the session. */
export function commitWorkflowTransition(approval: WorkflowTransition, target: WorkflowSpec, expectedId: string, isNew = false, databaseSpec: WorkflowSpec | null = target, activeDeployNumber?: number | null): boolean {
  if (!target || target.id !== expectedId || !workflowTransitionIsCurrent(approval) || !isNew && !editorWorkflowSourceIsCurrent(target, expectedId)) return false
  return useEditorStore.getState().replaceSession(target, approval.state, isNew, isNew ? null : databaseSpec, activeDeployNumber)
}
