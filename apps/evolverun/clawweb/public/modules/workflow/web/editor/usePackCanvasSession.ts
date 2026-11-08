import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { parse, stringify } from 'yaml'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { editorDraftDocument, readEditorDraftCas, type EditorDraftCas } from '../api/editorDraftCas'
import { assertWorkflowReconciliation, createWorkflowReconciliationOwner } from '../api/workflowSnapshotReconciliation'
import type { ReconciliationApproval, ReconciliationContext, ReconciliationReview } from '../api/workflowSnapshotReconciliation'
import { workflowUnifiedSave, readUnifiedSaveCapability, serializeUnifiedSpec, UNIFIED_SAVE_TIMEOUT_MS } from '../api/workflowUnifiedSave'
import { validateWorkflowSnapshotRequest } from '../api/validateWorkflowSnapshotRequest'
import { useEditorStore, validateSpec, prepareWorkflowSession } from './store'
import { sessionFor, load, releaseCleanSession, publish, updateVersion, dirty, diff, validateSnapshot, type Snapshot, type Version } from './packVersionSession'
import { currentVersionSaveConflict, legacyPackCandidate, savePackVersion } from './packVersionSave'
import { packVersionWritesEnabled } from './packVersionWritePolicy'
import { packManifest, packWorkflow, packWorkflowBaseline } from './packCanvas'
import { normalizationChanges } from './yamlDraft'
import { compareGitDatabase } from './workflowBaselineComparison'
import { saveVerifiedWorkflowDocument, verifyDatabaseBaseline, WorkflowDocumentAcknowledgedError } from './workflowDocumentSave'
import { assertEditorWorkflowReadable, editorWorkflowReadIsCurrent, beginEditorWorkflowWrite, finishEditorWorkflowWrite, abandonEditorWorkflowWrite } from './workflowWriteGuard'
import { createDeploymentId } from './deploymentGuard'

export type PackCanvasSaveResult =
  | { mode: 'unified'; dbSaved: true; gitSaved: true; commit: string }
  | { mode: 'legacy'; dbSaved: boolean; gitSaved: boolean; commit: string | null; remainingFileChanges: boolean }

export class PackCanvasPartialSaveError extends Error {
  constructor(readonly workflowId: string, message: string, readonly stages?: { db: 'confirmed' | 'unchanged' | 'unknown'; git: 'not-started' | 'confirmed' | 'unknown'; commit?: string }) { super(message); this.name = 'PackCanvasPartialSaveError' }
}

/** Read Pack identity and the selected file without interpreting its YAML. */
function workflowLocation(files: Snapshot['versionFiles'], id: string, expectedPackId?: string) {
  const location = packManifest(files, id)
  if (expectedPackId !== undefined && location.packId !== expectedPackId) throw new Error('Pack manifest 身份与 Git 基线不一致。')
  const file = files.find(item => item.path === location.path)
  if (!file) throw new Error('manifest 指向的工作流 YAML 不在完整版本中。')
  return { ...location, file }
}

/** Preserve a known identity mismatch as a client guard. If decoding or parsing
 * fails, the server must make its own identity decision before either write. */
function assertKnownSourceIdentity(file: Snapshot['versionFiles'][number], id: string) {
  let value: unknown
  try {
    const bytes = Uint8Array.from(atob(file.contentBase64), c => c.charCodeAt(0))
    value = parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } catch { return }
  if (value && typeof value === 'object' && !Array.isArray(value) && Object.hasOwn(value, 'id')
    && (value as { id?: unknown }).id !== id) throw new Error('工作流 YAML 身份与当前画布不一致。')
}

