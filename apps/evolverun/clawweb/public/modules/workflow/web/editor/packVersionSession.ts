import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { AutoHealDeployRequest, WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import type { PackVersionFile } from '../components/PackVersionEditor'

export type Version = 'current' | 'draft'
export type Snapshot = { workflowId: string; packId: string; packPath?: string; baseRef: string; baseCommit: string; draftHead: string | null; canWrite?: boolean; versionFiles: PackVersionFile[]; activeHistoryMismatch?: true }
export type Change = { path: string; operation: 'delete' } | ({ operation: 'create' | 'replace' } & PackVersionFile)
type VersionState = { snapshot: Snapshot | null; files: PackVersionFile[]; loading: boolean; error: string | null; readFailure?: string; blocked: boolean; notice: string | null }
export type SaveInput = { sourceVersion: Version; sourceCommit: string; expectedSavedHead: string | null; changes: Change[]; requestId?: string }
export type SaveResult = { branch: string; commit: string; requestId: string; version: number; packId: string }
export type SaveAttempt = { requestId: string; expectedPackId: string; snapshot: Snapshot; input: SaveInput; files: PackVersionFile[]; onSaved?: (result: SaveResult) => void; verifySaved?: (result: SaveResult) => Promise<void> }
type Reconciliation = {
  attempt: SaveAttempt
  token: object
  version: Version
  reason: 'server' | 'no-response' | 'unverified'
  identity: { branch: string; commit: string } | null
  attemptedFiles: PackVersionFile[]
  sourceCommit: string
  expectedSavedHead: string | null
  checking: boolean
  observation: { kind: 'head'; commit: string } | { kind: 'snapshot-not-found' } | null
  checkError: boolean
  operationState?: string
}
export type DeployAttempt = { input: AutoHealDeployRequest & { requestId: string; candidateCommit: string; packId: string }; pending: boolean; error: string | null }
export type CreateInput = { workflowId: string; packId: string; spec: WorkflowSpec; createOnly: true; requestId: string; expectedSavedHead: null }
export type CreateAttempt = { input: CreateInput; pending: boolean; phase: 'unknown' | 'failed' | 'conflict' | 'saved' | 'completed'; result?: SaveResult; error: string | null }
// null means no unfinished Pack identity write is known in this page session.
// A message survives snapshot reloads; clean Git files do not reconcile DB identity.
type State = { uncertainRename: string | null; postSaveVerificationError?: string | null; creation?: CreateAttempt; deployment?: DeployAttempt | null; version: Version; versions: Record<Version, VersionState>; saving: boolean; uploading: boolean; reconciliation: Reconciliation | null }
export type Session = { state: State; listeners: Set<() => void>; requests: Partial<Record<Version, AbortController>>; saveRequest?: object; deployRequest?: object; createRequest?: object; canvasDirty?: boolean; reconciliationRequest?: { controller: AbortController; timeout: ReturnType<typeof setTimeout> } }

const sessions = new Map<string, Session>()
/** Observation only: never create a session or affect its release lifecycle. */
export function hasPostSaveVerificationError(id: string): boolean {
  return !!sessions.get(id)?.state.postSaveVerificationError
}
let unloadGuardInstalled = false
let revision = 0
const observers = new Set<() => void>()
export const subscribePackVersions = (listener: () => void) => { observers.add(listener); return () => { observers.delete(listener) } }
export const packVersionsRevision = () => revision
function notify() { revision++; observers.forEach(listener => listener()) }
export function hasPackVersionEditRisk(id: string | null): boolean {
  const session = id ? sessions.get(id) : undefined
  return !!session && (session.state.uncertainRename !== null || !!session.state.postSaveVerificationError || session.state.saving || session.state.uploading || session.state.reconciliation !== null || Object.values(session.state.versions).some(v => dirty(v) || v.blocked))
}
export function hasPackVersionRisk(id: string | null): boolean {
  const state = id ? sessions.get(id)?.state : undefined
  return hasPackVersionEditRisk(id) || !!state?.deployment || !!(state?.creation && state.creation.phase !== 'completed' && state.creation.phase !== 'conflict')
}
export function hasPackVersionUpload(id: string | null): boolean { return !!(id && sessions.get(id)?.state.uploading) }
export function hasAnyPackVersionUpload(): boolean { return [...sessions.values()].some(s => s.state.uploading) }
export function hasAnyPackVersionRisk(): boolean { return [...sessions.keys()].some(hasPackVersionRisk) }
export const oid = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/
const empty = (): VersionState => ({ snapshot: null, files: [], loading: false, error: null, blocked: false, notice: null })

/** Capability is checked against the read snapshot, never inferred from the public host. */
export async function putPackVersion(id: string, input: SaveInput): Promise<Partial<SaveResult>> {
  // fetchJson gzips string bodies over 14 KiB; this route requires uncompressed JSON.
  const body = new Blob([JSON.stringify(input)], { type: 'application/json' })
  return fetchJson(`/api/workflows/${encodeURIComponent(id)}/pack-version`, {
    method: 'PUT', headers: { 'X-Pack-Version-CSRF': '1' }, body,
  })
}

// Do not treat a proxy's generic 404, HTML page, or error message as a snapshot result.
function parseErrorBody(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'string' || value.length > 8192) return null
  try {
    const parsed: unknown = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null
  } catch { return null }
}

