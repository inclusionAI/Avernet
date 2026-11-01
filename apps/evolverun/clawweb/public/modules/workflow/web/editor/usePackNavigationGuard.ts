import { createContext, useContext, useEffect } from 'react'
import { UNSAFE_NavigationContext } from 'react-router-dom'
import { hasAnyPackVersionRisk, hasAnyPackVersionUpload } from './packVersionSession'
import { useEditorStore } from './store'
import { workflowTransitionIsCurrent, type WorkflowTransition } from './workflowTransition'

/** True when an already-mounted workspace rendered a new POP location before
 * its native dispatcher could claim that same traversal. */
export const WorkspacePopPendingContext = createContext(false)

type RoutePermit = { approval: WorkflowTransition | null; used: boolean; completed: boolean }
let routePermit: RoutePermit | null = null
let installedGuards = 0
const committedPopEvents = new WeakSet<PopStateEvent>()
const consumedPopEvents = new WeakSet<PopStateEvent>()

type PopHandler = (event: PopStateEvent) => void
const workspacePops = new Set<PopHandler>()
const editorPops = new Set<PopHandler>()
let popDispatcherInstalled = false
const workflowPopOwners = new Set<symbol>()
const workflowPopOwnershipListeners = new Set<() => void>()
function notifyWorkflowPopOwnership() { for (const listener of workflowPopOwnershipListeners) listener() }

/** One native listener owns ordering: workspace transaction (including restore)
 * first, then ordinary leave protection. React mount order cannot prompt twice. */
function dispatchPop(event: PopStateEvent) {
  for (const handler of [...workspacePops, ...editorPops]) {
    handler(event)
    if (consumedPopEvents.has(event) || event.cancelBubble) return
  }
}
function registerPopHandler(handler: PopHandler, handlers: Set<PopHandler>) {
  handlers.add(handler)
  if (!popDispatcherInstalled) { window.addEventListener('popstate', dispatchPop, true); popDispatcherInstalled = true }
  return () => {
    handlers.delete(handler)
    if (!workspacePops.size && !editorPops.size) {
      window.removeEventListener('popstate', dispatchPop, true)
      popDispatcherInstalled = false
    }
  }
}
export function registerWorkflowPopHandler(handler: PopHandler) { return registerPopHandler(handler, workspacePops) }
/** Workspace owns the entire native POP transaction, including the restoration
 * traversal. The ordinary editor guard must not issue a second confirmation. */
export function claimWorkflowPop() { const token = Symbol('workflow-pop'); workflowPopOwners.add(token); notifyWorkflowPopOwnership(); return token }
export function releaseWorkflowPop(token: symbol) { if (workflowPopOwners.delete(token)) notifyWorkflowPopOwnership() }
export function isWorkflowPopOwned() { return workflowPopOwners.size > 0 }
export function subscribeWorkflowPopOwnership(listener: () => void) {
  workflowPopOwnershipListeners.add(listener)
  return () => workflowPopOwnershipListeners.delete(listener)
}
export function workflowPopOwnershipSnapshot() { return workflowPopOwners.size > 0 }

/** Explicit ownership survives native modal dialogs and browser event-flag quirks. */
export function consumeWorkflowPop(event: PopStateEvent) {
  consumedPopEvents.add(event)
  event.stopImmediatePropagation()
}

/** The workspace has already authorized and committed this exact native POP. */
export function permitCommittedWorkflowPop(event: PopStateEvent) {
  committedPopEvents.add(event)
}

/** A single confirmed route change; if the subsequent CAS fails, restore the prior URL. */
export function commitWorkflowRoute(
  approval: WorkflowTransition, navigate: () => void, commit: () => boolean, rollback: () => void,
): boolean {
  if (routePermit || !workflowTransitionIsCurrent(approval)) return false
  const run = (permit: RoutePermit, action: () => void): { completed: boolean; error: boolean } => {
    routePermit = permit
    try { action(); return { completed: permit.completed || (!permit.used && installedGuards === 0), error: false } }
    catch { return { completed: permit.completed, error: true } }
    finally { routePermit = null }
  }
  const routed = run({ approval, used: false, completed: false }, navigate)
  if (!routed.completed) return false
  if (!routed.error) {
    try { if (workflowTransitionIsCurrent(approval) && commit()) return true } catch { /* Restore the old URL below. */ }
  }
  run({ approval: null, used: false, completed: false }, rollback)
  return false
}

