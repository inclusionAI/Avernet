import { useMutation, useQueryClient, type MutateOptions, type UseMutationResult } from '@tanstack/react-query'
import { assertAutoHealUnifiedReview, type AutoHealUnifiedBaseline } from './autoHealUnifiedBaseline'
import { readLegacyAutoHealResponse } from './autoHealLegacyTransport'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import { hasPostSaveVerificationError } from '../editor/packVersionSession'
import { abandonEditorWorkflowWrite, assertEditorWorkflowReadable, beginEditorWorkflowWrite, editorWorkflowReadIsCurrent, editorWorkflowWriteState, finishEditorWorkflowWrite } from '../editor/workflowWriteGuard'
import type {
  AutoHealSaveRequest, AutoHealSaveResult, AutoHealDeployRequest, AutoHealDeployResult,
} from '@avernet/clawweb-shared/web/types'

export interface WorkflowAutoHealSaveRequest extends AutoHealSaveRequest {
  requestId?: string
  unifiedBaseline?: AutoHealUnifiedBaseline
  createOnly?: boolean
  sourceCommit?: string
  expectedSavedHead?: string | null
}
/** Ordinary saves attest DB plus the exact wf target; unified keeps its existing receipt shape. */
type WorkflowAutoHealGitSaveResult = AutoHealSaveResult & { branch: `wf/${string}`; commit: string }
export type WorkflowAutoHealSaveResult = (
  | WorkflowAutoHealGitSaveResult & { unified?: false }
  | AutoHealSaveResult & { unified: true; commit: string; draftToken: string }
) & { requestId?: string }

export interface WorkflowAutoHealDeployRequest extends AutoHealDeployRequest {
  requestId?: string
  candidateCommit?: string
  expectedDraftToken?: string
  snapshotProtocol?: 'saved-tree-v2'
  expectedActiveCommit?: string
  confirmedFileDiff?: true
  sourceFlowId?: string | null
}

export const postSaveLockMessage = 'Git 文件已保存，但数据库后置核对失败；本页工作流已锁定。请先人工核对数据库和 Git，不能使用 AutoHeal 保存或部署。此保护不覆盖其他标签页、刷新或其他客户端。'
export class PostSaveLockError extends Error {
  constructor() { super(postSaveLockMessage); this.name = 'PostSaveLockError' }
}

/** A guard rejection before fetch is proof that no deployment was sent. */
export class AutoHealWriteBlockedError extends Error {
  constructor(cause: unknown) {
    super(cause instanceof Error ? cause.message : '数据库写入状态不可用；请求未发送。')
    this.name = 'AutoHealWriteBlockedError'
  }
}

const editorDeploymentEpochs = new WeakMap<WorkflowAutoHealDeployRequest, { workflowId: string; epoch: number }>()
const unsentWrites = new WeakMap<object, { input: object; operation: 'save' | 'deploy' | 'editor-deploy' }>()
const savedBaselines = new WeakMap<object, { workflowId: string; packId: string; epoch: number }>()
const autoDeployBaselines = new WeakMap<object, { workflowId: string; packId: string; epoch: number }>()
const snapshotDeployInputs = new WeakMap<object, string>()
const unifiedReceipts = new WeakMap<object, { commit: string; draftToken: string }>()
export const unifiedDeployBlockedMessage = '统一保存后的旧部署接口无法验证草稿 token，可能发布其他草稿；为安全起见部署未发送。请使用支持草稿基线验证的部署流程。'
export const autoHealSavedBaselineChangedMessage = '保存后数据库写入状态已变化；部署未发送。请先重新核对草稿并重新诊断。此保护仅覆盖当前标签页。'

