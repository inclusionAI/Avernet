import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { oid, publish, sessionFor, validateSnapshot, hasPackVersionEditRisk, type CreateAttempt, type Snapshot } from './packVersionSession'
import { packWorkflow, sameExecutionSpec } from './packCanvas'
import { normalizationChanges } from './yamlDraft'
import { readWorkflowDocument } from '../api/workflowDocument'

export const CREATE_WORKFLOW_TIMEOUT_MS = 30_000
type CreateResult = { ok: true; saved: true; workflowId: string; packId: string; branch: string; commit: string }
export type CreatedWorkflow = { result: CreateResult; snapshot: Snapshot; requestedSpec: WorkflowSpec }
// Acknowledgements live only as long as the page; there is no journal or replay protocol.
const acknowledgements = new Map<string, CreateResult>()

function checkedResult(value: unknown, attempt: CreateAttempt): CreateResult {
  const r = value as Partial<CreateResult> | null
  if (!r || r.ok !== true || r.saved !== true || r.workflowId !== attempt.input.workflowId
    || r.packId !== attempt.input.packId || r.branch !== `saved/${attempt.input.workflowId}`
    || typeof r.commit !== 'string' || !oid.test(r.commit)) throw new Error('新建保存响应无法校验')
  return r as CreateResult
}

function safeFailure(cause: unknown): 'failed' | 'conflict' | 'unknown' {
  const e = cause as { status?: unknown; body?: unknown } | null
  if (typeof e?.body !== 'string' || e.body.length > 8192) return 'unknown'
  try {
    const data: unknown = JSON.parse(e.body)
    if (!data || typeof data !== 'object' || Array.isArray(data)) return 'unknown'
    const code = (data as { code?: unknown }).code
    if (e.status === 409 && code === 'WORKFLOW_ALREADY_EXISTS') return 'conflict'
    if ((e.status === 400 && ['INVALID_REQUEST', 'INVALID_WORKFLOW', 'WORKFLOW_ID_MISMATCH', 'UNSUPPORTED_GIT_PRECONDITION'].includes(String(code)))
      || (e.status === 403 && code === 'FORBIDDEN')
      || (e.status === 503 && code === 'CREATE_ONLY_UNAVAILABLE_GIT_OWNERSHIP_UNVERIFIED')
      || (e.status === 401 && (data as { error?: unknown }).error === 'Unauthorized')) return 'failed'
  } catch { /* An untrusted error is not proof that nothing was written. */ }
  return 'unknown'
}


/** The server's identity describes an attempted push, never its outcome. */
function attemptedIdentity(cause: unknown, workflowId: string): string {
  const e = cause as { status?: unknown; body?: unknown } | null
  if (e?.status !== 503 || typeof e.body !== 'string' || e.body.length > 8192
    || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(workflowId) || /[^A-Za-z0-9_.-]/.test(workflowId)
    || workflowId.includes('..') || workflowId.endsWith('.') || workflowId.endsWith('.lock')) return ''
  try {
    const data: unknown = JSON.parse(e.body)
    if (!data || typeof data !== 'object' || Array.isArray(data)) return ''
    const { code, branch, commit } = data as { code?: unknown; branch?: unknown; commit?: unknown }
    if (code !== 'CREATE_OUTCOME_UNKNOWN' || branch !== `saved/${workflowId}`
      || typeof commit !== 'string' || (commit.length !== 40 && commit.length !== 64) || !oid.test(commit)) return ''
    return ` 尝试推送的分支：${branch}；尝试的 commit：${commit}。这些信息不证明推送或创建成功。`
  } catch { return '' }
}

// Only fixed diagnostics may be shown; API errors can contain credentials.
function safeVerificationDetail(cause: unknown): string {
  const messages = [
    '缺少创建确认，不能校验快照。', 'saved 已移动，不能确认原始保存基线。',
    'saved 内容与提交草稿不一致。', '数据库内容与提交草稿不一致。',
    '核对期间版本文件状态已变化，未替换已有草稿。',
  ]
  return cause instanceof Error && messages.includes(cause.message) ? cause.message : ''
}

