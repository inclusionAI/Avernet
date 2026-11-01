import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from './store'
import { sessionFor, load, releaseCleanSession, publish, dirty, type Snapshot, type Version } from './packVersionSession'
import { packVersionWritesEnabled } from './packVersionWritePolicy'
import { mergeCanvasFiles, packWorkflow, sameExecutionSpec } from './packCanvas'
import { savePackVersion } from './packVersionSave'

type Binding = { sessionId: number; version: Version; snapshot: Snapshot; baseline: WorkflowSpec }

/** Capture at read time, never fetch/rebase during save. This consumer keeps the session alive across tabs. */
export function usePackCanvasSession(id: string | null, databaseSpec?: WorkflowSpec) {
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
  const binding = useRef<Binding | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (session && id && !current?.snapshot && !current?.loading && !current?.error) load(session, id, version)
  }, [session, id, version, current?.snapshot, current?.loading, current?.error])
  useEffect(() => () => { if (session && id) queueMicrotask(() => releaseCleanSession(id, session)) }, [session, id])
  useEffect(() => {
    const live = useEditorStore.getState()
    if (!id || !snapshot || live.spec?.id !== id) { binding.current = null; setError(null); return }
    if (!session!.canvasInitialized) {
      session!.canvasInitialized = true
      if (version === 'current' && snapshot.draftHead !== null && snapshot.draftHead !== snapshot.baseCommit
        && !live.isDirty && !Object.keys(live.pendingEdits ?? {}).length && !dirty(current!) && !state?.reconciliation) {
        publish(session!, { ...session!.state, version: 'draft' })
        return
      }
    }
    const previous = binding.current
    if (previous?.sessionId === live.sessionId && previous.snapshot === snapshot && previous.version === version) return
    try {
      const baseline = live._savedSpec ?? databaseSpec
      if (!baseline || baseline.id !== id) throw new Error('缺少读取时的数据库文档基线，无法安全保存。')
      if (previous?.sessionId === live.sessionId && live.isDirty
        && (previous.snapshot.baseCommit !== snapshot.baseCommit || previous.version !== version)) {
        throw new Error('保存冲突：画布仍基于先前 Git 版本，请先处理本地画布修改，不能替换保存基线。')
      }
      const git = packWorkflow(snapshot.versionFiles, id, snapshot.packId)
      if (!sameExecutionSpec(git.spec, baseline)) throw new Error('保存冲突：数据库初始文档与 Git 基线不一致。原布局和草稿仍保留，请明确选择 Git 版本。')
      binding.current = { sessionId: live.sessionId, version, snapshot, baseline: structuredClone(baseline) }
      setError(null)
    } catch (cause) {
      binding.current = null
      setError(cause instanceof Error ? cause.message : '无法校验画布与 Git 基线。')
    }
  }, [id, snapshot, version, editor.sessionId, databaseSpec])

  async function save() {
    const live = useEditorStore.getState()
    if (!id || !session) throw new Error('新建草稿已保留；首次保存需要服务端创建 Pack、manifest 和首个 Git snapshot 的接口，不能退回无并发保护的数据库保存。')
    if (Object.keys(live.pendingEdits ?? {}).length) throw new Error('存在未应用的配置草稿；请先应用或放弃，再保存完整版本。')
    if (session.state.deployment) throw new Error('上次部署结果尚未确认，请先查询或恢复部署操作。')
    if (session.state.uploading || session.state.saving || session.state.reconciliation) throw new Error('文件正在上传、保存或保存结果未知；请先完成当前操作。')
    if (live.validationErrors?.length) throw new Error(`保存前请修复验证错误：${live.validationErrors[0].message}`)
    const captured = binding.current
    const source = session.state.versions[session.state.version]
    if (!captured || captured.sessionId !== live.sessionId || captured.snapshot !== source.snapshot || captured.version !== session.state.version || live.spec?.id !== id) {
      throw new Error(error ?? 'Git 基线尚未读取或已经变化，请先校验画布与版本文件。')
    }
    if (!packVersionWritesEnabled(source.snapshot)) throw new Error('此宿主未授予版本写入权限（canWrite），本地草稿仍保留。')
    if (source.blocked || source.loading) throw new Error(source.error ?? 'Git 基线不可用，请先处理冲突。')
    const requestSpec = structuredClone(live.spec)
    const files = mergeCanvasFiles(captured.snapshot, source.files, captured.baseline, requestSpec)
    const savedSpec = packWorkflow(files, id).spec
    let ownedCompletion = false
    const result = await savePackVersion(session, id, captured.version, files, result => {
      const latest = useEditorStore.getState()
      if (latest.sessionId !== captured.sessionId || latest.spec?.id !== id) return
      ownedCompletion = true
      if (sameExecutionSpec(savedSpec, requestSpec)) {
        latest.completeSave(requestSpec, requestSpec, captured.sessionId)
      } else if (!latest.isDirty && !Object.keys(latest.pendingEdits ?? {}).length) {
        latest.loadSpec(savedSpec)
      }
      const now = useEditorStore.getState()
      // The next snapshot arrives in the same synchronous completion, after this callback.
      binding.current = { sessionId: now.sessionId, version: 'draft',
        snapshot: { ...captured.snapshot, packId: result.packId, baseCommit: result.commit }, baseline: structuredClone(now._savedSpec ?? savedSpec) }
    })
    return ownedCompletion ? result : null
  }

  function adoptGit() {
    if (!id || !snapshot || !session || state?.saving || state?.uploading || state?.reconciliation) return
    try {
      const incoming = packWorkflow(snapshot.versionFiles, id, snapshot.packId).spec
      const live = useEditorStore.getState()
      if (live.isDirty || Object.keys(live.pendingEdits ?? {}).length) {
        if (!window.confirm('以此 Git 基线替换画布并放弃画布草稿？Pack 文件编辑不会丢弃。')) return
        if (!live.replaceSession(incoming, live, false)) throw new Error('编辑内容已变化，未替换画布。')
      } else live.loadSpec(incoming) // Retains same-workflow DB layout.
      binding.current = null
    } catch (cause) { setError(cause instanceof Error ? cause.message : '无法加载 Git 工作流。') }
  }
  return { save, adoptGit, error: error ?? current?.error ?? null, loading: !!current?.loading,
    canWrite: packVersionWritesEnabled(snapshot ?? null), saving: !!state?.saving, snapshot }
}
