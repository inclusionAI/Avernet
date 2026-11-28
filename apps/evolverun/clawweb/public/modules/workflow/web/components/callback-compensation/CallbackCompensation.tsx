import { useCallback, useEffect, useRef, useState } from 'react'
import { callbackCompensationService as service } from '../../api/callback-compensation'
import { callbackCompensationErrorStatus } from '../../models/callbackCompensationErrors'
import type { CallbackCompensationCapabilities, CallbackCompensationContext } from '../../models/callbackCompensationTypes'
import { useCallbackCompensation } from '../../hooks/callback-compensation/useCallbackCompensation'
import { useCallbackCompensationIdentity } from '../../hooks/callback-compensation/useCallbackCompensationIdentity'
import { Button, BellRing } from './primitives'
import { CallbackCompensationDialog } from './CallbackCompensationDialog'

function Authorized({ context, capabilities, visible, onDenied }: {
  context: CallbackCompensationContext; capabilities: CallbackCompensationCapabilities; visible: boolean; onDenied: () => void
}) {
  const state = useCallbackCompensation(context, capabilities, onDenied)
  return <>
    {visible && <Button aria-label="成功回调补偿" size="sm" variant="outline"
      className="border-primary/30 bg-primary/5 text-primary hover:bg-primary/10"
      onClick={() => state.setOpen(true)}>
      <BellRing aria-hidden className="size-3.5" />成功回调补偿
      {state.batch.batchId && <span aria-hidden className="size-1.5 rounded-full bg-primary" />}
    </Button>}
    <CallbackCompensationDialog context={context} state={state} capabilities={capabilities} />
  </>
}

function CapabilityEntry({ context, visible }: { context: CallbackCompensationContext; visible: boolean }) {
  const [capabilities, setCapabilities] = useState<CallbackCompensationCapabilities | null>(null)
  const [status, setStatus] = useState<'checking' | 'allowed' | 'denied' | 'unavailable'>('checking')
  const [revision, setRevision] = useState(0)
  const request = useRef<AbortController | null>(null)
  const sequence = useRef(0)
  const deny = useCallback(() => {
    // A mutation denial invalidates any older capability read, not just the visible dialog.
    ++sequence.current
    request.current?.abort()
    setCapabilities(null)
    setStatus('denied')
  }, [])
  useEffect(() => {
    let active = true
    const check = async () => {
      request.current?.abort()
      const controller = new AbortController(), serial = ++sequence.current
      request.current = controller
      try {
        const value = await service.capabilities(context.workflowId, controller.signal)
        if (!active || controller.signal.aborted || serial !== sequence.current) return
        setCapabilities(value); setStatus('allowed')
      } catch (failure) {
        if (!active || controller.signal.aborted || serial !== sequence.current) return
        setCapabilities(null)
        setStatus([401, 403].includes(Number(callbackCompensationErrorStatus(failure))) ? 'denied' : 'unavailable')
      }
    }
    void check()
    // Revalidate exact admin, execution gates and environment, not a client-side canEdit/isAdmin flag.
    const timer = window.setInterval(() => void check(), 5000)
    return () => { active = false; ++sequence.current; request.current?.abort(); window.clearInterval(timer) }
  }, [context.workflowId, revision])
  if (status === 'unavailable') return visible ? <div className="flex items-center gap-2 text-xs text-muted-foreground">
    <span>补偿服务不可用</span><Button size="sm" variant="ghost" onClick={() => setRevision(value => value + 1)}>重试补偿权限</Button>
  </div> : null
  if (status !== 'allowed' || !capabilities) return null
  return <Authorized key={capabilities.environment} context={context} capabilities={capabilities} visible={visible} onDenied={deny} />
}

/** The native workspace boundary aborts old actor/scope/workflow requests and discards their views.
 * Uncertain creates remain partitioned in memory, never cleared by this unmount. */
export function CallbackCompensation({ workflowId, title, visible = true }: { workflowId: string; title?: string; visible?: boolean }) {
  const identity = useCallbackCompensationIdentity()
  if (!workflowId || !identity) return null
  const context = { workflowId, title, userId: identity.actorId, scopeKey: identity.scopeKey }
  const key = JSON.stringify([identity.scopeKey, identity.actorId, identity.generation, workflowId])
  return <CapabilityEntry key={key} context={context} visible={visible} />
}