/** BrowserRouter is a declarative router, so useBlocker is unavailable. Guard its
 * navigator and native POP separately, without discarding any editor state. */
export function usePackNavigationGuard(enabled = true) {
  const context = useContext(UNSAFE_NavigationContext)
  useEffect(() => {
    if (!enabled) return
    const beforeUnload = (event: BeforeUnloadEvent) => {
      const state = useEditorStore.getState()
      if (!state.isDirty && !state.isLayoutDirty && !Object.keys(state.pendingEdits ?? {}).length && !hasAnyPackVersionRisk()) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [enabled])
  useEffect(() => {
    if (!enabled || !context) return
    const navigator = context.navigator
    installedGuards++
    const push = navigator.push, replace = navigator.replace, go = navigator.go
    let index = typeof window.history.state?.idx === 'number' ? window.history.state.idx as number : null
    let href = window.location.href
    let historyState: unknown = window.history.state
    let restoring = false
    const allowed = () => {
      // Native workspace POPs are handled by the dispatcher below. Ownership
      // never authorizes unrelated navigator push/replace/go calls.
      if (hasAnyPackVersionUpload()) {
        window.alert('版本文件正在上传，请等待上传完成后再离开。')
        return false
      }
      const state = useEditorStore.getState()
      const canvasRisk = state.isDirty || state.isLayoutDirty || Object.keys(state.pendingEdits ?? {}).length > 0
      const packRisk = hasAnyPackVersionRisk()
      if (!canvasRisk && !packRisk) return true
      const details = [
        canvasRisk ? '画布有未保存或未应用的修改' : '',
        packRisk ? 'Pack 版本文件有未保存更改或保存状态不确定' : '',
      ].filter(Boolean).join('；')
      return window.confirm(`${details}。这些内容可能未持久化，离开后可能丢失。确定离开？`)

    }
    const remember = () => {
      index = typeof window.history.state?.idx === 'number' ? window.history.state.idx as number : null
      href = window.location.href
      historyState = window.history.state
    }
    const permitted = () => {
      const permit = routePermit
      if (!permit || permit.used) return allowed()
      permit.used = true
      return !permit.approval || workflowTransitionIsCurrent(permit.approval)
    }
    const guardedPush: typeof push = (...args) => {
      const permit = routePermit
      if (permitted()) { push.apply(navigator, args); if (permit?.used) permit.completed = true; remember() }
    }
    const guardedReplace: typeof replace = (...args) => {
      const permit = routePermit
      if (permitted()) { replace.apply(navigator, args); if (permit?.used) permit.completed = true; remember() }
    }
    // Check again at POP time: edits can occur while browser history.go is in flight.
    const guardedGo: typeof go = (...args) => { if (allowed()) go.apply(navigator, args) }
    navigator.push = guardedPush
    navigator.replace = guardedReplace
    navigator.go = guardedGo
    const pop = (event: PopStateEvent) => {
      if (isWorkflowPopOwned()) { event.stopImmediatePropagation(); return }
      if (committedPopEvents.has(event)) { remember(); return }
      if (restoring) { restoring = false; event.stopImmediatePropagation(); remember(); return }
      if (allowed()) { remember(); return }
      event.stopImmediatePropagation()
      const nextIndex = typeof event.state?.idx === 'number' ? event.state.idx as number : null
      if (index !== null && nextIndex !== null && index !== nextIndex) {
        restoring = true
        window.history.go(index - nextIndex)
      } else {
        // Unknown history entries cannot be reversed reliably; keep the editor
        // mounted and restore its URL rather than discard an in-flight upload.
        window.history.replaceState(historyState, '', href)
      }
    }
    const unregisterPop = registerPopHandler(pop, editorPops)
    return () => {
      installedGuards--
      if (navigator.push === guardedPush) navigator.push = push
      if (navigator.replace === guardedReplace) navigator.replace = replace
      if (navigator.go === guardedGo) navigator.go = go
      unregisterPop()
    }
  }, [context, enabled])
}
