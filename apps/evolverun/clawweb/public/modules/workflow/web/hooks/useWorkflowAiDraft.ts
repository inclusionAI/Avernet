import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import {
  generateWorkflowAiDraft,
  WorkflowAiCandidateError,
  workflowSpecSignature,
  type WorkflowAiDraft,
  type WorkflowAiMode,
} from '../services/workflow-ai-draft'

import { cancelWorkflowAiAttempt, createWorkflowAiAttempt, WorkflowAiRecoveryError, type WorkflowAiAttempt } from '../services/workflow-ai-lifecycle'

interface DraftResult {
  requestId: number
  draft: WorkflowAiDraft
  baseSpec: WorkflowSpec
  contextKey?: string
}

/** Owns request lifetime; abort is paired with an id guard for non-cooperative transports. */
export function useWorkflowAiDraft(open: boolean, initialMode: WorkflowAiMode, spec: WorkflowSpec | null, contextKey?: string) {
  const [mode, setMode] = useState(initialMode)
  const [prompt, setPrompt] = useState('')
  const [result, setResult] = useState<DraftResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [recoverable, setRecoverable] = useState(false)
  const attempt = useRef<WorkflowAiAttempt | null>(null)
  const attemptWorkflow = useRef<string | null>(null)
  const requestId = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const workflowId = spec?.id
  const signature = spec ? workflowSpecSignature(spec) : null

  const abort = useCallback(() => {
    requestId.current += 1
    const active = controller.current
    active?.abort()
    controller.current = null
    if (!active && attempt.current && attemptWorkflow.current) void cancelWorkflowAiAttempt(attemptWorkflow.current, attempt.current)
    attempt.current = null
  }, [])

  const invalidate = useCallback(() => {
    abort()
    setBusy(false)
    setResult(null)
    setError(null)
    setRecoverable(false)
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
    setRecoverable(false)
    return abort
  }, [signature, contextKey, abort])

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
    setResult(null)
    setError(null)
    setRecoverable(false)
    if (!prompt.trim()) {
      setError('请输入想增加或修改的工作流能力')
      return
    }
    const id = ++requestId.current
    const pending = new AbortController()
    controller.current = pending
    setBusy(true)
    try {
      // Retain exactly the request snapshot for old/new node comparisons.
      const baseSpec = structuredClone(spec)
      const currentAttempt = attempt.current ?? createWorkflowAiAttempt()
      attempt.current = currentAttempt
      attemptWorkflow.current = baseSpec.id
      const draft = await generateWorkflowAiDraft(mode, prompt, baseSpec, pending.signal, currentAttempt)
      if (pending.signal.aborted || id !== requestId.current) return
      if (draft.workflowId !== baseSpec.id || draft.spec.id !== baseSpec.id) {
        throw new WorkflowAiCandidateError('生成结果不属于当前工作流，请重试')
      }
      attempt.current = null
      setResult({ requestId: id, draft, baseSpec, contextKey })
    } catch (cause) {
      if (!pending.signal.aborted && id === requestId.current) {
        if (cause instanceof WorkflowAiRecoveryError) {
          attempt.current = cause.attempt
          setRecoverable(true)
        } else if (!(cause instanceof WorkflowAiCandidateError) && attempt.current) {
          // A failed readiness/auth/transport check cannot erase an earlier submission.
          setRecoverable(true)
        } else attempt.current = null
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
  const stale = Boolean(visibleResult && (visibleResult.draft.baseSignature !== signature || visibleResult.contextKey !== contextKey))

  return {
    mode, prompt, result: visibleResult, busy, error, stale, recoverable,
    changeMode, changePrompt, generate, cancel: invalidate,
  }
}
