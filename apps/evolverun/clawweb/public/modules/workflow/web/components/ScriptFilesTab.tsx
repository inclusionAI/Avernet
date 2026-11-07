import { useEffect, useState, useSyncExternalStore } from 'react'
import { sessionFor, publish, updateVersion, dirty, load, releaseCleanSession, observeReconciliationHead, cancelReconciliationHead, type Version } from '../editor/packVersionSession'
import { packVersionWritesEnabled } from '../editor/packVersionWritePolicy'
import FrozenPackVersionViewer, { downloadLocalBlob } from './FrozenPackVersionViewer'
import PackVersionEditor from './PackVersionEditor'
import PackReconciliationNotice from './PackReconciliationNotice'
import { savePackVersion } from '../editor/packVersionSave'


type Props = { workflowId: string | null; onSave?: () => void; canvasDirty?: boolean; savePending?: boolean; showSaveError?: boolean; showSaveButton?: boolean }
export default function ScriptFilesTab({ workflowId, onSave, canvasDirty = false, savePending = false, showSaveError = true, showSaveButton = true }: Props) {
  if (!workflowId) return <p className="p-5 text-sm text-muted-foreground">请先保存工作流，再查看版本文件。</p>
  return <ScriptFilesForWorkflow key={workflowId} workflowId={workflowId} onSave={onSave} canvasDirty={canvasDirty} savePending={savePending} showSaveError={showSaveError} showSaveButton={showSaveButton} />
}
function ScriptFilesForWorkflow({ workflowId, onSave, canvasDirty, savePending = false, showSaveError = true, showSaveButton = true }: Props & { workflowId: string }) {
  const session = sessionFor(workflowId)
  const state = useSyncExternalStore(listener => { session.listeners.add(listener); return () => { session.listeners.delete(listener) } }, () => session.state, () => session.state)
  const [downloadError, setDownloadError] = useState(false)
  const version = state.version
  const current = state.versions[version]
  const hasChanges = Object.values(state.versions).some(dirty)

  useEffect(() => {
    if (!current.snapshot && !current.files.length && !current.loading && !current.error) load(session, workflowId, version)
  }, [session, workflowId, version, current.snapshot, current.files.length, current.loading, current.error])
  useEffect(() => {
    return () => {
      cancelReconciliationHead(session)
      // Defer eviction until React has finished unsubscribing (including Strict Mode re-mounts).
      queueMicrotask(() => {
        if (!session.listeners.size) {
          for (const controller of Object.values(session.requests)) controller?.abort()
          for (const key of ['current', 'draft'] as const) {
            if (session.state.versions[key].loading) updateVersion(session, key, { loading: false })
          }
        }
        releaseCleanSession(workflowId, session)
      })
    }
  }, [session, workflowId])

  function switchVersion(next: Version) {
    if (next === version || state.saving || state.uploading) return
    if (canvasDirty && !(current.readFailure && !current.snapshot && current.files.length === 0)) {
      const before = session.state
      if (!window.confirm('画布有未保存修改或未应用草稿。切换仅查看另一份文件候选，不会合并或丢弃画布和文件修改。确定切换？') || session.state !== before) return
    }
    publish(session, { ...session.state, version: next })
  }
  function discardCandidate() {
    const before = session.state, selected = before.versions[before.version]
    if (before.saving || before.uploading || before.reconciliation || before.deployment || before.uncertainRename || selected.loading || !selected.snapshot || !dirty(selected)) return
    if (!window.confirm('仅放弃当前版本的文件修改并恢复其本地 Git 基线？其他版本候选和画布修改会保留。请先导出需要保留的候选。') || session.state !== before) return
    updateVersion(session, before.version, { files: selected.snapshot.versionFiles.map(file => ({ ...file })), notice: '当前版本文件已恢复到本地基线；其他候选和画布未改变。' })
  }
  function downloadCandidate() {
    const selected = session.state.versions[session.state.version]
    if (!selected.snapshot) return
    const json = JSON.stringify({ format: 'pack-version-candidate-v1', workflowId, sourceVersion: session.state.version, sourceCommit: selected.snapshot.baseCommit, expectedSavedHead: selected.snapshot.draftHead, files: selected.files })
    setDownloadError(!downloadLocalBlob(new Blob([json], { type: 'application/json' }), `pack-${session.state.version}-candidate.json`))
  }
  function retry() {
    if (state.saving || state.uploading || state.reconciliation) return
    if (!current.snapshot && current.files.length) { window.alert('文件缺少原始基线，不能重新读取覆盖；本地文件仍保留，请先人工核对。'); return }
    if (canvasDirty && !(current.readFailure && !current.snapshot && current.files.length === 0)) { window.alert('画布有未保存修改，不能重新读取并替换 Git 基线。'); return }
    if (dirty(current) && !window.confirm('重新读取会丢弃此版本的未保存更改，确定继续？')) return
    load(session, workflowId, version)
  }
  function save() {
    if (savePending) return // A workflow or Pack save owns the shared document write.
    if (onSave) onSave()
    else void savePackVersion(session, workflowId, version)
  }

  return <div className="h-full min-w-0 overflow-auto p-4">
    {current.snapshot && !current.loading && !current.readFailure && <p className="mb-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">{packVersionWritesEnabled(current.snapshot) ? '可编辑 Pack 版本文件；保存结果受宿主权限和保存模式约束，请以保存回执为准。保存不会部署。' : 'Pack 版本文件写入尚未启用；本界面不能保存或部署。可以临时编辑，但更改仅留在本页面内存中。'}当前版来自当前生效 tag；草稿(wf)保存不会改变生产版本。未保存更改在本页面会话中保留，刷新或关闭页面会丢失。</p>}
    <div className="mb-3 flex flex-wrap items-center gap-3">
      <div role="group" aria-label="脚本版本" className="inline-flex rounded-lg bg-muted/50 p-1 text-[11px]">
        <button type="button" disabled={state.saving || state.uploading} aria-pressed={version === 'current'} onClick={() => switchVersion('current')} className="inline-flex h-[31px] cursor-pointer items-center rounded-[7px] px-3 font-semibold text-muted-foreground transition-colors hover:bg-card hover:text-foreground aria-pressed:bg-card aria-pressed:text-foreground aria-pressed:shadow-sm disabled:cursor-not-allowed disabled:opacity-40">当前版（生效 tag）{dirty(state.versions.current) ? ' *' : ''}</button>
        <button type="button" disabled={state.saving || state.uploading} aria-pressed={version === 'draft'} onClick={() => switchVersion('draft')} className="inline-flex h-[31px] cursor-pointer items-center rounded-[7px] px-3 font-semibold text-muted-foreground transition-colors hover:bg-card hover:text-foreground aria-pressed:bg-card aria-pressed:text-foreground aria-pressed:shadow-sm disabled:cursor-not-allowed disabled:opacity-40">草稿版（wf）{dirty(state.versions.draft) ? ' *' : ''}</button>
      </div>
      {(current.error || current.snapshot) && <button type="button" disabled={state.saving || state.uploading || !!state.reconciliation || current.loading} onClick={retry} className="inline-flex h-[31px] cursor-pointer items-center justify-center rounded-[7px] border border-input bg-card px-3 text-[11px] font-semibold text-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40">重新读取</button>}
      {current.snapshot && dirty(current) && !state.reconciliation && <>
        <button type="button" className="rounded-lg border border-input bg-card px-3 py-2 text-[11px] font-semibold disabled:opacity-40" disabled={state.saving || state.uploading || current.loading} onClick={downloadCandidate}>导出本版本候选（JSON）</button>
        <button type="button" className="rounded-lg border border-input bg-card px-3 py-2 text-[11px] font-semibold disabled:opacity-40" disabled={state.saving || state.uploading || current.loading || !!state.deployment || !!state.uncertainRename} onClick={discardCandidate}>放弃本版本文件修改</button>
      </>}
      {downloadError && !state.reconciliation && <span role="alert">导出失败；本地候选仍保留。</span>}
      {hasChanges && <span role="status" className="inline-flex h-[22px] items-center rounded-full bg-primary/10 px-2 font-mono text-[10px] text-primary">有未保存的更改。</span>}
      {state.uploading && <span role="status" className="inline-flex h-[22px] items-center rounded-full bg-warning/10 px-2 font-mono text-[10px] text-warning">正在读取上传文件，请等待完成后再离开或保存。</span>}
      {current.snapshot && <span className="ml-auto max-w-full truncate rounded-lg border border-border bg-card px-2.5 py-1.5 font-mono text-[10px] text-muted-foreground">Pack：{current.snapshot.packId} · {current.snapshot.baseRef} · commit {current.snapshot.baseCommit}</span>}
    </div>
    {state.reconciliation && <PackReconciliationNotice reconciliation={state.reconciliation} busy={state.saving || state.uploading} downloadError={downloadError} onDownload={() => {
      const attempt = session.state.reconciliation
      if (!attempt) return
      const json = JSON.stringify({ format: 'pack-version-candidate-v1', workflowId, sourceVersion: attempt.version, sourceCommit: attempt.sourceCommit, expectedSavedHead: attempt.expectedSavedHead, requestId: attempt.attempt.requestId, identity: attempt.identity, files: attempt.attemptedFiles })
      setDownloadError(!downloadLocalBlob(new Blob([json], { type: 'application/json' }), 'pack-version-candidate.json'))
    }} onObserveHead={() => void observeReconciliationHead(session, workflowId)} />}
    {current.notice && <p role="status" className="mb-2 inline-flex h-[22px] items-center rounded-full bg-primary/10 px-2 font-mono text-[10px] text-primary">{current.notice}</p>}
    {showSaveError && (current.readFailure || current.error) && <p role="alert" className="mb-2 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive">{current.readFailure ? `${current.readFailure} 保存已阻止，请重新读取并核对；本地更改仍保留。` : current.error}</p>}
    {!current.snapshot && current.files.length > 0 && <p role="alert">文件缺少原始基线；本地文件仍保留，保存和部署已阻止，请先人工核对。</p>}
    {current.loading && <p role="status" className="mb-2 inline-flex h-[22px] items-center rounded-full bg-muted px-2 font-mono text-[10px] text-muted-foreground">正在读取版本文件…</p>}
    {current.snapshot && <>
      {state.reconciliation && <p className="mb-2 text-[11px] text-muted-foreground">对账期间文件已锁定，原始尝试和其他版本的本地更改将保留到对账完成。</p>}
      {state.reconciliation
        ? <FrozenPackVersionViewer key={version} files={version === state.reconciliation.version ? state.reconciliation.attemptedFiles : current.files} />
        : <fieldset disabled={state.saving || state.uploading || current.loading}>
          <PackVersionEditor key={version} files={current.files} versionSource={{ version, packId: current.snapshot.packId, baseRef: current.snapshot.baseRef, baseCommit: current.snapshot.baseCommit }} saveStatus={state.saving ? 'saving' : dirty(current) ? 'dirty' : 'clean'} onUploadStateChange={uploading => {
            publish(session, { ...session.state, uploading })
          }} onChange={files => {
            if (!session.state.saving && !session.state.reconciliation && !session.state.versions[version].loading) updateVersion(session, version, { files, notice: null })
          }} />
        </fieldset>}
      {showSaveButton && <button type="button" disabled={savePending || !packVersionWritesEnabled(current.snapshot) || (!dirty(current) && !canvasDirty) || current.blocked || !!current.readFailure || state.uncertainRename !== null || !!state.reconciliation || state.saving || state.uploading || !!state.deployment || current.loading} onClick={() => void save()} className="mt-3 inline-flex h-[31px] cursor-pointer items-center justify-center rounded-[7px] border border-primary bg-primary px-4 text-[11px] font-semibold text-primary-foreground transition-colors hover:border-primary hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40">{state.saving ? '正在保存…' : '保存到草稿'}</button>}
    </>}
  </div>
}