/** Treat an error identity as evidence only for the exact documented response. */
export function classifySaveFailure(error: unknown, id: string, requestId?: string): {
  conflict: boolean
  preOperationConflict: boolean
  originRejected: boolean
  reason: Reconciliation['reason']
  identity: Reconciliation['identity']
} {
  const value = error as { status?: unknown; body?: unknown } | null
  const body = parseErrorBody(value?.body)
  // These responses mean the server rejected the request before creating the
  // current operation. Never reconcile them: REQUEST_CONFLICT can reference a
  // different requestId, and WORKFLOW_BUSY has no current operation to observe.
  if (value?.status === 409 && (body?.code === 'WORKFLOW_BUSY' || body?.code === 'REQUEST_CONFLICT')) {
    return { conflict: false, preOperationConflict: true, originRejected: false, reason: 'server', identity: null }
  }
  if (requestId && body?.requestId !== undefined && body.requestId !== requestId) return { conflict: false, preOperationConflict: false, originRejected: false, reason: 'unverified', identity: null }
  // Only this exact guard response proves this PUT was rejected before writes.
  // A present, mismatched requestId above is still unverified.
  if (value?.status === 403 && body?.code === 'ORIGIN_NOT_ALLOWED') {
    return { conflict: false, preOperationConflict: false, originRejected: true, reason: 'server', identity: null }
  }
  if (value?.status === 409 && body?.code === 'VERSION_CONFLICT') return { conflict: true, preOperationConflict: false, originRejected: false, reason: 'server', identity: null }
  if (value?.status === 503 && body?.code === 'SAVE_OUTCOME_UNKNOWN' && body.branch === `wf/${id}` && typeof body.commit === 'string' && oid.test(body.commit)) {
    return { conflict: false, preOperationConflict: false, originRejected: false, reason: 'server', identity: { branch: body.branch, commit: body.commit } }
  }
  return { conflict: false, preOperationConflict: false, originRejected: false, reason: typeof value?.status === 'number' ? 'unverified' : 'no-response', identity: null }
}

export const RECONCILIATION_HEAD_TIMEOUT_MS = 15_000

/** Cancel promptly, even when a transport ignores AbortSignal. Late results cannot commit. */
export function cancelReconciliationHead(session: Session) {
  const request = session.reconciliationRequest
  if (!request) return
  session.reconciliationRequest = undefined
  clearTimeout(request.timeout)
  request.controller.abort()
  const latest = session.state.reconciliation
  if (latest?.checking) publish(session, { ...session.state, reconciliation: { ...latest, checking: false, observation: null, checkError: true } })
}

/** Observe the current draft head without replacing the candidate or resolving the attempt. */
export async function observeReconciliationHead(session: Session, id: string) {
  const attempt = session.state.reconciliation
  if (!attempt || attempt.checking || session.state.saving || session.state.uploading) return
  const controller = new AbortController()
  const request = { controller, timeout: setTimeout(() => controller.abort(), RECONCILIATION_HEAD_TIMEOUT_MS) }
  session.reconciliationRequest = request
  publish(session, { ...session.state, reconciliation: { ...attempt, checking: true, observation: null, checkError: false } })
  // The race makes the operation bounded even for non-conforming fetch implementations.
  const aborted = new Promise<never>((_resolve, reject) => {
    controller.signal.addEventListener('abort', () => reject(new Error('Observation cancelled')), { once: true })
  })
  try {
    const result = await Promise.race([fetchJson<unknown>(`/api/workflows/${encodeURIComponent(id)}/pack-version?version=draft`, { signal: controller.signal }), aborted])
    const snapshot = validateSnapshot(result, id, 'draft')
    const latest = session.state.reconciliation
    if (session.reconciliationRequest !== request || !latest || latest.token !== attempt.token) return
    publish(session, { ...session.state, reconciliation: { ...latest, checking: false, observation: snapshot.draftHead === null ? { kind: 'snapshot-not-found' } : { kind: 'head', commit: snapshot.draftHead }, checkError: false } })
  } catch (error) {
    const latest = session.state.reconciliation
    if (session.reconciliationRequest !== request || !latest || latest.token !== attempt.token) return
    const response = error as { status?: unknown; body?: unknown } | null
    const notFound = !controller.signal.aborted && response?.status === 404 && parseErrorBody(response.body)?.code === 'SNAPSHOT_NOT_FOUND'
    publish(session, { ...session.state, reconciliation: { ...latest, checking: false,
      observation: notFound ? { kind: 'snapshot-not-found' } : null, checkError: !notFound } })
  } finally {
    clearTimeout(request.timeout)
    if (session.reconciliationRequest === request) session.reconciliationRequest = undefined
  }
}