/** Only an acknowledged save from this transport can supply a deployment baseline. */
export function autoHealSaveIsCurrent(receipt: AutoHealSaveResult): boolean {
  const saved = receipt && savedBaselines.get(receipt)
  return !!saved && saved.workflowId === receipt.workflowId && saved.packId === receipt.packId && editorWorkflowReadIsCurrent(saved.workflowId, saved.epoch)
}
export function bindAutoHealDeployToSave(input: WorkflowAutoHealDeployRequest, receipt: AutoHealSaveResult): void {
  const saved = receipt && savedBaselines.get(receipt)
  if (!saved || saved.workflowId !== input.workflowId || saved.packId !== input.packId || receipt.workflowId !== saved.workflowId || receipt.packId !== saved.packId) {
    throw blockedBeforeSend('deploy', input, new Error(autoHealSavedBaselineChangedMessage))
  }
  const unified = unifiedReceipts.get(receipt)
  if (unified && (input.snapshotProtocol !== 'saved-tree-v2' || input.candidateCommit !== unified.commit
    || input.expectedDraftToken !== unified.draftToken || input.confirmedFileDiff !== true
    || typeof input.expectedActiveCommit !== 'string' || !/^(?:|[a-f0-9]{40}|[a-f0-9]{64})$/.test(input.expectedActiveCommit))) {
    throw blockedBeforeSend('deploy', input, new Error('部署请求与已保存快照或审阅基线不匹配；请求未发送。'))
  }
  if (!unified && input.snapshotProtocol !== undefined) throw blockedBeforeSend('deploy', input, new Error('缺少统一保存回执；请求未发送。'))
  autoDeployBaselines.set(input, saved)
  if (unified) snapshotDeployInputs.set(input, JSON.stringify(input))
}


/** Only transport-confirmed unified receipts provide these immutable identities. */
export function autoHealUnifiedSaveIdentity(receipt: AutoHealSaveResult): Readonly<{ commit: string; draftToken: string }> | null {
  const identity = unifiedReceipts.get(receipt)
  return identity ? { ...identity } : null
}

/** Bind the exact editor-deploy input object, not its serialized request body. */
export function bindEditorDeploymentWriteEpoch(input: WorkflowAutoHealDeployRequest, epoch: number): void {
  editorDeploymentEpochs.set(input, { workflowId: input.workflowId, epoch })
}

function blockedBeforeSend(operation: 'save' | 'deploy' | 'editor-deploy', input: object, cause: unknown): AutoHealWriteBlockedError {
  const error = new AutoHealWriteBlockedError(cause)
  unsentWrites.set(error, { input, operation })
  return error
}
export function isAutoHealWriteNotSent(error: unknown, input: object): boolean {
  return !!error && typeof error === 'object' && unsentWrites.get(error)?.input === input
}
export function isEditorDeploymentNotSent(error: unknown, input: WorkflowAutoHealDeployRequest): boolean {
  return !!error && typeof error === 'object' && unsentWrites.get(error)?.input === input && unsentWrites.get(error)?.operation === 'editor-deploy'
}


type AutoOperation = 'save' | 'deploy'
type AutoInput = WorkflowAutoHealSaveRequest | WorkflowAutoHealDeployRequest
// Each invocation gets its own shallow copy and epoch, including when a caller
// submits the same object twice. The copy has exactly the original JSON fields.
const autoSubmissions = new WeakMap<object, { input: AutoInput; workflowId: string; epoch?: number; expectedPackId?: string; error?: unknown }>()
function captureAutoSubmission<T extends AutoInput>(input: T, operation: AutoOperation): T {
  const workflowId = input.workflowId
  let epoch: number | undefined, expectedPackId: string | undefined, error: unknown
  try {
    const currentEpoch = assertEditorWorkflowReadable(workflowId)
    const saved = operation === 'deploy' ? autoDeployBaselines.get(input) : undefined
    epoch = saved ? saved.epoch : currentEpoch
    expectedPackId = saved?.packId
    if (saved && (saved.workflowId !== workflowId || saved.packId !== input.packId)) error = new Error(autoHealSavedBaselineChangedMessage)
    if (operation === 'deploy' && (snapshotDeployInputs.has(input) || (input as WorkflowAutoHealDeployRequest).snapshotProtocol !== undefined)
      && snapshotDeployInputs.get(input) !== JSON.stringify(input)) error = new Error('快照部署确认已变化；请求未发送。')
  } catch (cause) { error = cause }
  const copy = { ...input }
  autoSubmissions.set(copy, { input, workflowId, epoch, expectedPackId, error })
  return copy
}
function assertAutoSubmission(operation: AutoOperation, input: AutoInput): void {
  const submitted = autoSubmissions.get(input)
  const original = submitted?.input ?? input
  if (!submitted) throw blockedBeforeSend(operation, original, new Error('缺少写入状态基线；请求未发送。'))
  if (submitted.error) throw blockedBeforeSend(operation, original, submitted.error)
  if (input.workflowId !== submitted.workflowId || (submitted.expectedPackId !== undefined && input.packId !== submitted.expectedPackId) || submitted.epoch === undefined || !editorWorkflowReadIsCurrent(submitted.workflowId, submitted.epoch)) {
    try { assertEditorWorkflowReadable(submitted.workflowId) }
    catch (error) { throw blockedBeforeSend(operation, original, error) }
    throw blockedBeforeSend(operation, original, new Error('数据库写入状态在提交后已变化；请求未发送，请重新核对。'))
  }
}