/** A create is sent once. Unknown outcomes cannot be replayed or inferred from a later ref. */
export async function createWorkflowPack(spec: WorkflowSpec, packId = spec.id): Promise<CreatedWorkflow> {
  const session = sessionFor(spec.id)
  const previous = session.state.creation
  if (previous?.pending) throw new Error('新建保存正在进行，请等待。')
  if (previous?.phase === 'unknown') throw new Error(previous.error ?? '新建结果尚未确认，请勿重试；草稿仍保留。')
  if (previous?.phase === 'conflict') throw new Error(previous.error ?? '工作流 ID 已存在，请更换 ID。')
  if (previous?.phase === 'completed') throw new Error('工作流已创建，请勿重复创建。')
  if (hasPackVersionEditRisk(spec.id)) throw new Error('已有版本文件草稿或操作未完成；未发送新建，请先保留并处理草稿。')
  const initialVersions = session.state.versions
  const attempt: CreateAttempt = previous?.phase === 'saved' ? { ...previous } : {
    input: { workflowId: spec.id, packId, spec: structuredClone(spec), createOnly: true, expectedSavedHead: null, requestId: crypto.randomUUID() },
    pending: false, phase: 'unknown', error: null,
  }
  if (attempt.phase !== 'saved') acknowledgements.delete(spec.id)
  const token = {}
  session.createRequest = token
  publish(session, { ...session.state, creation: { ...attempt, pending: true, error: null } })
  let timer: ReturnType<typeof setTimeout> | undefined
  const execute = async (): Promise<CreatedWorkflow> => {
    let result = acknowledgements.get(spec.id)
    if (attempt.phase !== 'saved') {
      result = checkedResult(await fetchJson('/api/auto-heal/save', { method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' },
        body: new Blob([JSON.stringify(attempt.input)], { type: 'application/json' }) }), attempt)
      if (session.createRequest !== token) throw new Error('新建响应已过期')
      acknowledgements.set(spec.id, result)
      attempt.phase = 'saved'
      publish(session, { ...session.state, creation: { ...attempt, pending: true } })
    }
    if (!result) throw new Error('缺少创建确认，不能校验快照。')
    const snapshot = validateSnapshot(await fetchJson<unknown>(`/api/workflows/${encodeURIComponent(spec.id)}/pack-version?version=draft`), spec.id, 'draft')
    if (session.createRequest !== token) throw new Error('新建快照响应已过期')
    if (snapshot.baseCommit !== result.commit || snapshot.packId !== result.packId) throw new Error('saved 已移动，不能确认原始保存基线。')
    if (!sameExecutionSpec(packWorkflow(snapshot.versionFiles, spec.id, snapshot.packId).spec, attempt.input.spec)) throw new Error('saved 内容与提交草稿不一致。')
    const stored = await readWorkflowDocument(spec.id)
    if (session.createRequest !== token) throw new Error('新建数据库响应已过期')
    if (normalizationChanges(stored, attempt.input.spec).length) throw new Error('数据库内容与提交草稿不一致。')
    if (hasPackVersionEditRisk(spec.id) || session.state.versions.current !== initialVersions.current
      || session.state.versions.draft !== initialVersions.draft) throw new Error('核对期间版本文件状态已变化，未替换已有草稿。')
    publish(session, { ...session.state, version: 'draft', creation: { ...attempt, phase: 'completed', pending: false, error: null },
      versions: { ...session.state.versions, draft: { snapshot, files: snapshot.versionFiles.map(file => ({ ...file })), loading: false,
        blocked: false, error: null, notice: `已创建并保存到草稿 commit ${result.commit}，尚未部署。` } } })
    return { result, snapshot, requestedSpec: attempt.input.spec }
  }
  try {
    return await Promise.race([execute(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('新建响应超时')), CREATE_WORKFLOW_TIMEOUT_MS)
    })])
  } catch (cause) {
    if (session.createRequest !== token) throw new Error('新建响应已过期；草稿仍保留。')
    const phase = attempt.phase === 'saved' ? 'saved' : safeFailure(cause)
    const detail = safeVerificationDetail(cause)
    const error = phase === 'conflict' ? '新建冲突：工作流 ID 或 Pack 已存在，未覆盖原工作流。请更换 ID。'
      : phase === 'failed' ? '新建被拒绝，未执行创建；草稿仍保留，请修正后重试。'
      : phase === 'saved' ? `服务端已确认创建，但 saved 快照尚未校验；草稿仍保留。再次保存只会重新读取快照，不会重新创建。${detail}`
      : `新建结果尚未确认，数据库或 Git 可能已变更；草稿仍保留，请人工检查，勿重试创建。${attemptedIdentity(cause, attempt.input.workflowId)}`
    publish(session, { ...session.state, creation: { ...attempt, phase, pending: false, error } })
    throw new Error(`${error}（requestId：${attempt.input.requestId}）`)
  } finally {
    clearTimeout(timer)
    if (session.createRequest === token) session.createRequest = undefined
  }
}