export function sessionFor(id: string): Session {
  if (!unloadGuardInstalled && typeof window !== 'undefined') {
    window.addEventListener('beforeunload', event => {
      if (hasAnyPackVersionRisk()) {
        event.preventDefault()
        event.returnValue = ''
      }
    })
    unloadGuardInstalled = true
  }
  let session = sessions.get(id)
  if (!session) {
    session = { state: { uncertainRename: null, version: 'draft', versions: { current: empty(), draft: empty() }, saving: false, uploading: false, reconciliation: null }, listeners: new Set(), requests: {} }
    sessions.set(id, session)
  }
  return session
}
export function publish(session: Session, state: State) {
  session.state = state
  notify()
  session.listeners.forEach(listener => listener())
}
export function updateVersion(session: Session, version: Version, patch: Partial<VersionState>) {
  publish(session, { ...session.state, versions: { ...session.state.versions, [version]: { ...session.state.versions[version], ...patch } } })
}
export function diff(original: PackVersionFile[], files: PackVersionFile[]): Change[] {
  const before = new Map(original.map(file => [file.path, file]))
  const after = new Map(files.map(file => [file.path, file]))
  const changes: Change[] = []
  for (const file of original) if (!after.has(file.path)) changes.push({ path: file.path, operation: 'delete' })
  for (const file of files) {
    const old = before.get(file.path)
    if (!old || old.contentBase64 !== file.contentBase64 || old.mode !== file.mode) changes.push({ operation: old ? 'replace' : 'create', ...file })
  }
  return changes
}
export function dirty(state: VersionState): boolean {
  return !!state.snapshot && diff(state.snapshot.versionFiles, state.files).length > 0
}
/** current is DB-active production; draft is only the writable wf tip. */
export function canonicalPackPath(value: unknown): string | null {
  if (typeof value !== 'string') return null
  if (value === '') return ''
  const normalized = value.replace(/\/+$/, '')
  if (!normalized || normalized.split('/').some(part => !part || part === '.' || part === '..' || part.toLowerCase() === '.git')) return null
  return normalized
}

/**
 * A root Pack may contain a historical nested `packs/<packId>` tree as an
 * ordinary resource. Treat that explicit root/nested transition as the same
 * workflow Pack scope for review; the server still validates the immutable
 * candidate tree before publishing it.
 */
export function compatiblePackScope(left: Pick<Snapshot, 'packId' | 'packPath'>, right: Pick<Snapshot, 'packId' | 'packPath'>): boolean {
  if (left.packId !== right.packId || left.packPath === right.packPath) return left.packId === right.packId
  if (typeof left.packPath !== 'string' || typeof right.packPath !== 'string') return false
  return (left.packPath === '' && right.packPath === `packs/${left.packId}`)
    || (right.packPath === '' && left.packPath === `packs/${right.packId}`)
}

