import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import {
  generateWorkflowAiDraft,
  workflowSpecSignature,
  type WorkflowAiDraft,
  type WorkflowAiMode,
} from '../services/workflow-ai-draft'

interface DraftResult {
  requestId: number
  draft: WorkflowAiDraft
  baseSpec: WorkflowSpec
}

/** Owns request lifetime; abort is paired with an id guard for non-cooperative transports. */
export function useWorkflowAiDraft(open: boolean, initialMode: WorkflowAiMode, spec: WorkflowSpec | null) {
  const [mode, setMode] = useState(initialMode)
  const [prompt, setPrompt] = useState('')
  const [result, setResult] = useState<DraftResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestId = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const workflowId = spec?.id
  const signature = spec ? workflowSpecSignature(spec) : null

  const abort = useCallback(() => {
    requestId.current += 1
    controller.current?.abort()
    controller.current = null
  }, [])

  const invalidate = useCallback(() => {
    abort()
    setBusy(false)
    setResult(null)
    setError(null)
  }, [abort])

  // Reset session on close, workflow switch, or an externally selected mode.
  useLayoutEffect(() => {
    invalidate()
    setMode(initialMode)
    setPrompt('')
    return abort
  }, [open, initialMode, workflowId, invalidate, abort])

  // Keep completed previews visible but stale when the current workflow is edited.
  useLayoutEffect(() => {
    abort()
    setBusy(false)
    setError(null)
    return abort
  }, [signature, abort])

  function changePrompt(value: string) {
    if (value === prompt) return
    invalidate()
    setPrompt(value)
  }

  function changeMode(value: WorkflowAiMode) {
    if (value === mode) return
    invalidate()
    setMode(value)
  }

  async function generate() {
    if (!open || !spec || controller.current) return
    invalidate()
    if (!prompt.trim()) {
      setError('请输入想增加或修改的工作流能力')
      return
    }
    const id = requestId.current
    const pending = new AbortController()
    controller.current = pending
    setBusy(true)
    try {
      // Retain exactly the request snapshot for old/new node comparisons.
      const baseSpec = structuredClone(spec)
      const draft = await generateWorkflowAiDraft(mode, prompt.trim(), baseSpec, pending.signal)
      if (pending.signal.aborted || id !== requestId.current) return
      if (draft.workflowId !== baseSpec.id || draft.spec.id !== baseSpec.id) {
        throw new Error('生成结果不属于当前工作流，请重试')
      }
      setResult({ requestId: id, draft, baseSpec })
    } catch (cause) {
      if (!pending.signal.aborted && id === requestId.current) {
        setError(cause instanceof Error ? cause.message : 'AI 草稿生成失败，请重试')
      }
    } finally {
      if (id === requestId.current) {
        controller.current = null
        setBusy(false)
      }
    }
  }

  const visibleResult = open && result?.draft.workflowId === workflowId ? result : null
  const stale = Boolean(visibleResult && visibleResult.draft.baseSignature !== signature)

  return {
    mode, prompt, result: visibleResult, busy, error, stale,
    changeMode, changePrompt, generate, cancel: invalidate,
  }
}
