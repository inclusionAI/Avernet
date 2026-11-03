import { useEffect, useState, useSyncExternalStore } from 'react'
import { sessionFor, publish, updateVersion, dirty, load, releaseCleanSession, observeReconciliationHead, cancelReconciliationHead, type Version } from '../editor/packVersionSession'
import { packVersionWritesEnabled } from '../editor/packVersionWritePolicy'
import FrozenPackVersionViewer, { downloadLocalBlob } from './FrozenPackVersionViewer'
import PackVersionEditor from './PackVersionEditor'
import { savePackVersion } from '../editor/packVersionSave'


type Props = { workflowId: string | null; onSave?: () => void; canvasDirty?: boolean; savePending?: boolean }
export default function ScriptFilesTab({ workflowId, onSave, canvasDirty = false, savePending = false }: Props) {
  if (!workflowId) return <p className="p-5 text-sm text-muted-foreground">请先保存工作流，再查看版本文件。</p>
  return <ScriptFilesForWorkflow key={workflowId} workflowId={workflowId} onSave={onSave} canvasDirty={canvasDirty} savePending={savePending} />
}
function ScriptFilesForWorkflow({ workflowId, onSave, canvasDirty, savePending = false }: Props & { workflowId: string }) {
  const session = sessionFor(workflowId)
  const state = useSyncExternalStore(listener => { session.listeners.add(listener); return () => { session.listeners.delete(listener) } }, () => session.state, () => session.state)
  const [downloadError, setDownloadError] = useState(false)
  const version = state.version
  const current = state.versions[version]
  const hasChanges = Object.values(state.versions).some(dirty)

  useEffect(() => {
    if (!current.snapshot && !current.loading && !current.error) load(session, workflowId, version)
  }, [session, workflowId, version, current.snapshot, current.loading, current.error])
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
    if (canvasDirty) { window.alert('画布有未保存修改或未应用草稿，请先处理后切换 Git 版本。'); return }
    publish(session, { ...session.state, version: next })
  }
  function retry() {
    if (state.saving || state.uploading || state.reconciliation) return
    if (canvasDirty) { window.alert('画布有未保存修改，不能重新读取并替换 Git 基线。'); return }
    if (dirty(current) && !window.confirm('重新读取会丢弃此版本的未保存更改，确定继续？')) return
    load(session, workflowId, version)
  }
  function save() {
    if (savePending) return // A workflow or Pack save owns the shared document write.
    if (onSave) onSave()
    else void savePackVersion(session, workflowId, version)
  }

  return <div className="h-full overflow-auto p-4">
    <p className="mb-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">{packVersionWritesEnabled(current.snapshot) ? '编辑完整 Pack 版本文件并保存到草稿；保存不会部署。' : 'Pack 版本文件写入尚未启用；本界面不能保存或部署。可以临时编辑，但更改仅留在本页面内存中。'}当前版（wf）是 Git 当前分支，不保证与生效 tag 一致。未保存更改在本页面会话中保留，刷新或关闭页面会丢失。</p>
    <div className="mb-3 flex flex-wrap items-center gap-3">
      <div role="group" aria-label="脚本版本" className="inline-flex rounded-lg bg-muted/50 p-1 text-[11px]">
        <button type="button" disabled={state.saving || state.uploading} aria-pressed={version === 'current'} onClick={() => switchVersion('current')} className="inline-flex h-[31px] cursor-pointer items-center rounded-[7px] px-3 font-semibold text-muted-foreground transition-colors hover:bg-card hover:text-foreground aria-pressed:bg-card aria-pressed:text-foreground aria-pressed:shadow-sm disabled:cursor-not-allowed disabled:opacity-40">当前版（wf）{dirty(state.versions.current) ? ' *' : ''}</button>
        <button type="button" disabled={state.saving || state.uploading} aria-pressed={version === 'draft'} onClick={() => switchVersion('draft')} className="inline-flex h-[31px] cursor-pointer items-center rounded-[7px] px-3 font-semibold text-muted-foreground transition-colors hover:bg-card hover:text-foreground aria-pressed:bg-card aria-pressed:text-foreground aria-pressed:shadow-sm disabled:cursor-not-allowed disabled:opacity-40">草稿版（saved）{dirty(state.versions.draft) ? ' *' : ''}</button>
      </div>
      {(current.error || current.snapshot) && <button type="button" disabled={state.saving || state.uploading || !!state.reconciliation || current.loading} onClick={retry} className="inline-flex h-[31px] cursor-pointer items-center justify-center rounded-[7px] border border-input bg-card px-3 text-[11px] font-semibold text-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40">重新读取</button>}
      {hasChanges && <span role="status" className="inline-flex h-[22px] items-center rounded-full bg-primary/10 px-2 font-mono text-[10px] text-primary">有未保存的更改。</span>}
      {state.uploading && <span role="status" className="inline-flex h-[22px] items-center rounded-full bg-warning/10 px-2 font-mono text-[10px] text-warning">正在读取上传文件，请等待完成后再离开或保存。</span>}
      {current.snapshot && <span className="ml-auto max-w-full truncate rounded-lg border border-border bg-card px-2.5 py-1.5 font-mono text-[10px] text-muted-foreground">Pack：{current.snapshot.packId} · {current.snapshot.baseRef} · commit {current.snapshot.baseCommit}</span>}
    </div>
    {state.reconciliation && <section aria-label="保存结果对账" className="mb-3 rounded border border-border p-3 text-sm">
      <p>保存结果仍未知；查看当前 HEAD 不能证明候选是否曾经写入或已被后续提交覆盖。</p>
      {state.reconciliation.identity
        ? <p className="break-all">服务返回的待对账身份：分支 {state.reconciliation.identity.branch} · commit {state.reconciliation.identity.commit}。这不代表保存成功。</p>
        : <p>没有可信的待对账分支和 commit，请勿推测候选身份。</p>}
      {state.reconciliation.observation?.kind === 'head' && <p className="break-all">读取时的远端草稿 HEAD：{state.reconciliation.observation.commit}。{state.reconciliation.identity && (state.reconciliation.identity.commit === state.reconciliation.observation.commit ? '与待对账 commit 相同。' : '与待对账 commit 不同，远端可能已再次移动。')}</p>}
      {state.reconciliation.observation?.kind === 'snapshot-not-found' && <p>服务报告未找到草稿快照或 Pack 清单。这不证明之前的写入失败或未曾发生，请在外部核对历史。</p>}
      {state.reconciliation.checkError && <p>无法读取远端 HEAD，未清除对账状态或本地草稿。</p>}
      <p>原始候选可导出为 JSON（包含路径、模式和 Base64 原始字节）；文件可能含敏感信息，请妥善保管。</p>
      <button type="button" onClick={() => {
        const attempt = session.state.reconciliation
        if (!attempt) return
        const json = JSON.stringify({ format: 'pack-version-candidate-v1', workflowId, sourceVersion: attempt.version, sourceCommit: attempt.sourceCommit, expectedSavedHead: attempt.expectedSavedHead, requestId: attempt.attempt.requestId, identity: attempt.identity, files: attempt.attemptedFiles })
        setDownloadError(!downloadLocalBlob(new Blob([json], { type: 'application/json' }), 'pack-version-candidate.json'))
      }}>下载原始尝试候选（JSON）</button>
      {downloadError && <p role="alert">下载失败；候选仍保留在当前页面会话中。</p>}
      <button type="button" disabled={state.reconciliation.checking || state.saving || state.uploading} onClick={() => void observeReconciliationHead(session, workflowId)}>{state.reconciliation.checking ? '正在读取远端 HEAD…' : '查看远端草稿 HEAD'}</button>
      {state.reconciliation.operationState && <p>保存操作状态：{state.reconciliation.operationState}。只有 completed 结果才能确认保存。</p>}
      <p className="break-all">requestId：{state.reconciliation.attempt.requestId}</p>
      <p role="alert">系统不保存操作日志，无法自动恢复或安全重试。请先人工核对 Git 的 saved 分支与候选，再重新打开页面。</p>
    </section>}
    {current.notice && <p role="status" className="mb-2 inline-flex h-[22px] items-center rounded-full bg-primary/10 px-2 font-mono text-[10px] text-primary">{current.notice}</p>}
    {current.error && <p role="alert" className="mb-2 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive">{current.error}</p>}
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
      <button type="button" disabled={savePending || !packVersionWritesEnabled(current.snapshot) || (!dirty(current) && !canvasDirty) || current.blocked || !!state.reconciliation || state.saving || state.uploading || !!state.deployment || current.loading} onClick={() => void save()} className="mt-3 inline-flex h-[31px] cursor-pointer items-center justify-center rounded-[7px] border border-primary bg-primary px-4 text-[11px] font-semibold text-primary-foreground transition-colors hover:border-primary hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40">{state.saving ? '正在保存…' : '保存到草稿'}</button>
    </>}
  </div>
}