// Preserve the public mutation variables and callbacks while passing a unique
// per-invocation object to React Query (whose onMutate may suspend execution).
function withAutoSubmission<TData, TInput extends AutoInput>(mutation: UseMutationResult<TData, Error, TInput>, operation: AutoOperation) {
  function optionsFor(input: TInput, options?: MutateOptions<TData, Error, TInput>) {
    if (!options) return undefined
    return {
      ...options,
      onSuccess: options.onSuccess && ((data, _variables, context, mutationContext) => options.onSuccess!(data, input, context, mutationContext)),
      onError: options.onError && ((error, _variables, context, mutationContext) => options.onError!(error, input, context, mutationContext)),
      onSettled: options.onSettled && ((data, error, _variables, context, mutationContext) => options.onSettled!(data, error, input, context, mutationContext)),
    } satisfies MutateOptions<TData, Error, TInput>
  }
  return {
    ...mutation,
    variables: mutation.variables ? (autoSubmissions.get(mutation.variables)?.input as TInput ?? mutation.variables) : undefined,
    mutate: (input: TInput, options?: MutateOptions<TData, Error, TInput>) => mutation.mutate(captureAutoSubmission(input, operation), optionsFor(input, options)),
    mutateAsync: (input: TInput, options?: MutateOptions<TData, Error, TInput>) => mutation.mutateAsync(captureAutoSubmission(input, operation), optionsFor(input, options)),
  }
}

// Only a response to this exact deployment request can authorize unlocking it.
const rejectedDeployments = new WeakMap<object, { workflowId: string; requestId?: string }>()
export function isDeployPrePushConflict(error: unknown, input: { workflowId: string; requestId?: string }): boolean {
  if (!error || typeof error !== 'object') return false
  const rejected = rejectedDeployments.get(error)
  return !!rejected && rejected.workflowId === input.workflowId && rejected.requestId === input.requestId
}

function nativeResponseStatus(response: Response): number {
  if (!(response instanceof Response)) throw new Error('写入响应无效，结果未知。')
  // Brand-check the native object and ignore instance-level overrides.
  return Object.getOwnPropertyDescriptor(Response.prototype, 'status')!.get!.call(response) as number
}

/** Match only the enterprise /save protocol's pre-write responses. A generic
 * HTTP error, an unreadable body, or a create-only service failure is not proof
 * that a DB/Git write did not happen. */