/** Existing drafts use one candidate for DB, projected YAML and Pack files. */
export function usePackCanvasSession(id: string | null, _databaseSpec?: WorkflowSpec | null, { enabled = true }: { enabled?: boolean } = {}) {
  const session = id ? sessionFor(id) : null
  const state = useSyncExternalStore(listener => { session?.listeners.add(listener); return () => { session?.listeners.delete(listener) } },
    () => session?.state ?? null, () => null)
  const editor = useEditorStore()
  if (session) session.canvasDirty = editor.isDirty || editor.isLayoutDirty || Object.keys(editor.pendingEdits ?? {}).length > 0
  const version = state?.version ?? 'draft', current = state?.versions[version], snapshot = current?.snapshot
  const baselineComparison = useMemo(() => compareGitDatabase(snapshot, id, editor._databaseSpec === undefined ? editor._savedSpec : editor._databaseSpec),
    [snapshot, id, editor._databaseSpec, editor._savedSpec])
  const busy = useRef(false)
  useEffect(() => {
    if (enabled && session && id && !current?.snapshot && !current?.files.length && !current?.loading && !current?.error) load(session, id, version)
  }, [enabled, session, id, version, current?.snapshot, current?.files.length, current?.loading, current?.error])
  useEffect(() => () => { if (session && id) queueMicrotask(() => releaseCleanSession(id, session)) }, [session, id])

  const reconciliationOwner = useRef(createWorkflowReconciliationOwner())
  const reconciliation = useRef<{ review: ReconciliationReview; row: EditorDraftCas; revision: () => number; stop: () => void } | null>(null)
  function closeReconciliation() {
    const active = reconciliation.current
    reconciliation.current = null
    active?.stop()
    reconciliationOwner.current.close()
  }
  useEffect(() => () => closeReconciliation(), [id])

  function reconciliationContext(row: EditorDraftCas, revision: number): ReconciliationContext {
    if (!id || !session) throw new Error('缺少工作流会话；未发送保存。')
    const live = useEditorStore.getState(), selected = session.state.version, source = session.state.versions[selected], base = source.snapshot
    if (!live.spec || live.spec.id !== id || !live._savedSpec || live._databaseSpec == null || live._databasePackId === undefined
      || live._databasePackId !== row.packId || row.workflowId !== id || sessionFor(id) !== session
      || !base || base.packId !== (row.packId ?? id) || source.loading || source.readFailure || source.blocked
      || session.state.uploading || session.state.reconciliation || session.state.deployment || session.state.uncertainRename || session.state.postSaveVerificationError) throw new Error('审阅基线或编辑状态不可用；未发送保存。')
    const other = selected === 'draft' ? 'current' : 'draft', otherState = session.state.versions[other]
    if (otherState.loading || dirty(otherState) || !otherState.snapshot && otherState.files.length) throw new Error('另一个版本有未保存文件或正在读取；未发送保存。')
    const conflict = currentVersionSaveConflict(selected, base)
    if (conflict) throw new Error(conflict)
    const original = workflowLocation(base.versionFiles, id, base.packId), candidate = workflowLocation(source.files, id)
    const originalFile = original.file, candidateFile = candidate.file
    if (candidate.packId !== base.packId || candidate.path !== original.path || candidateFile.mode !== originalFile.mode
      || diff(base.versionFiles.filter(f => f.path === 'workflow.pack.yaml'), source.files.filter(f => f.path === 'workflow.pack.yaml')).length) {
      throw new Error('统一保存暂不支持修改 Pack manifest、工作流路径或 YAML 文件模式；本地草稿仍保留。')
    }
    const gitYaml = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(Uint8Array.from(atob(originalFile.contentBase64), c => c.charCodeAt(0)))
    return { workflowId: id, packId: base.packId, sourceVersion: selected, sourceCommit: base.baseCommit, expectedSavedHead: base.draftHead,
      expectedDraftToken: row.draftToken, workflowPath: original.path, dbSpecJson: row.specJson, gitYaml,
      candidateSpecJson: serializeUnifiedSpec(live.spec), filesBefore: structuredClone(base.versionFiles), filesAfter: structuredClone(source.files),
      changes: diff(base.versionFiles, source.files).filter(change => change.path !== original.path), revision: `${live.sessionId}:${revision}` }
  }

  async function prepareReconciliation(): Promise<ReconciliationReview> {
    closeReconciliation()
    if (!id || !session || busy.current || session.state.saving) throw new Error('工作流保存或审阅正在进行；未发送保存。')
    const epoch = assertEditorWorkflowReadable(id), initial = useEditorStore.getState()
    if (!initial.spec || initial.spec.id !== id || !initial._savedSpec || !initial._databaseSpec || initial._databasePackId === undefined) throw new Error('缺少可信的数据库草稿；未发送保存。')
    let revision = 0
    const stopStore = useEditorStore.subscribe(() => { revision++ })
    let prior = session.state
    const observe = () => {
      const next = session.state
      if (next.versions !== prior.versions || next.version !== prior.version || next.uploading !== prior.uploading
        || next.reconciliation !== prior.reconciliation || next.deployment !== prior.deployment || next.uncertainRename !== prior.uncertainRename
        || next.postSaveVerificationError !== prior.postSaveVerificationError) revision++
      prior = next
    }
    session.listeners.add(observe)
    const stop = () => { stopStore(); session.listeners.delete(observe) }
    busy.current = true
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      const row = await Promise.race([verifyDatabaseBaseline(id, initial._databaseSpec, () => revision === 0 && useEditorStore.getState() === initial, initial._databasePackId),
        new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('读取数据库草稿超时；未发送保存。')), UNIFIED_SAVE_TIMEOUT_MS) })])
      if (revision !== 0 || useEditorStore.getState() !== initial || !editorWorkflowReadIsCurrent(id, epoch)) throw new Error('审阅准备期间基线已变化；未发送保存。')
      const review = reconciliationOwner.current.open(reconciliationContext(row, revision))
      reconciliation.current = { review, row, revision: () => revision, stop }
      return review
    } catch (error) { stop(); throw error }
    finally { clearTimeout(timer); busy.current = false }
  }
  function currentReconciliation(review: ReconciliationReview): ReconciliationContext {
    const active = reconciliation.current
    if (!active || active.review !== review) throw new Error('审阅已取消或不再有效；未发送保存。')
    return reconciliationContext(active.row, active.revision())
  }
  function cancelReconciliation(review: ReconciliationReview) {
    if (reconciliation.current?.review === review) closeReconciliation()
  }

  async function performSave(first?: { version: Version; base: Snapshot; files: Snapshot['versionFiles']; activeSpec: WorkflowSpec; sessionId: number }, approval?: ReconciliationApproval): Promise<PackCanvasSaveResult> {
    const live = useEditorStore.getState()
    if (!approval) closeReconciliation()
    if (!id || !session) throw new Error('首次新建尚无法安全初始化 Git 草稿(wf)分支；本地草稿仍保留。')
    assertEditorWorkflowReadable(id)
    if (session.state.uncertainRename || session.state.postSaveVerificationError) throw new Error(session.state.uncertainRename ?? session.state.postSaveVerificationError ?? '')
    if (busy.current || session.state.saving || session.state.uploading || session.state.reconciliation || session.state.deployment) throw new Error('保存、上传或部署状态未完成；本地草稿仍保留。')
    if (Object.keys(live.pendingEdits ?? {}).length) throw new Error('存在未应用的配置草稿；请先应用或放弃，再保存。')
    if (live.validationErrors?.length) throw new Error(`保存前请修复验证错误：${live.validationErrors[0].message}`)
    if (!live.spec || live.spec.id !== id || !live._savedSpec) throw new Error('缺少数据库文档基线；本地草稿仍保留。')
    const databaseBaseline = live._databaseSpec === undefined ? live._savedSpec : live._databaseSpec
    if (live._databasePackId === undefined) throw new Error('缺少可信的原始数据库 Pack 身份基线；本地草稿仍保留。')
    // Initial drafts use a different protocol. A read-only capability probe
    // never proves permission to write; the server reauthorizes every POST.
    busy.current = true
    publish(session, { ...session.state, saving: true })
    let capability: 'available' | 'unavailable'
    try { capability = databaseBaseline === null ? 'unavailable' : first ? 'available' : await readUnifiedSaveCapability(id) }
    finally { busy.current = false; publish(session, { ...session.state, saving: false }) }
    if (useEditorStore.getState() !== live) throw new Error('读取能力期间编辑会话已变化；未发送保存。')
    // Editor save must publish the YAML too. Legacy DB-only or script-only
    // compatibility cannot satisfy that contract; leave both stores untouched.
    // The separate AutoHeal legacy API is unaffected.
    if (databaseBaseline !== null && capability === 'unavailable') {
      throw new Error('统一保存不可用，无法同步草稿(wf)工作流 YAML；数据库和 Git 均未写入，本地修改仍保留。')
    }
    if (capability === 'unavailable') {
      const selected = session.state.version, source = session.state.versions[selected], base = source.snapshot
      const files = structuredClone(source.files)
      const fileDirty = dirty(source) || !base && files.length > 0
      const otherDirty = () => Object.entries(session.state.versions).some(([key, value]) => key !== selected && (dirty(value) || !value.snapshot && value.files.length > 0))
      if (otherDirty()) throw new Error('另一个版本有未保存文件草稿；本地修改仍保留。')
      if (databaseBaseline === null) {
        if (!base || source.loading || source.readFailure || source.blocked) throw new Error('首次保存缺少可信 Git 基线；数据库和 Git 均未写入。')
        if (base.packId !== live._databasePackId) throw new Error('Git Pack 与生效版本 Pack 身份不一致；数据库和 Git 均未写入。')
        const conflict = currentVersionSaveConflict(selected, base)
        if (conflict) throw new Error(conflict)
        const original = workflowLocation(base.versionFiles, id, base.packId)
        assertKnownSourceIdentity(original.file, id)
        const candidate = workflowLocation(files, id)
        if (candidate.packId !== base.packId || candidate.path !== original.path || candidate.file.mode !== original.file.mode
          || diff(base.versionFiles.filter(f => f.path === 'workflow.pack.yaml'), files.filter(f => f.path === 'workflow.pack.yaml')).length) {
          throw new Error('统一保存暂不支持修改 Pack manifest、工作流路径或 YAML 文件模式；本地草稿仍保留。')
        }
        if (candidate.file.contentBase64 !== original.file.contentBase64) {
          const candidateSpec = packWorkflow(files, id).spec
          const errors = validateSpec(candidateSpec)
          if (errors.length) throw new Error(`文件 YAML 验证失败：${errors[0].message}；本地草稿仍保留。`)
          if ((compareGitDatabase(base, id, live._savedSpec) !== 'same'
            || normalizationChanges(live.spec, live._savedSpec).length > 0 || live.isLayoutDirty)
            && normalizationChanges(candidateSpec, live.spec).length) throw new Error('画布和文件同时修改了工作流 YAML，请先统一内容；草稿仍保留。')
        }
      }
      let gitFiles = files
      if (databaseBaseline !== null && fileDirty && base) {
        if (source.loading || source.readFailure || source.blocked) throw new Error('Git 版本文件不可用；本地草稿仍保留。')
        if (base.packId !== (live._databasePackId ?? id)) throw new Error('Git Pack 与数据库 Pack 身份不一致；本地草稿仍保留。')
        const conflict = currentVersionSaveConflict(selected, base)
        if (conflict) throw new Error(conflict)
        const original = packWorkflowBaseline(base.versionFiles, id, base.packId)
        const candidate = legacyPackCandidate(files, id, base)
        if (candidate.packId !== base.packId) throw new Error('暂不支持更改 Pack 身份；本地草稿仍保留。')
        const old = base.versionFiles.find(f => f.path === original.path)!
        const edited = files.find(f => f.path === candidate.path)!
        if (edited.contentBase64 !== old.contentBase64 && candidate.spec && normalizationChanges(candidate.spec, live.spec).length) {
          const canvasChanged = normalizationChanges(live.spec, live._savedSpec).length > 0
          throw new Error(canvasChanged
            ? '画布和文件同时修改了工作流 YAML，内容不一致；请先统一。旧接口不支持写入工作流 YAML，本地草稿仍保留。'
            : '请先将文件 YAML 应用到画布；旧接口不支持写入工作流 YAML，本地草稿仍保留。')
        }
        const retained = files.find(f => f.path === original.path)
        if (candidate.path !== original.path && retained && retained.contentBase64 !== old.contentBase64) throw new Error('移动后旧 YAML 路径也被修改；请先核对，本地草稿仍保留。')
        if (candidate.path !== original.path && base.versionFiles.some(f => f.path === candidate.path)) throw new Error('工作流目标路径已存在；本地草稿仍保留。')
        gitFiles = files.map(f => f.path === candidate.path ? { ...f, contentBase64: old.contentBase64 } : f)
      }
      const canSaveFiles = !!base && packVersionWritesEnabled(base)
      if (!window.confirm(databaseBaseline === null
        ? '首次保存将先创建数据库草稿，再尝试统一保存 Git YAML 和文件；两阶段不是原子操作。若统一保存不可用，数据库可能已保存但 Git 未写入，需核对。是否继续？'
        : canSaveFiles
          ? '兼容模式先保存数据库，再保存已授权的 Pack 文件；Git 工作流 YAML 内容不会更新。两阶段不是原子操作，结果不明须人工核对。是否继续？'
          : '统一保存不可用，且未获得文件写入权限；仅能保存数据库，Git YAML 和文件不会更新，文件草稿仍保留。是否继续？')) throw new Error('已取消兼容保存；未发送写入，本地草稿仍保留。')
      const activeBaseline = structuredClone(live._activeBaseline)
      const requestSpec = JSON.parse(serializeUnifiedSpec(live.spec)) as WorkflowSpec
      const isCurrent = () => {
        const now = useEditorStore.getState(), nowSource = session.state.versions[selected]
        const nowDatabase = now._databaseSpec === undefined ? now._savedSpec : now._databaseSpec
        return now.sessionId === live.sessionId && now.spec?.id === id && now._databasePackId === live._databasePackId
          && !normalizationChanges(requestSpec, now.spec).length && !normalizationChanges(databaseBaseline, nowDatabase).length
          && !normalizationChanges(activeBaseline, now._activeBaseline).length && !Object.keys(now.pendingEdits ?? {}).length && !now.validationErrors.length
          && session.state.version === selected && nowSource.snapshot === base && !diff(files, nowSource.files).length && !otherDirty()
      }
      if (!isCurrent()) throw new Error('确认期间编辑会话已变化；未发送保存。')
      busy.current = true
      publish(session, { ...session.state, saving: true })
      let dbSaved = false
      try {
        let stored: WorkflowSpec
        if (databaseBaseline !== null && !normalizationChanges(databaseBaseline, requestSpec).length) {
          await verifyDatabaseBaseline(id, databaseBaseline, isCurrent, live._databasePackId)
          stored = databaseBaseline
        } else {
          stored = await saveVerifiedWorkflowDocument(id, databaseBaseline, requestSpec, isCurrent,
            { expectedRawPackId: live._databasePackId, activeBaseline, expectedActiveDeployNumber: live._activeDeployNumber ?? undefined })
          dbSaved = true
          const latest = useEditorStore.getState()
          if (latest.sessionId !== live.sessionId || latest.spec?.id !== id
            || !latest.completeSave(stored, requestSpec, live.sessionId, databaseBaseline === null ? stored : undefined, databaseBaseline === null ? activeBaseline : undefined)) {
            throw new PackCanvasPartialSaveError(id, '数据库已保存，但编辑器会话已变化；Git 未写入，请核对。', { db: 'confirmed', git: 'not-started' })
          }
        }
        const afterDb = useEditorStore.getState(), latestSource = session.state.versions[selected]
        const stable = afterDb.sessionId === live.sessionId && afterDb.spec?.id === id && afterDb._databasePackId === live._databasePackId
          && !normalizationChanges(requestSpec, afterDb.spec).length && !Object.keys(afterDb.pendingEdits ?? {}).length && !afterDb.validationErrors.length
          && session.state.version === selected && latestSource.snapshot === base && !diff(files, latestSource.files).length && !otherDirty()
        if (!stable) throw new PackCanvasPartialSaveError(id, `数据库${dbSaved ? '已保存' : '已核对'}，但保存期间草稿变化；Git 未写入，后续修改仍保留。`, { db: dbSaved ? 'confirmed' : 'unchanged', git: 'not-started' })
        if (databaseBaseline === null) {
          // The capability resolver needs a persisted row. Only probe after the
          // first-draft transaction and verified readback; no POST fallback.
          const firstPartial = (message: string) => {
            const detail = `部分保存：${message}`
            publish(session, { ...session.state, postSaveVerificationError: detail })
            return new PackCanvasPartialSaveError(id, detail, { db: 'confirmed', git: 'not-started' })
          }
          let available: 'available' | 'unavailable'
          try { available = await readUnifiedSaveCapability(id) }
          catch { throw firstPartial('首次数据库草稿已保存，但无法确认统一保存能力；Git 未写入，请核对后处理。') }
          if (available === 'unavailable') throw firstPartial('首次数据库草稿已保存，但统一保存不可用；Git YAML 和文件均未写入，请核对后处理。')
          const now = useEditorStore.getState(), nowSource = session.state.versions[selected]
          if (now.sessionId !== live.sessionId || now.spec?.id !== id || now._databasePackId !== live._databasePackId
            || normalizationChanges(requestSpec, now.spec).length || normalizationChanges(stored, now._databaseSpec).length
            || Object.keys(now.pendingEdits ?? {}).length || now.validationErrors.length
            || session.state.version !== selected || nowSource.snapshot !== base || diff(files, nowSource.files).length || otherDirty()) {
            throw firstPartial('首次数据库草稿已保存，但读取能力期间编辑会话变化；Git 未写入。')
          }
          if (available === 'available') {
            if (!base) throw firstPartial('首次数据库草稿已保存，但 Git 基线不可用；Git 未写入。')
            // Transition synchronously to the same guarded unified path. The
            // original active document, not the just-written draft, proves the
            // selected Git source baseline; neither observation is a transaction.
            busy.current = false
            publish(session, { ...session.state, saving: false })
            try { return await performSave({ version: selected, base, files, activeSpec: structuredClone(live._savedSpec!), sessionId: live.sessionId }) }
            catch (cause) {
              if (cause instanceof PackCanvasPartialSaveError) {
                throw new PackCanvasPartialSaveError(id, `部分保存：首次数据库草稿已保存；${cause.message}`,
                  cause.stages ?? { db: 'confirmed', git: 'unknown' })
              }
              throw firstPartial(`首次数据库草稿已保存，Git 未写入：${cause instanceof Error ? cause.message : '操作失败'} 请核对后处理。`)
            }
          }
        }
        if (!fileDirty) return { mode: 'legacy', dbSaved, gitSaved: false, commit: null, remainingFileChanges: false }
        if (!canSaveFiles || !base) throw new PackCanvasPartialSaveError(id, `数据库${dbSaved ? '已保存' : '无变更'}；宿主未授予文件写入权限，Git 文件及 YAML 未更新，文件草稿仍保留。`, { db: dbSaved ? 'confirmed' : 'unchanged', git: 'not-started' })
        if (!diff(base.versionFiles, gitFiles).length) throw new PackCanvasPartialSaveError(id, `数据库${dbSaved ? '已保存' : '无变更'}；旧接口不支持写入工作流 YAML，YAML 文件草稿仍保留。`, { db: dbSaved ? 'confirmed' : 'unchanged', git: 'not-started' })
        // savePackVersion owns the Git pending state. Transition synchronously so
        // no other editor operation can enter between the two stages.
        publish(session, { ...session.state, saving: false })
        // Keep the same-tab write guard through Git and postflight. A reload,
        // timeout, malformed receipt, or failure to clear remains quarantined.
        let owner: string | undefined
        let result: Awaited<ReturnType<typeof savePackVersion>> = null
        try {
          owner = beginEditorWorkflowWrite(id)
          result = await savePackVersion(session, id, selected, gitFiles, undefined, async () => {
            await verifyDatabaseBaseline(id, stored, () => {
              const now = useEditorStore.getState()
              return now.sessionId === live.sessionId && now.spec?.id === id && now._databasePackId === live._databasePackId
                && !normalizationChanges(stored, now._databaseSpec === undefined ? now._savedSpec : now._databaseSpec).length && !otherDirty()
            }, live._databasePackId)
          }, files, true)
          if (!result || session.state.postSaveVerificationError) {
            const detail = session.state.postSaveVerificationError ?? session.state.versions[selected].error ?? '文件保存未开始或结果无法确认'
            throw new PackCanvasPartialSaveError(id, `数据库${dbSaved ? '已保存' : '已核对'}；${detail} Git 工作流 YAML 未更新，请勿盲目重试。`,
              { db: dbSaved ? 'confirmed' : 'unchanged', git: result ? 'confirmed' : 'unknown', ...(result ? { commit: result.commit } : {}) })
          }
          finishEditorWorkflowWrite(id, owner)
          owner = undefined
          const remainingFileChanges = Object.values(session.state.versions).some(v => dirty(v) || !v.snapshot && v.files.length > 0)
          return { mode: 'legacy', dbSaved, gitSaved: true, commit: result.commit, remainingFileChanges }
        } catch (cause) {
          if (owner) abandonEditorWorkflowWrite(id, owner)
          if (cause instanceof PackCanvasPartialSaveError) throw cause
          throw new PackCanvasPartialSaveError(id, `数据库${dbSaved ? '已保存' : '已核对'}；${result ? `Git 已确认 commit ${result.commit}，但状态标记无法清除` : 'Git 未能确认或未发送'}；请人工核对，勿盲目重试。`,
            { db: dbSaved ? 'confirmed' : 'unchanged', git: result ? 'confirmed' : owner ? 'unknown' : 'not-started', ...(result ? { commit: result.commit } : {}) })
        }

      } finally { busy.current = false; publish(session, { ...session.state, saving: false }) }
    }
    const selected: Version = session.state.version, source = session.state.versions[selected], base = source.snapshot
    const otherDirty = () => Object.entries(session.state.versions).some(([key, value]) => key !== selected && (dirty(value) || !value.snapshot && value.files.length > 0))
    if (otherDirty()) throw new Error('另一个版本有未保存文件草稿；本地修改仍保留。')
    if (!base || source.loading || source.readFailure || source.blocked) throw new Error(`Git 版本文件尚未成功读取或不可用；本地草稿仍保留。${source.readFailure ?? source.error ?? ''}`)
    if (first && (first.sessionId !== live.sessionId || first.version !== selected || first.base !== base || diff(first.files, source.files).length)) throw new Error('首次保存后的 Git 基线或文件草稿已变化；未发送统一保存。')
    if (base.packId !== (live._databasePackId ?? id)) throw new Error('Git Pack 与数据库 Pack 身份不一致；本地草稿仍保留。')
    const conflict = currentVersionSaveConflict(selected, base)
    if (conflict) throw new Error(conflict)
    const original = workflowLocation(base.versionFiles, id, base.packId)
    assertKnownSourceIdentity(original.file, id)
    const baselineComparison = compareGitDatabase(base, id, first?.activeSpec ?? databaseBaseline)
    const candidate = workflowLocation(source.files, id)
    const originalFile = original.file, candidateFile = candidate.file
    const manifestChange = diff(base.versionFiles.filter(f => f.path === 'workflow.pack.yaml'), source.files.filter(f => f.path === 'workflow.pack.yaml'))
    if (candidate.packId !== base.packId || candidate.path !== original.path || manifestChange.length || candidateFile.mode !== originalFile.mode) throw new Error('统一保存暂不支持修改 Pack manifest、工作流路径或 YAML 文件模式；本地草稿仍保留。')
    const fileChanged = candidateFile.contentBase64 !== originalFile.contentBase64
    const candidateSpec = fileChanged ? packWorkflow(source.files, id).spec : null
    const canvasChanged = normalizationChanges(live.spec, live._savedSpec).length > 0 || live.isLayoutDirty
    const yamlOnly = !!candidateSpec && !canvasChanged && normalizationChanges(candidateSpec, live.spec).length > 0
    if (candidateSpec && (canvasChanged || baselineComparison !== 'same') && normalizationChanges(candidateSpec, live.spec).length && !approval) throw new Error('画布和文件同时修改了工作流 YAML，请先统一内容；草稿仍保留。')
    if (candidateSpec) {
      const errors = validateSpec(candidateSpec)
      if (errors.length) throw new Error(`文件 YAML 验证失败：${errors[0].message}；本地草稿仍保留。`)
    }
    const specJson = approval ? approval.specJson : serializeUnifiedSpec(yamlOnly ? candidateSpec : live.spec)
    const requestSpec = approval ? validateWorkflowSnapshotRequest(specJson, id) : JSON.parse(specJson) as WorkflowSpec
    const reconciliationAdopt = !!approval && normalizationChanges(requestSpec, live.spec).length > 0
    const requestErrors = validateSpec(requestSpec)
    if (requestErrors.length) throw new Error(`最终文档验证失败：${requestErrors[0].message}；未发送保存。`)
    const submitted = structuredClone(source.files)
    const other = selected === 'draft' ? 'current' : 'draft'
    const otherSnapshot = session.state.versions[other].snapshot
    const otherFiles = structuredClone(session.state.versions[other].files)
    // The server owns selected YAML bytes; ordinary operations must not include it.
    const changes = diff(base.versionFiles, submitted).filter(change => change.path !== original.path)
    const isCurrent = () => {
      const now = useEditorStore.getState(), currentSource = session.state.versions[selected]
      const nowDatabase = now._databaseSpec === undefined ? now._savedSpec : now._databaseSpec
      return now.sessionId === live.sessionId && now._databasePackId === live._databasePackId
        && !!now.spec && !!now._savedSpec && !!nowDatabase
        && !normalizationChanges(live.spec, now.spec).length && (!(yamlOnly || reconciliationAdopt) || !normalizationChanges(live.positions, now.positions).length)
        && !normalizationChanges(live._savedSpec, now._savedSpec).length
        && !normalizationChanges(databaseBaseline, nowDatabase).length && !normalizationChanges(live._activeBaseline, now._activeBaseline).length
        && !Object.keys(now.pendingEdits ?? {}).length && !now.validationErrors.length && !session.state.uploading
        && session.state.version === selected && currentSource.snapshot === base && !diff(submitted, currentSource.files).length && !otherDirty()
    }
    // Once dispatched, user edits are allowed; only identity and baseline changes
    // invalidate applying a verified receipt. Do not confuse dirty content with
    // uncertainty about the write itself.
    const sameBaseline = () => {
      const now = useEditorStore.getState(), selectedState = session.state.versions[selected], otherState = session.state.versions[other]
      const nowDatabase = now._databaseSpec === undefined ? now._savedSpec : now._databaseSpec
      return now.sessionId === live.sessionId && now.spec?.id === id && now._databasePackId === live._databasePackId
        && !!now._savedSpec && !!nowDatabase && !normalizationChanges(live._savedSpec, now._savedSpec).length
        && !normalizationChanges(databaseBaseline, nowDatabase).length && !normalizationChanges(live._activeBaseline, now._activeBaseline).length
        && sessionFor(id) === session && session.state.version === selected && selectedState.snapshot === base
        && otherState.snapshot === otherSnapshot && !diff(otherFiles, otherState.files).length && !session.state.uploading
    }
    busy.current = true
    publish(session, { ...session.state, saving: true })
    // An earlier version read must not replace the newly saved draft (or any
    // edits made after saving) when its response arrives late.
    for (const key of ['current', 'draft'] as const) {
      session.requests[key]?.abort()
      if (session.state.versions[key].loading) updateVersion(session, key, { loading: false })
    }
    let owner: string | undefined
    let writeStarted = false
    try {
      const row = await verifyDatabaseBaseline(id, databaseBaseline, isCurrent, live._databasePackId)
      if (!isCurrent()) throw new Error('核对期间草稿变化；未发送保存。')
      // Only a saved-tree baseline already equal to the verified DB document can
      // be a no-op. Current-only or first-draft paths still have to publish.
      if (!first && !approval && baselineComparison === 'same' && selected === 'draft' && !fileChanged && !changes.length
        && !normalizationChanges(databaseBaseline, requestSpec).length) {
        return { mode: 'legacy', dbSaved: false, gitSaved: false, commit: null, remainingFileChanges: false }
      }
      // The server reauthorizes the write; a capability read is not a grant.
      owner = beginEditorWorkflowWrite(id)
      if (approval) {
        try {
          const active = reconciliation.current
          if (!active || row.draftToken !== active.row.draftToken || row.specJson !== active.row.specJson || row.packId !== active.row.packId || !isCurrent()) throw new Error('审阅后数据库或编辑内容已变化；未发送保存。')
          assertWorkflowReconciliation(approval, currentReconciliation(active.review), specJson)
        } catch (error) {
          finishEditorWorkflowWrite(id, owner); owner = undefined
          throw error
        }
        closeReconciliation()
      }
      writeStarted = true
      const receipt = await workflowUnifiedSave(id, { requestId: createDeploymentId(), sourceVersion: selected,
        sourceCommit: base.baseCommit, expectedSavedHead: base.draftHead, expectedDraftToken: row.draftToken, specJson, changes })
      let timer: ReturnType<typeof setTimeout> | undefined
      let saved: ReturnType<typeof validateSnapshot>, stored: WorkflowSpec
      try {
        const result = await Promise.race([Promise.all([
          readEditorDraftCas(id),
          fetchJson<unknown>(`/api/workflows/${encodeURIComponent(id)}/pack-version?version=draft`, { cache: 'no-store' }),
        ]), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('postflight timeout')), UNIFIED_SAVE_TIMEOUT_MS) })])
        const [db, raw] = result
        saved = validateSnapshot(raw, id, 'draft')
        stored = editorDraftDocument(db.specJson, id, requestSpec)
        const expectedYaml = stringify(JSON.parse(specJson), { sortMapEntries: true, lineWidth: 0 })
        const actualYaml = saved.versionFiles.find(f => f.path === original.path)
        const text = actualYaml && new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(actualYaml.contentBase64), c => c.charCodeAt(0)))
        if (db.draftToken !== receipt.draftToken || db.packId !== row.packId || normalizationChanges(requestSpec, stored).length
          || saved.baseRef !== `origin/wf/${id}` || saved.baseCommit !== receipt.commit || saved.draftHead !== receipt.commit || saved.packId !== base.packId
          || !actualYaml || actualYaml.mode !== originalFile.mode || text !== expectedYaml
          || diff(submitted.filter(f => f.path !== original.path), saved.versionFiles.filter(f => f.path !== original.path)).length
          || !sameBaseline()) throw new Error('postflight mismatch')
      } finally { clearTimeout(timer) }
      // A file-only YAML candidate may contain nodes absent from the live
      // canvas. Do not let a concurrent canvas/layout edit turn that old
      // canvas into a retryable baseline which could erase those saved nodes.
      const yamlCanvasIsCurrent = () => {
        const now = useEditorStore.getState()
        return now.sessionId === live.sessionId && !!now.spec
          && !normalizationChanges(live.spec, now.spec).length
          && !normalizationChanges(live.positions, now.positions).length
          && !Object.keys(now.pendingEdits ?? {}).length
      }
      const protectYamlCanvas = () => {
        if (!(yamlOnly || reconciliationAdopt) || yamlCanvasIsCurrent()) return
        // finishEditorWorkflowWrite and completeSave notify subscribers. If a
        // synchronous observer changed the canvas after the marker was cleared,
        // restore a durable marker before rejecting the acknowledged result.
        if (!owner) owner = beginEditorWorkflowWrite(id)
        throw new Error('文件 YAML 已写入，但保存期间画布或布局发生变化；本地画布和文件保留，请人工核对后再处理。')
      }
      protectYamlCanvas()
      // Do not mutate either local baseline if the durable marker cannot be cleared.
      finishEditorWorkflowWrite(id, owner)
      owner = undefined
      protectYamlCanvas()
      if (!sameBaseline() || !useEditorStore.getState().completeSave(stored, requestSpec, live.sessionId)) {
        owner = beginEditorWorkflowWrite(id)
        throw new Error('当前编辑器无法确认保存基线')
      }
      protectYamlCanvas()
      // Adopt a file-only candidate only after both stores have been verified.
      // importYaml preserves the editor's layout/history behavior. A later canvas
      // edit or pending form draft must never be replaced by the earlier file.
      if (yamlOnly || reconciliationAdopt) {
        const now = useEditorStore.getState()
        if (now.sessionId === live.sessionId && !normalizationChanges(now.spec, live.spec).length
          && !Object.keys(now.pendingEdits ?? {}).length) {
          const prepared = prepareWorkflowSession(stored)
          if (!prepared) { owner = beginEditorWorkflowWrite(id); throw new Error('保存后文件 YAML 无法安全应用到画布；请人工核对。') }
          const expectedPositions = Object.fromEntries(stored.nodes.map(node => [node.id,
            Object.hasOwn(live.positions, node.id) ? structuredClone(live.positions[node.id]) : structuredClone(prepared.positions[node.id])]))
          now.importYaml(stored)
          const imported = useEditorStore.getState()
          if (imported.sessionId !== live.sessionId || normalizationChanges(imported.spec, stored).length
            || normalizationChanges(imported.positions, expectedPositions).length || Object.keys(imported.pendingEdits ?? {}).length) {
            owner = beginEditorWorkflowWrite(id)
            throw new Error('应用已保存 YAML 期间画布或布局变化；本地内容保留，请人工核对。')
          }
          // New auto-layout positions are clean only when the original layout
          // was clean. Never erase a user's existing or synchronous later drag.
          if (!live.isLayoutDirty) useEditorStore.setState({ _baselinePositions: structuredClone(expectedPositions), isLayoutDirty: false })
        }
      }
      const previous = session.state.versions
      // completeSave notifies store subscribers synchronously. A subscriber may
      // change the other version while the verified DB baseline is adopted.
      if (session.state.version !== selected || previous[selected].snapshot !== base
        || previous[other].snapshot !== otherSnapshot || diff(otherFiles, previous[other].files).length) {
        owner = beginEditorWorkflowWrite(id)
        throw new Error('保存基线更新期间文件候选已变化；本地文件仍保留')
      }
      // Reapply only edits made after dispatch; the verified saved tree already
      // contains the submitted edits and the server-generated workflow YAML.
      const later = diff(submitted, previous[selected].files)
      const rebase = (files: Snapshot['versionFiles']) => {
        const result = new Map(structuredClone(files).map(file => [file.path, file]))
        for (const change of later) {
          if (change.operation === 'delete') result.delete(change.path)
          else result.set(change.path, { path: change.path, mode: change.mode, contentBase64: change.contentBase64 })
        }
        return [...result.values()]
      }
      const migrateCurrent = selected === 'current' && later.length > 0
      const versions = { ...previous, draft: { snapshot: saved, files: rebase(saved.versionFiles), loading: false, error: null, readFailure: undefined, blocked: false,
        notice: migrateCurrent
          ? `已保存到草稿 commit ${receipt.commit}，尚未部署。保存期间的后续文件修改已迁移到草稿版，仍需再次保存；当前版已恢复原基线。`
          : `已保存到草稿 commit ${receipt.commit}，尚未部署。` } }
      if (versions.current.snapshot) versions.current = { ...versions.current, snapshot: { ...versions.current.snapshot, draftHead: receipt.commit } }
      // A current-based save writes only wf/. Restore current's real tree and
      // move only post-dispatch edits to the newly verified saved snapshot. The
      // unchanged-other-version check above prevents overwriting another draft.
      if (selected === 'current') versions.current = { ...versions.current, files: structuredClone(base.versionFiles) }
      publish(session, { ...session.state, version: migrateCurrent ? 'draft' : session.state.version, versions })
      return { mode: 'unified', dbSaved: true, gitSaved: true, commit: receipt.commit }
    } catch (cause) {
      if (owner) abandonEditorWorkflowWrite(id, owner)
      if (writeStarted) {
        const message = `统一保存未能完整确认：${cause instanceof Error ? cause.message : '结果未知'} 本地草稿仍保留；请人工核对数据库和草稿(wf)分支，勿重复提交。刷新不会清除写入风险标记。`
        publish(session, { ...session.state, postSaveVerificationError: message })
        throw new PackCanvasPartialSaveError(id, message)
      }
      throw cause
    } finally { busy.current = false; publish(session, { ...session.state, saving: false }) }
  }
  // Legacy pack-version canWrite is not a unified-save capability. Until the
  // host exposes one, never advertise unified write availability in this hook.
  return { save: () => performSave(), prepareReconciliation, currentReconciliation, cancelReconciliation, saveReconciled: (approval: ReconciliationApproval) => performSave(undefined, approval), error: state?.uncertainRename ?? state?.postSaveVerificationError ?? current?.error ?? null, versionError: current?.error ?? null,
    loading: !!current?.loading, canWrite: false, saving: !!state?.saving, snapshot,
    baselineComparison: current?.loading || current?.readFailure || current?.blocked ? 'unavailable' as const : baselineComparison }
}
