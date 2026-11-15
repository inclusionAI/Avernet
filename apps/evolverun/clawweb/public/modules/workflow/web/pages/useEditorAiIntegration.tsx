import { useLayoutEffect, useRef, useState } from 'react'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import WorkflowAiDrawer from '../components/WorkflowAiDrawer'
import EditorAiControls from '../components/EditorAiControls'
import { useEditorStore } from '../editor/store'
import { workflowSpecSignature, type WorkflowAiDraft, type WorkflowAiMode } from '../services/workflow-ai-draft'
import { assertWorkflowAiSpec } from '../services/workflow-ai-spec'

type Message = { type: 'success' | 'error'; text: string }
type Options = {
  spec: WorkflowSpec | null
  selectedWorkflowId: string | null
  targetWorkflowId?: string | null
  editorSessionId: number
  loading: boolean
  saving: boolean
  saveInFlight: { current: boolean }
  hasPendingEdits: boolean
  onMessage: (message: Message) => void
  onApplied: () => void
}

function contextKey() {
  const state = useEditorStore.getState()
  return JSON.stringify([state.sessionId, state.positions, state._databaseSpec, state._savedSpec])
}

/** AI owns neither the upstream document nor save/deploy/split/YAML-editor state. */
export function useEditorAiIntegration(options: Options) {
  const { spec, selectedWorkflowId, targetWorkflowId, editorSessionId, loading, saving, saveInFlight, hasPendingEdits, onMessage, onApplied } = options
  const [session, setSession] = useState<{ id: number; mode: WorkflowAiMode; editorSessionId: number } | null>(null)
  const sequence = useRef(0)
  const activeSession = useRef<number | null>(null)
  const key = contextKey()
  const current = useRef(options)
  current.current = options
  useLayoutEffect(() => {
    activeSession.current = null
    setSession(null)
  }, [selectedWorkflowId, targetWorkflowId, editorSessionId])
  const close = () => { activeSession.current = null; setSession(null) }
  const disabled = !spec || loading || saving || hasPendingEdits || Boolean(selectedWorkflowId && selectedWorkflowId !== spec.id)

  function open(mode: WorkflowAiMode) {
    const state = useEditorStore.getState()
    if (disabled || saveInFlight.current || Object.keys(state.pendingEdits ?? {}).length) {
      onMessage({ type: 'error', text: '请先加载当前工作流，并应用或放弃未完成的配置草稿，等待保存完成。' })
      return
    }
    const id = ++sequence.current
    activeSession.current = id
    setSession({ id, mode, editorSessionId })
  }

  function apply(draft: WorkflowAiDraft) {
    const state = useEditorStore.getState()
    const live = current.current
    if (!session || activeSession.current !== session.id || session.editorSessionId !== state.sessionId
      || live.loading || live.saving || saveInFlight.current || Object.keys(state.pendingEdits ?? {}).length
      || !state.spec || draft.workflowId !== state.spec.id || draft.spec.id !== state.spec.id
      || live.selectedWorkflowId && live.selectedWorkflowId !== state.spec.id
      || live.targetWorkflowId && live.targetWorkflowId !== state.spec.id
      || draft.baseSignature !== workflowSpecSignature(state.spec) || key !== contextKey()) {
      onMessage({ type: 'error', text: '编辑器内容、布局或会话已变更，或存在未应用草稿，请重新生成 AI 草稿。' })
      return
    }
    try {
      assertWorkflowAiSpec(draft.spec, state.spec)
      state.importYaml(draft.spec)
      // Store actions report refused imports via actionError, not necessarily by throwing.
      const imported = useEditorStore.getState()
      if (imported.actionError) throw new Error(imported.actionError)
      if (imported.sessionId !== state.sessionId || !imported.spec
        || workflowSpecSignature(imported.spec) !== workflowSpecSignature(draft.spec)) throw new Error('AI 草稿未应用，原编辑器内容仍保留')
      close()
      onApplied()
      onMessage({ type: 'success', text: 'AI 草稿已通过校验并应用到编辑器，尚未保存或部署。' })
    } catch (cause) {
      onMessage({ type: 'error', text: cause instanceof Error ? cause.message : 'AI 草稿应用失败' })
    }
  }

  return {
    open: session !== null && session.editorSessionId === editorSessionId,
    controls: <EditorAiControls disabled={disabled} hasPendingEdits={hasPendingEdits} onOpen={open} />,
    drawer: session && session.editorSessionId === editorSessionId ? <WorkflowAiDrawer key={session.id} open mode={session.mode}
      contextKey={key} spec={loading || selectedWorkflowId && spec?.id !== selectedWorkflowId ? null : spec}
      blockedReason={hasPendingEdits ? '请先应用或放弃未完成的配置草稿，再生成或应用 AI 草稿。'
        : saving ? '工作流正在保存，请等待完成后再生成或应用 AI 草稿。' : undefined}
      onClose={close} onApply={apply} /> : null,
  }
}