function legacySavePrewriteRejection(status: number, text: string, createOnly: boolean): boolean {
  if (text.length > 8192) return false
  let value: unknown
  try { value = JSON.parse(text) } catch { return false }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const body = value as Record<string, unknown>
  const exact = (expected: Record<string, string>) => Object.keys(body).length === Object.keys(expected).length
    && Object.entries(expected).every(([key, item]) => body[key] === item)
  if (status === 401) return exact({ error: 'Unauthorized', message: 'Authentication required' })
  if (status === 403 && (exact({ error: 'Origin not allowed', code: 'ORIGIN_NOT_ALLOWED' })
    || exact({ error: 'Request rejected', code: 'CSRF_REJECTED' }))) return true
  if (status === 400 && (exact({ error: 'Bad Request', message: 'workflowId and spec are required' })
    || exact({ error: 'Bad Request', message: 'createOnly must be boolean' })
    || exact({ error: 'Bad Request', message: 'Invalid request body' })
    || (body.error === 'Bad Request' && body.code === 'INVALID_REQUEST' && Object.keys(body).length === 3
      && ['expectedDraftAbsent must be true and cannot be combined with createOnly',
        'expectedActiveDeployNumber is required for expectedDraftAbsent',
        'expectedActiveDeployNumber must be a positive integer with expectedDraftAbsent=true'].includes(String(body.message))))) return true
  if (createOnly) return false
  if (status === 403 && exact({ error: 'Forbidden', message: 'No edit permission for this workflow' })) return true
  if (body.error !== 'Request Failed' || body.message !== 'Auto-heal request failed' || Object.keys(body).length !== 3) return false
  return status === 403 && body.code === 'FORBIDDEN' || status === 400 && typeof body.code === 'string'
    && ['INVALID_REQUEST', 'INVALID_WORKFLOW', 'WORKFLOW_ID_MISMATCH', 'UNSUPPORTED_GIT_PRECONDITION'].includes(body.code)
}

