import {
  classifySaveFailure, diff, dirty, oid, publish, putPackVersion, updateVersion,
  type SaveAttempt, type SaveResult, type Session, type Snapshot, type Version,
} from './packVersionSession'
import { packVersionWritesEnabled } from './packVersionWritePolicy'
import { readPackOperation } from './packOperation'
import { packWorkflow, packWorkflowBaseline } from './packCanvas'
import type { PackVersionFile } from '../components/PackVersionEditor'

export const PACK_SAVE_TIMEOUT_MS = 30_000

/** Historical incomplete YAML is accepted only if it is the exact selected
 * source blob. Every changed candidate must pass the full current contract. */
export function legacyPackCandidate(files: PackVersionFile[], id: string, snapshot: Snapshot) {
  const original = packWorkflowBaseline(snapshot.versionFiles, id, snapshot.packId)
  const candidate = packWorkflowBaseline(files, id)
  const before = snapshot.versionFiles.find(file => file.path === original.path)!
  const after = files.find(file => file.path === candidate.path)!
  return before.contentBase64 === after.contentBase64 ? candidate : packWorkflow(files, id)
}

export function currentVersionSaveConflict(version: Version, snapshot: Snapshot): string | null {
  return version === 'current' && snapshot.draftHead !== null && snapshot.draftHead !== snapshot.baseCommit
    ? '保存冲突：已有不同的草稿，不能用当前版覆盖。当前修改仍保留；请切换到草稿或人工核对后处理。'
    : null
}

/** A successful Git acknowledgement must not leave the session stuck forever
 * when the independent database postflight never resolves. */
async function verifySavedWithinDeadline(verify: (result: SaveResult) => Promise<void>, result: SaveResult): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    await Promise.race([verify(result), new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('Database postflight timeout')), PACK_SAVE_TIMEOUT_MS)
    })])
  } finally { clearTimeout(timer) }
}

function verifiedResult(value: Partial<SaveResult>, id: string, attempt: SaveAttempt): SaveResult {
  if (!value || value.branch !== `wf/${id}` || typeof value.commit !== 'string' || !oid.test(value.commit)
    || value.requestId !== attempt.requestId || value.packId !== attempt.expectedPackId
    || !Number.isSafeInteger(value.version) || value.version! < 1) throw { status: 200 }
  return value as SaveResult
}

/** Commit the submitted bytes, not a potentially newer/stale follow-up GET. */
function complete(session: Session, id: string, attempt: SaveAttempt, result: SaveResult) {
  const snapshot = attempt.snapshot
  const candidate = attempt.files.map(file => ({ ...file }))
  const saved: Snapshot = { ...snapshot, packId: result.packId, baseRef: `origin/wf/${id}`, baseCommit: result.commit, draftHead: result.commit, versionFiles: candidate }
  const source = session.state.versions[attempt.input.sourceVersion]
  const localFiles = (attempt as SaveAttempt & { localFiles?: typeof attempt.files }).localFiles ?? attempt.files
  const sourceChanged = diff(localFiles, source.files).length > 0
  const previousDraft = session.state.versions.draft
  const preserveOtherDraft = attempt.input.sourceVersion === 'current'
    && (dirty(previousDraft) || !previousDraft.snapshot && previousDraft.files.length > 0)
  const savedFiles = (attempt.input.sourceVersion === 'draft' && sourceChanged ? source.files : localFiles).map(file => ({ ...file }))
  const versions = { ...session.state.versions, draft: preserveOtherDraft ? previousDraft : { snapshot: saved, files: savedFiles, loading: false, error: null, blocked: false,
    notice: `已保存到草稿 commit ${result.commit}，尚未部署。` } }
  if (versions.current.snapshot) versions.current = { ...versions.current, snapshot: { ...versions.current.snapshot, draftHead: result.commit } }
  if (attempt.input.sourceVersion === 'current') versions.current = { ...versions.current, files: sourceChanged ? source.files : snapshot.versionFiles.map(file => ({ ...file })), notice: null }
  if (session.state.postSaveVerificationError) {
    for (const key of ['current', 'draft'] as const) versions[key] = { ...versions[key], blocked: true,
      error: session.state.postSaveVerificationError, notice: null }
  }
  // The callback checks the captured editor lifetime before touching canvas state.
  attempt.onSaved?.(result)
  publish(session, { ...session.state, version: 'draft', versions, saving: false, reconciliation: null })
}

