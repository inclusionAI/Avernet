import { useEffect, useRef, useSyncExternalStore } from 'react'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from './store'
import { sessionFor, load, releaseCleanSession, publish, dirty, diff, updateVersion, type Version, type Session } from './packVersionSession'
import { packVersionWritesEnabled } from './packVersionWritePolicy'
import { savePackVersion } from './packVersionSave'
import { packWorkflow } from './packCanvas'
import { normalizationChanges } from './yamlDraft'
import { saveVerifiedWorkflowDocument } from './workflowDocumentSave'

// A split identity write requires explicit manual reconciliation before another save.
// This guard is deliberately session-local, not a durable recovery journal.
const uncertainRenames = new WeakMap<Session, string>()

/** DB owns the workflow document; Git owns the other Pack files. */
export function usePackCanvasSession(id: string | null, _databaseSpec?: WorkflowSpec | null, { enabled = true }: { enabled?: boolean } = {}) {
  const session = id ? sessionFor(id) : null
  const state = useSyncExternalStore(listener => {
    session?.listeners.add(listener)
    return () => { session?.listeners.delete(listener) }
  }, () => session?.state ?? null, () => null)
  const editor = useEditorStore()
  if (session) session.canvasDirty = editor.isDirty || editor.isLayoutDirty || Object.keys(editor.pendingEdits ?? {}).length > 0
  const version = state?.version ?? 'current'
  const current = state?.versions[version]
  const snapshot = current?.snapshot
  const busy = useRef(false)

  useEffect(() => {
    if (enabled && session && id && !current?.snapshot && !current?.loading && !current?.error) load(session, id, version)
  }, [enabled, session, id, version, current?.snapshot, current?.loading, current?.error])
  useEffect(() => () => { if (session && id) queueMicrotask(() => releaseCleanSession(id, session)) }, [session, id])
  useEffect(() => {
    if (!enabled || !session || !snapshot || session.canvasInitialized) return
    session.canvasInitialized = true
    if (version === 'current' && snapshot.draftHead !== null && snapshot.draftHead !== snapshot.baseCommit
      && !dirty(current!) && !state?.reconciliation) publish(session, { ...session.state, version: 'draft' })
  }, [enabled, session, snapshot, version, current, state?.reconciliation])

  async function save() {
    const live = useEditorStore.getState()
    if (!id || !session) throw new Error('首次新建尚无法安全初始化 Git saved 分支；本地草稿仍保留，请先导出备份。')
    const uncertainRename = uncertainRenames.get(session)
    if (uncertainRename) throw new Error(uncertainRename)
    if (busy.current || session.state.saving || session.state.uploading || session.state.reconciliation || session.state.deployment) throw new Error('保存、上传或部署状态未完成；本地草稿仍保留。')
    if (Object.keys(live.pendingEdits ?? {}).length) throw new Error('存在未应用的配置草稿；请先应用或放弃，再保存。')
    if (live.validationErrors?.length) throw new Error(`保存前请修复验证错误：${live.validationErrors[0].message}`)
    if (!live.spec || live.spec.id !== id || !live._savedSpec) throw new Error('缺少数据库文档基线；本地草稿仍保留。')
    const databaseBaseline = live._databaseSpec === undefined ? live._savedSpec : live._databaseSpec
    const selected: Version = session.state.version
    const source = session.state.versions[selected]
    const base = source.snapshot
    const isCurrent = () => {
      const now = useEditorStore.getState(), currentSource = session.state.versions[selected]
      const nowDatabase = now._databaseSpec === undefined ? now._savedSpec : now._databaseSpec
      return now.sessionId === live.sessionId && !!now.spec && !!now._savedSpec
        && !normalizationChanges(live.spec, now.spec).length && !normalizationChanges(live._savedSpec, now._savedSpec).length
        && (databaseBaseline === null ? nowDatabase === null : nowDatabase !== null && !normalizationChanges(databaseBaseline, nowDatabase).length)
        && !Object.keys(now.pendingEdits ?? {}).length && !now.validationErrors.length && !session.state.uploading
        && session.state.version === selected && currentSource.snapshot === base && !diff(source.files, currentSource.files).length
        && !Object.entries(session.state.versions).some(([key, value]) => key !== selected && dirty(value))
    }
    if (!base || (!dirty(source) && !source.blocked && !source.loading)) {
      if (source.blocked || Object.values(session.state.versions).some(dirty)) throw new Error('文件草稿尚未处理；本地修改仍保留。')
      if (!live.isDirty) return { dbSaved: false, commit: null as string | null }
      const requestSpec = structuredClone(live.spec), sessionId = live.sessionId
      busy.current = true
      publish(session, { ...session.state, saving: true })
      try {
        const stored = await saveVerifiedWorkflowDocument(id, databaseBaseline, requestSpec, isCurrent,
          { expectedActiveDeployNumber: live._databaseSpec === null ? live._activeDeployNumber : undefined })
        const latest = useEditorStore.getState()
        if (latest.sessionId === sessionId && latest.spec?.id === id) latest.completeSave(stored, requestSpec, sessionId)
        return { dbSaved: true, commit: null as string | null }
      } finally {
        busy.current = false
        publish(session, { ...session.state, saving: false })
      }
    }
    if (source.loading || source.blocked) throw new Error(source.error ?? 'Git 版本尚未读取或不可用；本地草稿仍保留。')
    const original = packWorkflow(base.versionFiles, id, base.packId)
    const candidate = packWorkflow(source.files, id)
    const renaming = candidate.packId !== base.packId
    const originalFile = base.versionFiles.find(f => f.path === original.path)!
    const candidateFile = source.files.find(f => f.path === candidate.path)!
    // A retained old path is no longer the selected workflow. Do not silently
    // commit edits to its former YAML or discard them as part of the move.
    const retainedOldFile = candidate.path === original.path ? undefined : source.files.find(f => f.path === original.path)
    if (retainedOldFile && retainedOldFile.contentBase64 !== originalFile.contentBase64) {
      throw new Error('移动工作流路径时旧路径的 YAML 也已修改；请先恢复或删除旧文件，本地草稿仍保留。')
    }
    const fileChanged = candidateFile.contentBase64 !== originalFile.contentBase64
    const canvasChanged = normalizationChanges(live.spec, live._savedSpec).length > 0
    if (fileChanged && canvasChanged && normalizationChanges(candidate.spec, live.spec).length > 0) throw new Error('画布和文件同时修改了工作流 YAML，请先统一内容；所有草稿仍保留。')
    if (fileChanged && databaseBaseline && normalizationChanges(original.spec, databaseBaseline).length > 0 && normalizationChanges(candidate.spec, databaseBaseline).length > 0) {
      const dbPaths = normalizationChanges(original.spec, databaseBaseline)
      const candidatePaths = normalizationChanges(databaseBaseline, candidate.spec)
      const describe = (paths: string[]) => `${paths.slice(0, 5).join('、')}${paths.length > 5 ? '等' : ''}`
      const capturedState = session.state
      if (!window.confirm(`文件 YAML 基于较旧的 Git 版本，数据库草稿已有不同内容（字段：${describe(dbPaths)}）。继续将用文件候选完整替换数据库草稿，候选与数据库不同的字段：${describe(candidatePaths)}。这不是自动合并，可能丢失数据库修改。确定替换？取消可保留两份草稿并先手动合并。`)) {
        throw new Error('已取消替换数据库草稿；画布和文件修改均保留，请先手动合并。')
      }
      if (useEditorStore.getState() !== live || session.state !== capturedState) throw new Error('确认期间编辑内容已变化；未保存，请重新核对。')
    }
    const requestSpec = structuredClone(fileChanged && !canvasChanged ? candidate.spec : live.spec)
    const dbChanged = fileChanged || live.isDirty || renaming
    // Git stores the old source bytes at the selected path; edited YAML belongs
    // to the DB. Preserve the candidate path, mode and all unrelated files.
    const files = source.files.map(f => f.path === candidate.path ? { ...f, contentBase64: originalFile.contentBase64 } : f)
    const changes = diff(base.versionFiles, files)
    if (changes.length && !packVersionWritesEnabled(base)) throw new Error('此宿主未授予版本文件写入权限；所有草稿仍保留。')
    const sessionId = live.sessionId
    let dbSaved = false
    let renameDbState = '未发送'
    let gitStarted = false
    busy.current = true
    publish(session, { ...session.state, saving: true })
    try {
      if (dbChanged) {
        const stored = await saveVerifiedWorkflowDocument(id, databaseBaseline, requestSpec, isCurrent, {
          packId: candidate.packId,
          expectedActiveDeployNumber: live._databaseSpec === null ? live._activeDeployNumber : undefined,
          identityWrite: renaming ? {
          newPackId: candidate.packId,
          onStart: () => { renameDbState = '已发送，结果未知' },
          onResponse: returned => { renameDbState = `服务报告已保存 Pack ID ${returned}，文档尚未核对` },
        } : undefined })
        dbSaved = true
        if (renaming) renameDbState = `已确认 Pack ID ${candidate.packId}，文档已核对`
        const latest = useEditorStore.getState()
        if (fileChanged && (latest.sessionId !== sessionId || !latest.spec || latest.spec.id !== id
          || normalizationChanges(live.spec, latest.spec).length || Object.keys(latest.pendingEdits ?? {}).length
          || session.state.version !== selected || session.state.versions[selected].snapshot !== base
          || diff(source.files, session.state.versions[selected].files).length)) {
          throw new Error('部分成功：数据库已保存，但保存期间画布或文件已变化；当前画布、未应用草稿和文件候选仍保留，请核对。')
        }
        if (latest.sessionId === sessionId && latest.spec?.id === id) {
          if (fileChanged && !canvasChanged && !latest.isDirty) {
            latest.importYaml(stored)
            useEditorStore.getState().completeSave(stored, requestSpec, sessionId)
          } else latest.completeSave(stored, requestSpec, sessionId)
        }
        // Remove only the workflow-file edit already saved to DB. Keep all other bytes.
        const latestFiles = session.state.versions[selected].files
        if (diff(source.files, latestFiles).length) throw new Error('部分成功：数据库已保存，但保存期间文件已变化；所有当前文件仍保留，请核对后重新保存。')
        if (fileChanged) updateVersion(session, selected, { files })
      }
      if (!changes.length) return { dbSaved, commit: null as string | null }
      // The shared helper owns the Git CAS and its unknown-outcome guard.
      publish(session, { ...session.state, saving: false })
      let result: Awaited<ReturnType<typeof savePackVersion>>
      try {
        const pending = savePackVersion(session, id, selected, files)
        gitStarted = !!session.saveRequest
        result = await pending
      }
      catch (cause) {
        const detail = cause instanceof Error ? cause.message : '文件保存结果未知。'
        throw new Error(`${dbSaved ? '部分成功：工作流已保存到数据库；' : ''}${detail} 文件仍保留；请勿盲目重试。`)
      }
      if (!result) {
        const detail = session.state.versions[selected].error ?? '文件保存未完成或已取消。'
        throw new Error(`${dbSaved ? '部分成功：工作流已保存到数据库；' : ''}${detail} 文件仍保留；请勿盲目重试。`)
      }
      return { dbSaved, commit: result.commit }

    } catch (cause) {
      if (renaming && renameDbState !== '未发送') {
        const detail = cause instanceof Error ? cause.message : '操作失败。'
        const gitState = gitStarted ? 'Git 保存已发起，结果未确认；请核对 saved 分支和完整候选' : 'Git 保存未发起'
        const message = `Pack 重命名 ${base.packId} → ${candidate.packId} 未完整完成：数据库${renameDbState}；${gitState}。${detail} 请先人工核对数据库和 Git，不能自动重试。本页会话已锁定；刷新会丢失本地草稿和此锁定状态。`
        uncertainRenames.set(session, message)
        for (const key of ['current', 'draft'] as const) updateVersion(session, key, { blocked: true, error: message })
        throw new Error(message)
      }
      throw cause
    } finally {
      busy.current = false
      publish(session, { ...session.state, saving: false })
    }
  }

  return { save, error: current?.error ?? null, loading: !!current?.loading,
    canWrite: packVersionWritesEnabled(snapshot ?? null), saving: !!state?.saving, snapshot }
}