/** The trusted host requires CSRF and uncompressed JSON. */
async function postAutoHealWrite<T>(operation: 'save' | 'deploy' | 'editor-deploy', input: WorkflowAutoHealSaveRequest | WorkflowAutoHealDeployRequest): Promise<T> {
  // React Query may defer mutation execution after the click. Check at the
  // transport boundary, before serialization or any network request. Editor
  // deployment has its own session guard and outcome handling.
  const workflowId = input.workflowId
  if (operation !== 'editor-deploy' && hasPostSaveVerificationError(workflowId)) throw new PostSaveLockError()
  if (operation !== 'editor-deploy') assertAutoSubmission(operation, input)
  if (operation === 'save' && (input as WorkflowAutoHealSaveRequest).unifiedBaseline) {
    return postUnifiedAutoHealSave<T>(input as WorkflowAutoHealSaveRequest)
  }
  const body = new Blob([JSON.stringify(input)], { type: 'application/json' })
  if (operation === 'save') {
    const expectedPackId = input.packId
    let owner: string
    assertAutoSubmission(operation, input)
    try { owner = beginEditorWorkflowWrite(workflowId) }
    catch (error) { throw blockedBeforeSend(operation, autoSubmissions.get(input)?.input ?? input, error) }
    try {
      const user = getClientUser()
      const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Pack-Version-CSRF': '1' }
      if (user) { headers['X-User-Id'] = user.userId; headers['X-User-Name'] = encodeURIComponent(user.nickName) }
      const { status, text, bodyRead } = await readLegacyAutoHealResponse('/api/auto-heal/save', { method: 'POST', cache: 'no-store', headers, body }, 'save')
      if (status < 200 || status >= 300) {
        const error = Object.assign(new Error(`API ${status}: ${text}`), { status, body: text })
        const createOnly = (input as WorkflowAutoHealSaveRequest).createOnly === true
        // Observe a real response, not an exception from fetch. Create-only may
        // write DB and Git, so only its host-level pre-write guards are eligible.
        let prewrite = bodyRead && legacySavePrewriteRejection(status, text, createOnly)
        if (!prewrite && !createOnly && bodyRead && status === 409 && text.length <= 8192) {
          let parsed: unknown
          try { parsed = JSON.parse(text) } catch { /* malformed response is uncertain */ }
          prewrite = !!parsed && typeof parsed === 'object' && !Array.isArray(parsed) && (parsed as { code?: unknown }).code === 'DRAFT_CONFLICT'
        }
        if (prewrite) finishEditorWorkflowWrite(workflowId, owner)
        throw error
      }
      if (!bodyRead) throw new Error('保存响应不可读取，结果未知；请人工核对，勿重试。')
      const result = JSON.parse(text) as WorkflowAutoHealGitSaveResult
      // A malformed or mismatched acknowledgement is not proof of a terminal
      // write, even if it arrived with an HTTP success status.
      if (!result || result.ok !== true || result.saved !== true || result.workflowId !== workflowId
        || typeof result.packId !== 'string' || !result.packId.trim() || (expectedPackId != null && result.packId !== expectedPackId)
        || result.branch !== `wf/${workflowId}` || typeof result.commit !== 'string'
        || ![40, 64].includes(result.commit.length) || /[^a-f0-9]/.test(result.commit)) {
        abandonEditorWorkflowWrite(workflowId, owner)
        throw new Error('保存结果无效或工作流不匹配，写入结果未知；请人工核对，勿重试。')
      } else {
        // finish advances the pending epoch exactly once before notifying listeners.
        // Capture it first: listeners or mutation callbacks may run another write.
        const completedEpoch = editorWorkflowWriteState(workflowId).epoch + 1
        finishEditorWorkflowWrite(workflowId, owner)
        savedBaselines.set(result, { workflowId, packId: result.packId, epoch: completedEpoch })
      }
      return result as T
    } catch (error) {
      abandonEditorWorkflowWrite(workflowId, owner)
      throw error
    }
  }

  const { requestId } = input as WorkflowAutoHealDeployRequest
  // Inspect the response itself, never an exception thrown by fetch: a network
  // error can have arbitrary properties and is not evidence of a rejection.
  const user = getClientUser()
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Pack-Version-CSRF': '1' }
  if (user) { headers['X-User-Id'] = user.userId; headers['X-User-Name'] = encodeURIComponent(user.nickName) }
  if (operation === 'deploy') {
    assertAutoSubmission(operation, input)
    if ((input as WorkflowAutoHealDeployRequest).snapshotProtocol !== 'saved-tree-v2') return postLegacyAutoHealDeploy<T>(input as WorkflowAutoHealDeployRequest, headers, body)
  }
  if (operation === 'editor-deploy') {
    const binding = editorDeploymentEpochs.get(input as WorkflowAutoHealDeployRequest)
    if (!binding || binding.workflowId !== workflowId || !editorWorkflowReadIsCurrent(workflowId, binding.epoch)) {
      throw blockedBeforeSend(operation, input, new Error('数据库写入状态已变化或缺少部署基线；部署未发送，请重新核对。'))
    }
  }
  const endpoint = (input as WorkflowAutoHealDeployRequest).snapshotProtocol === 'saved-tree-v2'
    ? 'editor-deploy/snapshot-v2' : operation
  const response = await fetch(`/api/auto-heal/${endpoint}`, { method: 'POST', cache: 'no-store', headers, body })
  const status = nativeResponseStatus(response)
  if (status < 200 || status >= 300) {
    let bodyRead = false
    let text: string
    try { text = await Response.prototype.text.call(response); bodyRead = true } catch { text = response.statusText }
    const error = Object.assign(new Error(`API ${status}: ${text}`), { status, body: text })
    if (bodyRead && status === 409 && text.length <= 8192) {
      try {
        const parsed: unknown = JSON.parse(text)
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          const value = parsed as { code?: unknown; requestId?: unknown }
          if ((value.code === 'DEPLOY_TREE_CONFLICT' || (operation === 'editor-deploy' || (input as WorkflowAutoHealDeployRequest).snapshotProtocol === 'saved-tree-v2') && (value.code === 'DEPLOY_BASELINE_MISSING' || value.code === 'DRAFT_CONFLICT'
            || (input as WorkflowAutoHealDeployRequest).snapshotProtocol === 'saved-tree-v2' && ['SNAPSHOT_DB_MISMATCH', 'CANDIDATE_NOT_SAVED', 'SAVE_REQUIRED', 'DEPLOY_BASELINE_STALE'].includes(String(value.code)))) && typeof requestId === 'string' && requestId.length > 0 && value.requestId === requestId) {
            rejectedDeployments.set(error, { workflowId, requestId })
            error.message = value.code === 'DRAFT_CONFLICT' ? '数据库草稿已变化或不存在，请重新读取并确认部署。'
              : value.code === 'SNAPSHOT_DB_MISMATCH' ? '数据库草稿与待部署的 Git 工作流不一致或无法安全比较。为避免运行内容和部署内容不一致，请先保存当前内容，再重新部署。保存可能立即影响使用数据库草稿的运行端。'
              : '当前生效 tag 缺失或候选快照存在文件缺失、模式或受保护文件内容冲突，已阻止部署。请联系有权限的维护人员核对；未自动合并或恢复。'
          }
        }
      } catch { /* an unrecognized response remains uncertain */ }
    }
    throw error
  }
  return Response.prototype.json.call(response) as Promise<T>
}

