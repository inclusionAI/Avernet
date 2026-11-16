import type { Dispatch, ReactNode, SetStateAction } from 'react'
import type { WorkflowSpec, DeployHistoryItem } from '@avernet/clawweb-shared/web/types'

type Setter<T> = Dispatch<SetStateAction<T>>
interface Props {
  spec: WorkflowSpec | null; isDirty: boolean; selectedWorkflowId: string | null; latestDeploy: DeployHistoryItem | null
  deploymentBaselineLabel: string; deploymentBaselineStatus: 'loading' | 'ready' | 'error'
  comparisonUncertain: boolean; hasUndeployedChanges: boolean; workspaceTab: 'workflow' | 'scripts'
  viewMode: 'visual' | 'yaml' | 'split'; setViewMode: Setter<'visual' | 'yaml' | 'split'>; hasPendingEdits: boolean
  setActionError: (message: string | null) => void; setShowWorkflowSettings: Setter<boolean>
  canUndo: boolean; canRedo: boolean; undo: () => void; redo: () => void; saving: boolean
  aiControls: ReactNode; setShowHistory: Setter<boolean>; embedded: boolean
  showMore: boolean; setShowMore: Setter<boolean>; setShowNewDialog: Setter<boolean>; setShowYamlImport: Setter<boolean>
  setYamlInput: Setter<string>; handleImportFile: () => void; handleYamlExport: () => void
  handleOpenSaveDialog: () => Promise<void>; creationPending?: boolean; handleDeployClick: () => Promise<void>
  deployPending: boolean; canEdit: boolean; canDeploy: boolean
}

export default function EditorToolbar({ spec, isDirty, selectedWorkflowId, latestDeploy, deploymentBaselineLabel, deploymentBaselineStatus,
  comparisonUncertain, hasUndeployedChanges, workspaceTab, viewMode, setViewMode, hasPendingEdits,
  setActionError, setShowWorkflowSettings, canUndo, canRedo, undo, redo, saving,
  aiControls, setShowHistory, embedded, showMore, setShowMore, setShowNewDialog, setShowYamlImport,
  setYamlInput, handleImportFile, handleYamlExport, handleOpenSaveDialog, creationPending,
  handleDeployClick, deployPending, canEdit, canDeploy }: Props) {
  return (
    <div className="flex min-h-12 items-center justify-between gap-4 overflow-x-auto border-b border-border bg-card px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-xs font-medium text-slate-700">
            {spec ? `${spec.id}${spec.version !== undefined ? ` v${spec.version}` : ''}` : '未加载工作流'}
          </span>
          {spec?.title && <span className="hidden truncate text-xs text-slate-400 xl:inline">· {spec.title}</span>}
          {isDirty && <span className="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">未保存</span>}
          {selectedWorkflowId &&
            (latestDeploy ? (
              <span className="hidden shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500 2xl:inline">
                {deploymentBaselineLabel} v{latestDeploy.version} · deploy #{latestDeploy.deployNumber}
              </span>
            ) : (
              <span className="hidden text-[10px] text-slate-400 2xl:inline">{deploymentBaselineStatus === 'ready' ? '未发布 · 数据库暂存稿' : '生效状态未确认'}</span>
            ))}
          {comparisonUncertain && latestDeploy && (
            <span role="status" className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800">
              无法判断数据库内容与{deploymentBaselineLabel} v{latestDeploy.version} 是否有差异：历史内容来源不确定或无法无损解析
            </span>
          )}
          {hasUndeployedChanges && latestDeploy && (
            <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800">
              数据库内容与{deploymentBaselineLabel} v{latestDeploy.version} 有差异
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div hidden={workspaceTab !== 'workflow'} className={workspaceTab === 'workflow' ? 'flex rounded-lg bg-slate-100 p-0.5' : 'hidden'} aria-label="编辑器视图">
            <button
              onClick={() => setViewMode('visual')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'visual' ? 'bg-card text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              画布
            </button>
            <button
              onClick={() => setViewMode('split')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'split' ? 'bg-card text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              对照
            </button>
            <button
              onClick={() => setViewMode('yaml')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'yaml' ? 'bg-card text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              YAML
            </button>
          </div>
          {workspaceTab === 'workflow' && <><button type="button" onClick={() => {
            if (hasPendingEdits) { setActionError?.('请先应用或放弃未完成的配置草稿'); return }
            setShowWorkflowSettings(open => !open)
          }} disabled={!spec} className="rounded-lg border border-input bg-card px-3 py-1.5 text-xs text-muted-foreground disabled:opacity-40">流程设置</button>
          <button type="button" onClick={() => undo?.()} disabled={!canEdit || !canUndo || hasPendingEdits} aria-label="撤销" className="rounded px-2 py-1 text-muted-foreground disabled:opacity-30">↶</button>
          <button type="button" onClick={() => redo?.()} disabled={!canEdit || !canRedo || hasPendingEdits} aria-label="重做" className="rounded px-2 py-1 text-muted-foreground disabled:opacity-30">↷</button></>}
          {aiControls}
          <button
            type="button"
            onClick={() => setShowHistory(true)}
            disabled={!selectedWorkflowId}
            className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:text-slate-300"
          >
            版本历史
          </button>
          {!embedded && <div className="relative">
            <button type="button" aria-label="更多操作" aria-expanded={showMore} onClick={() => setShowMore((open) => !open)} className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">更多 ···</button>
            {showMore && <><button type="button" aria-label="关闭更多操作" className="fixed inset-0 z-40 cursor-default" onClick={() => setShowMore(false)} /><div role="menu" className="absolute right-0 top-full z-50 mt-2 w-44 overflow-hidden rounded-xl border border-border bg-card p-1.5 shadow-[0_16px_40px_rgba(15,23,42,0.16)]">
              <button role="menuitem" onClick={() => { setShowNewDialog(true); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">新建工作流</button>
              <button role="menuitem" disabled={!canEdit} onClick={() => { setShowYamlImport(true); setYamlInput(''); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">粘贴 YAML</button>
              <button role="menuitem" disabled={!canEdit} onClick={() => { handleImportFile(); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">导入 YAML 文件</button>
              <button role="menuitem" onClick={() => { handleYamlExport(); setShowMore(false) }} disabled={!spec} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50 disabled:text-slate-300">导出 YAML</button>
            </div></>}
          </div>}
          <button
            onClick={handleOpenSaveDialog}
            disabled={!canEdit || !spec || saving || creationPending}
            className="rounded-lg bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
          >
            {saving || creationPending ? '正在保存…' : '保存'}
          </button>
          <button
            type="button"
            onClick={handleDeployClick}
            disabled={!canDeploy || !selectedWorkflowId || deployPending}
            className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:text-slate-300"
          >
            {deployPending ? '部署中…' : '部署'}
          </button>
        </div>
      </div>
  )
}
