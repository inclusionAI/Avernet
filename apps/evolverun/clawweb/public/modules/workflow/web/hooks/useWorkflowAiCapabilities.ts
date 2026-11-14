import { useCallback, useEffect, useRef, useState } from 'react'
import { abortable, readWorkflowAiCapabilities } from '../services/workflow-ai-lifecycle'
import type { WorkflowAiCapabilities } from '../api/workflow-ai'

type CapabilityState = { workflowId: string; value?: WorkflowAiCapabilities; loading: boolean; error?: string }

export function useWorkflowAiCapabilities(open: boolean, workflowId?: string) {
  const [state, setState] = useState<CapabilityState | null>(null)
  const [revision, setRevision] = useState(0)
  const sequence = useRef(0)
  useEffect(() => {
    const id = ++sequence.current
    if (!open || !workflowId) { setState(null); return }
    const controller = new AbortController()
    let disposed = false
    const timeout = setTimeout(() => controller.abort(new Error('AI 草稿能力检查超时：无法确认服务器 autoHeal.botId / BaaS，请重试')), 10_000)
    setState({ workflowId, loading: true })
    void abortable(readWorkflowAiCapabilities(workflowId, controller.signal), controller.signal).then(value => {
      if (!controller.signal.aborted && sequence.current === id) setState({ workflowId, value, loading: false })
    }, cause => {
      if (!disposed && sequence.current === id) setState({ workflowId, loading: false, error: cause instanceof Error ? cause.message : 'AI 草稿能力检查失败' })
    }).finally(() => clearTimeout(timeout))
    return () => { disposed = true; ++sequence.current; clearTimeout(timeout); controller.abort() }
  }, [open, workflowId, revision])
  const retry = useCallback(() => setRevision(value => value + 1), [])
  const current = open && state?.workflowId === workflowId ? state : null
  return { value: current?.value, loading: open && !!workflowId && (!current || current.loading), error: current?.error, retry }
}