async function submit(session: Session, id: string, attempt: SaveAttempt): Promise<SaveResult | null> {
  const token = {}
  session.saveRequest = token
  publish(session, { ...session.state, saving: true })
  let timeout: ReturnType<typeof setTimeout> | undefined
  try {
    const result = verifiedResult(await Promise.race([
      putPackVersion(id, attempt.input),
      new Promise<never>((_, reject) => { timeout = setTimeout(() => reject(new Error('Save response timeout')), PACK_SAVE_TIMEOUT_MS) }),
    ]), id, attempt)
    if (session.saveRequest !== token) return null
    if (attempt.verifySaved) {
      try { await verifySavedWithinDeadline(attempt.verifySaved, result) }
      catch {
        if (session.saveRequest !== token) return null
        publish(session, { ...session.state, postSaveVerificationError: `Git 已成功保存为保存版本 commit ${result.commit}，但数据库原始文档或 Pack 身份已变化或无法核对；请人工核对数据库和 Git，勿盲目重试。本页会话已锁定。` })
      }
    }
    if (session.saveRequest !== token) return null
    complete(session, id, attempt, result)
    return result
  } catch (error) {
    if (session.saveRequest !== token) return null
    const failure = classifySaveFailure(error, id, attempt.requestId)
    const version = attempt.input.sourceVersion
    if (failure.originRejected) {
      // End only this PUT's pending state; never clear an existing block or
      // unknown outcome. The separate workflow DB save may already have run.
      publish(session, { ...session.state, saving: false,
        versions: { ...session.state.versions, [version]: { ...session.state.versions[version],
          error: '版本文件保存请求被来源校验拒绝，本地修改保留。请检查访问入口或代理配置后，再主动保存。' } },
      })
      return null
    }
    const errorMessage = failure.preOperationConflict ? '保存未开始：服务端检测到并发请求。当前候选仍保留，请修改后使用新的 requestId 重试。'
      : failure.conflict ? '保存冲突：远端版本已变化。更改仍在本地，请重新读取后处理。'
      : failure.reason === 'no-response' ? '保存结果无法确认：未收到可用响应。请先人工核对 Git 的 草稿(wf)分支，不要盲目重试。'
      : '保存结果无法确认，可能已部分完成；更改仍在本地，请先人工核对 Git 分支。'
    if (failure.preOperationConflict) {
      // The server did not create this operation, so there is nothing safe to
      // GET/reconcile. Keep the candidate editable; the next save allocates a
      // fresh requestId while retaining this attempt's source/CAS baseline.
      publish(session, { ...session.state, saving: false,
        versions: { ...session.state.versions, [version]: { ...session.state.versions[version], blocked: false, error: errorMessage } },
        reconciliation: null,
      })
      return null
    }
    publish(session, { ...session.state, saving: false,
      versions: { ...session.state.versions, [version]: { ...session.state.versions[version], blocked: true, error: errorMessage } },
      reconciliation: failure.conflict ? null : { token: {}, attempt, version, reason: failure.reason, identity: failure.identity,
        attemptedFiles: attempt.files, sourceCommit: attempt.snapshot.baseCommit, expectedSavedHead: attempt.snapshot.draftHead,
        checking: false, observation: null, checkError: false },
    })
    return null
  } finally {
    clearTimeout(timeout)
    if (session.saveRequest === token) session.saveRequest = undefined
  }
}