/** Legacy deployment has no token contract; timeout must retain the caller's marker. */
async function postLegacyAutoHealDeploy<T>(input: WorkflowAutoHealDeployRequest, headers: Record<string, string>, body: Blob): Promise<T> {
  const { status, text, bodyRead } = await readLegacyAutoHealResponse('/api/auto-heal/deploy', { method: 'POST', cache: 'no-store', headers, body }, 'deploy')
  if (status < 200 || status >= 300) {
    const error = Object.assign(new Error(`API ${status}: ${text}`), { status, body: text })
    if (bodyRead && status === 409 && text.length <= 8192) {
      try {
        const value: unknown = JSON.parse(text)
        if (value && typeof value === 'object' && !Array.isArray(value)) {
          const reply = value as { code?: unknown; requestId?: unknown }
          if (reply.code === 'DEPLOY_TREE_CONFLICT' && typeof input.requestId === 'string' && input.requestId.length > 0 && reply.requestId === input.requestId) {
            rejectedDeployments.set(error, { workflowId: input.workflowId, requestId: input.requestId })
            error.message = '当前生效 tag 缺失或候选快照存在文件缺失、模式或受保护文件内容冲突，已阻止部署。请联系有权限的维护人员核对；未自动合并或恢复。'
          }
        }
      } catch { /* malformed responses are uncertain */ }
    }
    throw error
  }
  if (!bodyRead) throw new Error('部署响应不可读取，结果未知；请人工核对，勿重试。')
  return JSON.parse(text) as T
}

/** Unified save has no journal or retry. Only a complete, correlated receipt is
 * success; uncertain and partial results leave the existing write guard locked. */
