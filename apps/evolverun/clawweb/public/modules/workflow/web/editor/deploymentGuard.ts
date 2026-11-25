// A browser-local warning, not a server lock: another browser or tab can still
// race this check. Never store deployment payloads or credentials here.
const prefix = 'clawweb.autoheal.review-blocked.v1.'
const keyFor = (workflowId: string) => prefix + encodeURIComponent(workflowId)
// Fail closed in this page if removal may have succeeded but cannot be verified.
const uncertainRemovals = new Map<string, string>()

export const deploymentBlockedMessage = '本浏览器存在未确认的部署，或无法访问浏览器存储；请人工核对 Git 和数据库，勿重试。此标记不阻止其他浏览器或标签页的部署。'
export const deploymentClearFailureMessage = '已确认人工核对，但无法清除浏览器核对标记；当前工作流仍保持 blocked。'

export type ManualReconciliation = { gitAndDatabaseConfirmed: true }

function removeDeploymentMarker(workflowId: string): void {
  localStorage.removeItem(keyFor(workflowId))
  if (localStorage.getItem(keyFor(workflowId)) !== null) throw new Error('remove failed')
}

export function assertDeploymentAvailable(workflowId: string): void {
  if (isEditorDeploymentPending(workflowId) || uncertainRemovals.has(workflowId)) throw new Error(deploymentBlockedMessage)
  try {
    if (localStorage.getItem(keyFor(workflowId)) !== null) throw new Error('blocked')
  } catch {
    throw new Error(deploymentBlockedMessage)
  }
}

// randomUUID is restricted to secure contexts; getRandomValues also works on
// supported HTTP origins. Tokens still require cryptographically strong entropy.
export function createDeploymentId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function markDeploymentPending(workflowId: string): string {
  assertDeploymentAvailable(workflowId)
  try {
    const marker = createDeploymentId()
    localStorage.setItem(keyFor(workflowId), marker)
    if (localStorage.getItem(keyFor(workflowId)) !== marker) throw new Error('write failed')
    return marker
  } catch {
    throw new Error('无法记录待核对标记，部署未发送；请检查浏览器存储。')
  }
}

// Only a verified pre-push rejection may clear the marker belonging to this attempt.
export function clearRejectedDeployment(workflowId: string, token: string): void {
  try {
    removeOwnedMarker(workflowId, token)
  } catch {
    throw new Error('部署已阻止，但无法清除本次浏览器核对标记；当前工作流仍保持 blocked。')
  }
}

// Call only after validating a successful response for the intended workflow.
export function clearConfirmedDeployment(workflowId: string): void {
  if (isEditorDeploymentPending(workflowId)) throw new Error('部署操作正在进行，不能清除核对标记。')
  try {
    removeDeploymentMarker(workflowId)
  } catch {
    throw new Error('部署已确认成功，但无法清除浏览器核对标记；后续部署仍受阻。')
  }
}

// Call only after a human has explicitly reconciled Git and the database for
// this workflow. Failure, HTTP errors, or an absent confirmation never unlock.
export function clearReconciledDeployment(workflowId: string, reconciliation: ManualReconciliation): void {
  if (isEditorDeploymentPending(workflowId)) throw new Error('部署操作正在进行，不能清除核对标记。')
  if (reconciliation.gitAndDatabaseConfirmed !== true) {
    throw new Error('请先确认已人工核对 Git 和数据库，才能清除当前工作流标记。')
  }
  const uncertain = uncertainRemovals.get(workflowId)
  if (uncertain) { clearEditorReconciledDeployment(workflowId, uncertain); return }
  try {
    removeDeploymentMarker(workflowId)
  } catch {
    throw new Error(deploymentClearFailureMessage)
  }
}

// In-memory only: survives component remounts in this tab, not reloads or other tabs.
// localStorage has no cross-tab compare-and-swap; these checks are not a lock.
const editorPending = new Map<string, string>()
const pendingObservers = new Set<() => void>()
let pendingRevision = 0
export const subscribeWorkflowDeploymentPending = (listener: () => void) => { pendingObservers.add(listener); return () => { pendingObservers.delete(listener) } }
export const workflowDeploymentPendingRevision = () => pendingRevision
function notifyPending(): void { pendingRevision++; pendingObservers.forEach(listener => listener()) }
export function isEditorDeploymentPending(workflowId: string): boolean {
  return editorPending.has(workflowId)
}
export function markEditorDeploymentPending(workflowId: string): string {
  if (isEditorDeploymentPending(workflowId)) throw new Error('部署操作正在进行，不能重复部署。')
  const marker = markDeploymentPending(workflowId)
  editorPending.set(workflowId, marker)
  notifyPending()
  return marker
}
export function finishEditorDeployment(workflowId: string, token: string): void {
  if (editorPending.get(workflowId) === token) { editorPending.delete(workflowId); notifyPending() }
}
// Shared by AutoHeal and Editor; aliases preserve existing callers.
export const isWorkflowDeploymentPending = isEditorDeploymentPending
export const markWorkflowDeploymentPending = markEditorDeploymentPending
export const finishWorkflowDeployment = finishEditorDeployment
export function readEditorDeploymentMarker(workflowId: string): string | null {
  return localStorage.getItem(keyFor(workflowId)) ?? uncertainRemovals.get(workflowId) ?? null
}
export function editorDeploymentMarkerMatches(workflowId: string, token: string): boolean {
  try { return !!token && readEditorDeploymentMarker(workflowId) === token } catch { return false }
}
// Check the current marker before removal; if verification after removal fails,
// preserve an in-memory quarantine rather than claiming success or retry safety.
function removeOwnedMarker(workflowId: string, token: string): void {
  if (!token || localStorage.getItem(keyFor(workflowId)) !== token) throw new Error('marker changed')
  try {
    localStorage.removeItem(keyFor(workflowId))
    if (localStorage.getItem(keyFor(workflowId)) !== null) throw new Error('remove failed')
  } catch (error) {
    uncertainRemovals.set(workflowId, token)
    throw error
  }
  uncertainRemovals.delete(workflowId)
}
export function clearAttemptConfirmedDeployment(workflowId: string, token: string): void {
  try { removeOwnedMarker(workflowId, token) }
  catch { throw new Error('部署已确认成功，但无法清除浏览器核对标记；后续部署仍受阻。') }
}
export const clearEditorConfirmedDeployment = clearAttemptConfirmedDeployment
export function clearEditorReconciledDeployment(workflowId: string, token: string): void {
  if (isEditorDeploymentPending(workflowId)) throw new Error('部署操作正在进行，不能清除核对标记。')
  try {
    const actual = localStorage.getItem(keyFor(workflowId))
    if (actual === null && uncertainRemovals.get(workflowId) === token && token) {
      uncertainRemovals.delete(workflowId)
      return
    }
    removeOwnedMarker(workflowId, token)
  } catch { throw new Error(deploymentClearFailureMessage) }
}