/** Both canvas and file save buttons enter here with one immutable snapshot and candidate. */
export async function savePackVersion(session: Session, id: string, version: Version,
  files = session.state.versions[version].files, onSaved?: (result: SaveResult) => void,
  verifySaved?: (result: SaveResult) => Promise<void>, localFiles = files, allowUnchangedLegacyYaml = false): Promise<SaveResult | null> {
  const current = session.state.versions[version], snapshot = current.snapshot
  if (!snapshot || !packVersionWritesEnabled(snapshot) || current.loading || current.readFailure || current.blocked || session.state.uncertainRename !== null || !!session.state.postSaveVerificationError
    || session.state.saving || session.state.uploading || session.state.reconciliation || session.state.deployment) return null
  const changes = diff(snapshot.versionFiles, files)
  if (version === 'current' && dirty(session.state.versions.draft)) {
    updateVersion(session, version, { error: '保存冲突：已有本地草稿更改，不会静默覆盖。请切换到草稿处理。' })
    return null
  }
  const conflict = currentVersionSaveConflict(version, snapshot)
  if (conflict) {
    updateVersion(session, version, { error: conflict })
    return null
  }
  if (!changes.length && version === 'draft') return null
  let expectedPackId: string
  try { expectedPackId = (allowUnchangedLegacyYaml ? legacyPackCandidate(files, id, snapshot) : packWorkflow(files, id)).packId }
  catch {
    updateVersion(session, version, { error: '完整候选的 Pack manifest 或工作流身份无效；请检查合法 Pack ID，并保留当前 workflowId。更改仍在本地。' })
    return null
  }
  for (const key of ['current', 'draft'] as const) {
    session.requests[key]?.abort()
    if (session.state.versions[key].loading) updateVersion(session, key, { loading: false })
  }
  // Preserve the merged canvas YAML locally even if the request conflicts or is unknown.
  const candidate = files.map(file => ({ ...file }))
  const requestId = crypto.randomUUID()
  // A new explicit attempt supersedes the previous source error. Clear it
  // before sending, not on completion, so a later independent error survives.
  const preservedFiles = localFiles.map(file => ({ ...file }))
  updateVersion(session, version, { files: preservedFiles, error: null })
  const attempt: SaveAttempt & { localFiles: typeof files } = { requestId, expectedPackId, snapshot, files: candidate, onSaved, verifySaved,
    localFiles: preservedFiles, input: { sourceVersion: version, sourceCommit: snapshot.baseCommit, expectedSavedHead: snapshot.draftHead, changes, requestId } }
  return submit(session, id, attempt)
}

/** Without a durable journal a retry may perform a second write. */
export async function retryPackVersionSave(_session: Session, _id: string): Promise<SaveResult | null> {
  throw new Error('未配置持久操作日志，无法安全自动重试；请先人工核对 草稿(wf)分支。')
}

/** GET is observation-only; recovery is always an explicit CSRF-guarded POST. */
export async function reconcilePackVersionSave(session: Session, id: string, recover = false) {
  const pending = session.state.reconciliation
  if (!pending || pending.checking || session.state.saving || session.state.uploading) return
  const token = {}
  const controller = new AbortController()
  session.saveRequest = token
  publish(session, { ...session.state, reconciliation: { ...pending, checking: true, checkError: false } })
  let timeout: ReturnType<typeof setTimeout> | undefined
  try {
    const operation = await Promise.race([
      readPackOperation(id, pending.attempt.requestId, 'save', recover, controller.signal),
      new Promise<never>((_, reject) => { timeout = setTimeout(() => { controller.abort(); reject(new Error('Operation timeout')) }, PACK_SAVE_TIMEOUT_MS) }),
    ])
    if (session.saveRequest !== token || session.state.reconciliation?.token !== pending.token) return
    if (operation.state === 'completed') {
      const result = verifiedResult(operation.result as SaveResult, id, pending.attempt)
      if (pending.attempt.verifySaved) {
        try { await verifySavedWithinDeadline(pending.attempt.verifySaved, result) }
        catch {
          if (session.saveRequest !== token || session.state.reconciliation?.token !== pending.token) return
          publish(session, { ...session.state, postSaveVerificationError: `Git 已成功保存为保存版本 commit ${result.commit}，但数据库原始文档或 Pack 身份已变化或无法核对；请人工核对数据库和 Git，勿盲目重试。本页会话已锁定。` })
        }
      }
      if (session.saveRequest !== token || session.state.reconciliation?.token !== pending.token) return
      complete(session, id, pending.attempt, result)
    }
    else if (operation.state === 'failed') {
      // Only the authenticated receipt of this exact request proves no push took
      // place. Preserve its candidate/baseline, but allow correction as a NEW operation.
      const version = pending.version
      publish(session, { ...session.state, reconciliation: null,
        versions: { ...session.state.versions, [version]: { ...session.state.versions[version], blocked: false, error: null,
          notice: '服务端已确认保存于推送前失败，未写入 Git。候选仍保留，可就地修改后重新保存（新 requestId）。' } } })
    }
    else publish(session, { ...session.state, reconciliation: { ...pending, checking: false, checkError: false, operationState: operation.state } })
  } catch {
    if (session.saveRequest === token && session.state.reconciliation?.token === pending.token) {
      publish(session, { ...session.state, reconciliation: { ...pending, checking: false, checkError: true } })
    }
  } finally {
    clearTimeout(timeout)
    if (session.saveRequest === token) session.saveRequest = undefined
  }
}