async function postUnifiedAutoHealSave<T>(input: WorkflowAutoHealSaveRequest): Promise<T> {
  const original = autoSubmissions.get(input)?.input ?? input
  const baseline = input.unifiedBaseline!
  let body: Blob
  try {
    assertAutoSubmission('save', input)
    const reviewedSpec = assertAutoHealUnifiedReview(baseline, input.workflowId, input.spec)
    if (input.createOnly || !input.requestId || !/^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/.test(input.requestId)
      || input.spec.id !== input.workflowId || (input.packId != null && input.packId !== baseline.packId)) throw new Error('统一保存请求或工作流身份无效；请求未发送。')
    body = new Blob([JSON.stringify({ requestId: input.requestId, workflowId: input.workflowId, packId: baseline.packId,
      sourceVersion: baseline.sourceVersion, sourceCommit: baseline.sourceCommit, expectedSavedHead: baseline.expectedSavedHead,
      expectedDraftToken: baseline.expectedDraftToken, specJson: reviewedSpec })], { type: 'application/json' })
  } catch (error) { throw blockedBeforeSend('save', original, error) }
  let owner: string
  try { owner = beginEditorWorkflowWrite(input.workflowId) }
  catch (error) { throw blockedBeforeSend('save', original, error) }
  try {
    const user = getClientUser()
    const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Pack-Version-CSRF': '1' }
    if (user) { headers['X-User-Id'] = user.userId; headers['X-User-Name'] = encodeURIComponent(user.nickName) }
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    let status: number, result: any
    try {
      ({ status, result } = await Promise.race([(async () => {
        const response = await fetch('/api/auto-heal/unified-save', { method: 'POST', cache: 'no-store', headers, body, signal: controller.signal })
        const status = nativeResponseStatus(response)
        let result: unknown
        try { result = await Response.prototype.json.call(response) } catch { throw new Error('统一保存响应无效，结果未知；请人工核对，勿重试。') }
        return { status, result }
      })(), new Promise<never>((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('统一保存超时，结果未知；请人工核对，勿重试。')) }, 30_000) })]))
    } finally { clearTimeout(timer) }
    // These exact responses are defined by the AutoHeal protocol before its
    // unified service starts, or explicitly attest both phases did not start.
    // All other responses remain unknown; never fall back to legacy save.
    const prewriteCode = result && typeof result === 'object' && !Array.isArray(result)
      && Object.keys(result).length === 1 && ((status === 503 && result.code === 'UNIFIED_SAVE_UNAVAILABLE')
        || (status === 409 && result.code === 'BASELINE_STALE') || (status === 400 && result.code === 'INVALID_REQUEST'))
    const e = result?.evidence
    const prewriteReceipt = status === 409 && result?.requestId === input.requestId && result?.outcome === 'rejected'
      && result?.reconciliationRequired === false && e?.db === 'not_started' && e?.git === 'not_started'
      && e.commit === undefined && e.draftToken === undefined
    if (prewriteCode || prewriteReceipt) {
      finishEditorWorkflowWrite(input.workflowId, owner)
      throw new Error(result.code === 'UNIFIED_SAVE_UNAVAILABLE' ? '宿主未授权统一保存；未写入，未自动降级。' : '统一保存被拒绝或基线已变化；未写入，请重新核对。')
    }
    if (status !== 200 || result?.requestId !== input.requestId || result?.outcome !== 'confirmed' || result?.reconciliationRequired !== false
      || e?.db !== 'confirmed' || e?.git !== 'confirmed' || typeof e.inputHash !== 'string' || !/^[a-f0-9]{64}$/.test(e.inputHash)
      || typeof e.commit !== 'string' || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(e.commit)
      || typeof e.draftToken !== 'string' || !/^[a-f0-9]{64}$/.test(e.draftToken)) {
      throw new Error('统一保存部分完成或结果未知；未自动降级，请人工核对 Git 和数据库，勿重试。')
    }
    const receipt = { ok: true, saved: true, workflowId: input.workflowId, packId: baseline.packId,
      unified: true, commit: e.commit, draftToken: e.draftToken }
    const completedEpoch = editorWorkflowWriteState(input.workflowId).epoch + 1
    finishEditorWorkflowWrite(input.workflowId, owner)
    savedBaselines.set(receipt, { workflowId: input.workflowId, packId: baseline.packId, epoch: completedEpoch })
    unifiedReceipts.set(receipt, { commit: e.commit, draftToken: e.draftToken })
    return receipt as T
  } catch (error) {
    abandonEditorWorkflowWrite(input.workflowId, owner)
    throw error
  }
}

export function useAutoHealSave() {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (input: WorkflowAutoHealSaveRequest) => postAutoHealWrite<WorkflowAutoHealSaveResult>('save', input),
    // The caller owns the immutable candidate and requestId across explicit retries.
    retry: false,
    onSuccess: (_result, input) => {
      void queryClient.invalidateQueries({ queryKey: ['editor-workflow-document', input.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
  return withAutoSubmission(mutation, 'save')
}

export function useAutoHealDeploy(options?: { editorGuard: boolean }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (input: WorkflowAutoHealDeployRequest) => postAutoHealWrite<AutoHealDeployResult & { requestId?: string }>(options?.editorGuard ? 'editor-deploy' : 'deploy', input),
    retry: false,
    onSuccess: (_result, input) => {
      void queryClient.invalidateQueries({ queryKey: ['editor-workflow-document', input.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['db-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })
  const auto = withAutoSubmission(mutation, 'deploy')
  return options?.editorGuard ? mutation : auto
}