function validSnapshotRef(ref: unknown, id: string, version: Version): boolean {
  if (typeof ref !== 'string') return false
  if (version === 'draft') return ref === `origin/wf/${id}`
  const prefix = `refs/tags/deploy/${id}/#`
  if (!ref.startsWith(prefix)) return false
  const number = ref.slice(prefix.length), value = Number(number)
  return Number.isSafeInteger(value) && value > 0 && String(value) === number
}
export function validateSnapshot(value: unknown, id: string, version: Version): Snapshot {
  if (!value || typeof value !== 'object') throw new Error('Invalid response')
  const data = value as Snapshot
  const packPath = data.packPath === undefined ? undefined : canonicalPackPath(data.packPath)
  if (data.workflowId !== id || typeof data.packId !== 'string' || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(data.packId) || data.packId.includes('..') || data.packId.endsWith('.') || data.packId.endsWith('.lock') ||
      (data.packPath !== undefined && packPath === null) ||
      !validSnapshotRef(data.baseRef, id, version) ||
      typeof data.baseCommit !== 'string' || !oid.test(data.baseCommit) ||
      !(data.draftHead === null || (typeof data.draftHead === 'string' && oid.test(data.draftHead))) ||
      (version === 'draft' && data.baseCommit !== data.draftHead) || !Array.isArray(data.versionFiles)) throw new Error('Invalid response')
  const paths = new Set<string>()
  for (const file of data.versionFiles) {
    if (!file || typeof file.path !== 'string' || !file.path || file.path.split('/').some(p => !p || p === '.' || p === '..' || p.toLowerCase() === '.git') ||
        /[\\\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/u.test(file.path) || /^[A-Za-z]:/.test(file.path) ||
        typeof file.contentBase64 !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(file.contentBase64) ||
        (file.mode !== '100644' && file.mode !== '100755') || paths.has(file.path)) throw new Error('Invalid file')
    try { if (btoa(atob(file.contentBase64)) !== file.contentBase64) throw new Error('Invalid base64') } catch { throw new Error('Invalid base64') }
    paths.add(file.path)
  }
  for (const path of paths) {
    const parts = path.split('/')
    for (let i = 1; i < parts.length; i++) if (paths.has(parts.slice(0, i).join('/'))) throw new Error('Conflicting paths')
  }
  const activeHistoryMismatch = version === 'current' && data.activeHistoryMismatch === true ? { activeHistoryMismatch: true as const } : {};
  const result: Snapshot = { ...data, ...(packPath === undefined ? {} : { packPath }), ...activeHistoryMismatch, versionFiles: data.versionFiles.map(file => ({ path: file.path, contentBase64: file.contentBase64, mode: file.mode })) };
  if (!(version === 'current' && data.activeHistoryMismatch === true)) delete result.activeHistoryMismatch;
  return result
}
function readError(error: unknown): string {
  const value = error as { status?: unknown; body?: unknown } | null
  const status = value?.status
  const body = parseErrorBody(value?.body)
  if (status === 401) return '请先登录，再查看版本文件。'
  if (status === 403) return '没有权限或请求来源不被允许，无法查看版本文件。'
  if (status === 404) return '版本或接口不可用，请检查服务版本和对应快照。'
  if (status === 409 && body?.code === 'SNAPSHOT_CONFLICT') return '同一工作流匹配到多个 Pack，或 Pack 布局存在冲突；暂无法安全读取，请先整理重复的 Pack 或冲突的目录布局，再重新读取。'
  if (status === 409 && body?.code === 'VERSION_CONFLICT') return '版本已发生变化，请刷新或重新读取后再继续。'
  if (status === 503) return '版本服务暂不可用，请稍后重试。'
  return '无法读取完整版本文件，服务响应可能不兼容，请稍后重试。'
}
export function load(session: Session, id: string, version: Version) {
  if (session.state.saving || session.state.uploading || session.state.postSaveVerificationError || session.state.reconciliation?.version === version) return
  session.requests[version]?.abort()
  const controller = new AbortController()
  session.requests[version] = controller
  updateVersion(session, version, { loading: true, error: null })
  void fetchJson<unknown>(`/api/workflows/${encodeURIComponent(id)}/pack-version?version=${version}`, { signal: controller.signal })
    .then(value => {
      if (controller.signal.aborted) return
      const snapshot = validateSnapshot(value, id, version)
      updateVersion(session, version, { snapshot, files: snapshot.versionFiles.map(file => ({ ...file })), loading: false, error: null, readFailure: undefined, blocked: false, notice: null })
    }).catch(error => {
      if (!controller.signal.aborted) {
        const message = readError(error)
        updateVersion(session, version, { loading: false, error: message, readFailure: message })
      }
    }).finally(() => { if (session.requests[version] === controller) delete session.requests[version] })
}

export function releaseCleanSession(id: string, session: Session) {
  if (sessions.get(id) === session && session.listeners.size === 0 && !session.canvasDirty && !hasPackVersionRisk(id)) sessions.delete(id)
}

/** Creation may publish only its own new Pack; unrelated draft changes still invalidate navigation approval. */
export function checkpointOtherPackSessions(exceptId: string): () => boolean {
  const before = new Map([...sessions].filter(([id]) => id !== exceptId).map(([id, session]) => [id, session.state]))
  return () => {
    const after = [...sessions].filter(([id]) => id !== exceptId)
    return after.length === before.size && after.every(([id, session]) => before.get(id) === session.state)
  }
}
