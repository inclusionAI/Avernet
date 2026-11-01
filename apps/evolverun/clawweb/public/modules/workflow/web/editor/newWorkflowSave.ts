import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { oid, publish, sessionFor, validateSnapshot, type CreateAttempt, type SaveResult, type Snapshot } from './packVersionSession'
import { packWorkflow, sameExecutionSpec } from './packCanvas'
import { readPackOperation } from './packOperation'

export const CREATE_WORKFLOW_TIMEOUT_MS = 30_000
export type CreatedWorkflow = { result: SaveResult; snapshot: Snapshot; requestedSpec: WorkflowSpec }

function checkedResult(value: unknown, attempt: CreateAttempt): SaveResult {
  const result = value as SaveResult
  if (!result || result.branch !== `saved/${attempt.input.workflowId}` || result.packId !== attempt.input.packId
    || result.requestId !== attempt.input.requestId || !oid.test(result.commit) || !Number.isSafeInteger(result.version) || result.version < 1) {
    throw new Error('新建保存响应无法校验')
  }
  return result
}

async function run(attempt: CreateAttempt, action: () => Promise<unknown>): Promise<CreatedWorkflow> {
  const id = attempt.input.workflowId, session = sessionFor(id)
  if (session.state.creation?.pending) throw new Error('新建保存正在进行，请等待。')
  const token = {}
  session.createRequest = token
  publish(session, { ...session.state, creation: { ...attempt, pending: true, error: null } })
  let timer: ReturnType<typeof setTimeout> | undefined
  const execute = async () => {
    const result = checkedResult(await action(), attempt)
    if (session.createRequest !== token) throw new Error('新建操作响应已过期')
    // A confirmed saved commit is durable even when the subsequent snapshot read fails.
    attempt = { ...attempt, result, phase: 'saved' }
    publish(session, { ...session.state, creation: { ...attempt, pending: true } })
    const snapshot = validateSnapshot(await fetchJson<unknown>(`/api/workflows/${encodeURIComponent(id)}/pack-version?version=draft`), id, 'draft')
    if (session.createRequest !== token) throw new Error('新建快照响应已过期')
    if (snapshot.baseCommit !== result.commit || snapshot.packId !== result.packId) throw new Error('新建 saved 已移动，不能替换原始保存基线。')
    const git = packWorkflow(snapshot.versionFiles, id, snapshot.packId)
    if (!sameExecutionSpec(git.spec, attempt.input.spec)) throw new Error('新建保存内容与提交草稿不一致，草稿未清除。')
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
    if (session.createRequest !== token) throw cause
    const status = (cause as { status?: number })?.status
    let code: unknown
    try { code = JSON.parse((cause as { body?: string }).body ?? '{}').code } catch { /* Untrusted response. */ }
    const conflict = !attempt.result && status === 409 && (code === 'WORKFLOW_ALREADY_EXISTS' || code === 'WORKFLOW_EXISTS' || code === 'DUPLICATE_WORKFLOW')
    const phase = conflict ? 'conflict' : attempt.result ? 'saved' : attempt.phase === 'failed' ? 'failed' : 'unknown'
    const detail = cause instanceof Error ? cause.message : ''
    const error = conflict ? '新建冲突：工作流 ID 已存在，未覆盖原工作流。请更换新 ID。'
      : phase === 'saved' ? `新建已保存到 Git，但完整快照尚未校验；草稿仍保留。${detail}`
      : phase === 'failed' ? '数据库工作流已创建，Git 初始化在推送前失败；草稿仍保留，可处理失败后重试原始保存（同一 requestId）。'
      : '新建保存结果尚未确认，数据库可能已创建而 Git 待保存；草稿仍保留，请查询或恢复同一 requestId。'
    publish(session, { ...session.state, creation: { ...attempt, phase, pending: false, error } })
    throw new Error(`${error}（requestId：${attempt.input.requestId}）`)
  } finally {
    clearTimeout(timer)
    if (session.createRequest === token) session.createRequest = undefined
  }
}

/** The server atomically creates the DB row, creator permission and operation. Never DB-upsert first. */
export function createWorkflowPack(spec: WorkflowSpec, packId = spec.id): Promise<CreatedWorkflow> {
  const session = sessionFor(spec.id)
  const previous = session.state.creation?.phase === 'completed' ? undefined : session.state.creation
  if (previous?.phase === 'conflict') return Promise.reject(new Error(previous.error!))
  const attempt: CreateAttempt = previous ?? { input: { workflowId: spec.id, packId, spec: structuredClone(spec),
    createOnly: true, expectedSavedHead: null, requestId: crypto.randomUUID() }, pending: false, phase: 'unknown', error: null }
  return run(attempt, () => attempt.result ? Promise.resolve(attempt.result)
    : fetchJson('/api/auto-heal/save', { method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' },
      body: new Blob([JSON.stringify(attempt.input)], { type: 'application/json' }) }))
}

/** GET is read-only. Only an explicit POST may recover the journalled new workflow. */
export function reconcileNewWorkflow(id: string, recover = false): Promise<CreatedWorkflow> {
  const stored = sessionFor(id).state.creation
  if (!stored) return Promise.reject(new Error('没有待恢复的新建操作'))
  const attempt = { ...stored }
  return run(attempt, async () => {
    if (attempt.result) return attempt.result
    const operation = await readPackOperation(id, attempt.input.requestId, 'save', recover)
    if (operation.state === 'failed') {
      attempt.phase = 'failed'
      throw new Error('初始化已在推送前失败')
    }
    if (operation.state !== 'completed') throw new Error('新建操作尚未完成')
    return operation.result
  })
}
