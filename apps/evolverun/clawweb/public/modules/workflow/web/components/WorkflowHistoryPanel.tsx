import { useState, useEffect, useCallback } from 'react'
import { stringify as stringifyYaml } from 'yaml'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { DeployHistoryItem, VersionSnapshot } from '@avernet/clawweb-shared/web/types'
import { useWorkflowAccess } from '../api/hooks'
import WorkflowVersionDiff from './WorkflowVersionDiff'

interface WorkflowHistoryPanelProps {
  workflowId: string
  onActiveVersionChange?: () => void
  onClose?: () => void
}

const ACTION_STYLE: Record<string, string> = {
  deploy: 'bg-green-100 text-green-700',
  edit: 'bg-blue-100 text-blue-700',
  rollback: 'bg-orange-100 text-orange-700',
  pull: 'bg-gray-100 text-gray-600',
  migration: 'bg-gray-100 text-gray-600',
}

function actionClass(action: string): string {
  return ACTION_STYLE[action] ?? 'bg-gray-100 text-gray-600'
}

function formatTime(epochSec: number): string {
  if (!epochSec) return '-'
  return new Date(epochSec * 1000).toLocaleString()
}

/** Best-effort: render spec_json as text. Handles the {"content":"yaml"} wrapper. */
function specJsonToText(specJson: string): string {
  try {
    const parsed = JSON.parse(specJson)
    if (parsed && typeof parsed === 'object' && typeof parsed.content === 'string' && !Array.isArray(parsed.nodes)) {
      return parsed.content
    }
    return stringifyYaml(parsed, { lineWidth: 0 })
  } catch {
    return specJson
  }
}

export default function WorkflowHistoryPanel({ workflowId, onActiveVersionChange, onClose }: WorkflowHistoryPanelProps) {
  const [history, setHistory] = useState<DeployHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Single version view (by deploy_number — each row is an independent deploy record)
  const [viewDeploy, setViewDeploy] = useState<number | null>(null)
  const [snapshot, setSnapshot] = useState<VersionSnapshot | null>(null)
  const [snapshotLoading, setSnapshotLoading] = useState(false)

  // Diff selection: selected deploy_numbers (each row is independent, no cross-row linking)
  const [selected, setSelected] = useState<number[]>([])
  const [diffFromDeploy, setDiffFromDeploy] = useState<number | null>(null)
  const [diffToDeploy, setDiffToDeploy] = useState<number | null>(null)
  const [activating, setActivating] = useState<number | null>(null)
  const { data: access } = useWorkflowAccess(workflowId)
  const canEdit = access?.canEdit === true

  const loadHistory = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.workflows.getHistory(workflowId, 50, true)
      // `edit` rows are from the old browser-save flow. They have no Git tag and
      // cannot run as a version, so do not present them as release history.
      setHistory((r.history ?? []).filter((item) => item.action !== 'edit'))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载历史失败')
    } finally {
      setLoading(false)
    }
  }, [workflowId])

  useEffect(() => {
    void loadHistory()
  }, [loadHistory])

  const handleActivateVersion = useCallback(async (deployNumber: number, version: number) => {
    setActivating(version)
    try {
      await api.workflows.activateVersion(workflowId, version)
      await loadHistory()
      onActiveVersionChange?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : '激活版本失败')
    } finally {
      setActivating(null)
    }
  }, [workflowId, loadHistory, onActiveVersionChange])

  // Reset selections on workflow change
  useEffect(() => {
    setViewDeploy(null)
    setSnapshot(null)
    setSelected([])
    setDiffFromDeploy(null)
    setDiffToDeploy(null)
  }, [workflowId])

  useEffect(() => {
    if (viewDeploy == null) {
      setSnapshot(null)
      return
    }
    setSnapshotLoading(true)
    api.workflows
      .getDeploySnapshot(workflowId, viewDeploy)
      .then(setSnapshot)
      .catch((err) => setError(err instanceof Error ? err.message : '加载版本快照失败'))
      .finally(() => setSnapshotLoading(false))
  }, [workflowId, viewDeploy])

  const toggleSelect = (deployNumber: number) => {
    setSelected((prev) => {
      if (prev.includes(deployNumber)) {
        return prev.filter((v) => v !== deployNumber)
      }
      const next = [...prev, deployNumber]
      if (next.length > 2) {
        // keep the last 2 selected
        return next.slice(-2)
      }
      return next
    })
  }

  const handleCompare = () => {
    if (selected.length !== 2) return
    // Order by deploy_number so the earlier deploy is "from"
    const [a, b] = [...selected].sort((x, y) => x - y)
    setDiffFromDeploy(a)
    setDiffToDeploy(b)
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-5 py-3">
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold tracking-tight text-slate-900">发布历史</span>
          <span className="rounded bg-slate-200/70 px-2 py-0.5 font-mono text-[11px] text-slate-500">{workflowId}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void loadHistory()}
            className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:border-slate-400 hover:text-slate-900"
          >
            刷新
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:border-slate-400 hover:text-slate-900"
            >
              关闭
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-600">{error}</div>
      )}

      {loading ? (
        <div className="p-4 text-sm text-gray-500">加载中…</div>
      ) : history.length === 0 ? (
        <div className="p-8 text-center text-sm text-gray-400">暂无已发布版本</div>
      ) : (
        <div className="flex flex-1 overflow-hidden bg-slate-100">
          <aside className="flex w-[340px] shrink-0 flex-col border-r border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-4 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-slate-400">已发布版本 · 选择两项进行比较</div>
            <div className="flex-1 overflow-y-auto px-2 py-2">
              {history.map((h) => {
                const isViewing = viewDeploy === h.deployNumber
                const isSelected = selected.includes(h.deployNumber)
                const actor = h.ownerId || h.botId ? [h.ownerId, h.botId].filter(Boolean).join('/') : '未知触发者'
                return (
                  <article key={`${h.deployNumber}-${h.version}`} onClick={() => setViewDeploy(h.deployNumber)} className={`mb-1.5 cursor-pointer rounded-lg border px-3 py-2.5 transition-colors ${isViewing ? 'border-blue-300 bg-blue-50/70 shadow-sm' : 'border-transparent hover:border-slate-200 hover:bg-slate-50'} ${h.isActive ? 'ring-1 ring-emerald-100' : ''}`}>
                    <div className="flex items-start gap-2">
                      <input type="checkbox" checked={isSelected} onClick={(e) => e.stopPropagation()} onChange={() => toggleSelect(h.deployNumber)} className="mt-1 cursor-pointer accent-blue-600" aria-label={`选择 v${h.version} 进行对比`} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-baseline gap-2"><span className="font-mono text-sm font-semibold text-slate-800">v{h.version}</span><span className="font-mono text-[11px] text-slate-400">#{h.deployNumber}</span></div>
                          {h.isActive ? <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">线上生效</span> : <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${actionClass(h.action)}`}>{h.action}</span>}
                        </div>
                        <div className="mt-1 truncate text-[11px] text-slate-500" title={actor}>{actor} · {formatTime(h.gmtCreate)}</div>
                        {canEdit && !h.isActive && h.action === 'deploy' && <button onClick={(e) => { e.stopPropagation(); void handleActivateVersion(h.deployNumber, h.version) }} disabled={activating === h.version} className="mt-2 text-[11px] font-medium text-blue-600 hover:text-blue-800 disabled:opacity-50" title={`将 v${h.version} 设置为线上生效版本`}>{activating === h.version ? '设置中…' : '设为线上生效版本'}</button>}
                      </div>
                    </div>
                  </article>
                )
              })}
            </div>
            <div className="border-t border-slate-200 bg-slate-50 px-3 py-3">
              <div className="mb-2 flex items-center justify-between text-[11px] text-slate-500"><span>已选 <strong className="text-slate-700">{selected.length}</strong>/2</span>{selected.length === 2 && <span className="text-blue-600">已准备对比</span>}</div>
              <button onClick={handleCompare} disabled={selected.length !== 2} className="w-full rounded-md bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400">查看版本差异</button>
            </div>
          </aside>

          <main className="min-w-0 flex-1 overflow-hidden bg-white">
            {diffFromDeploy != null && diffToDeploy != null ? (
              <div className="flex h-full flex-col">
                <div className="flex items-center justify-end border-b border-slate-200 bg-slate-50 px-4 py-2">
                  <button
                    onClick={() => {
                      setDiffFromDeploy(null)
                      setDiffToDeploy(null)
                    }}
                    className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:border-slate-400"
                  >
                    退出对比
                  </button>
                </div>
                {diffFromDeploy === diffToDeploy ? (
                  <div className="p-4 text-sm text-gray-400">选中的两条是同一次部署，无法对比。</div>
                ) : (
                  <div className="flex-1 overflow-hidden">
                    <WorkflowVersionDiff
                      workflowId={workflowId}
                      fromDeploy={diffFromDeploy}
                      toDeploy={diffToDeploy}
                    />
                  </div>
                )}
              </div>
            ) : viewDeploy != null ? (
              <div className="flex h-full flex-col">
                <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-2">
                  <span className="font-mono text-xs font-semibold text-slate-700">{snapshot ? `v${snapshot.version} · deploy #${snapshot.deployNumber}` : `deploy #${viewDeploy}`}</span>
                  <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600">{snapshot?.action ?? 'loading'}</span>
                </div>
                {snapshotLoading ? (
                  <div className="p-4 text-sm text-gray-500">加载中…</div>
                ) : snapshot ? (
                  <pre className="flex-1 overflow-auto bg-slate-950 p-5 font-mono text-xs leading-relaxed text-slate-200">
                    {specJsonToText(snapshot.specJson)}
                  </pre>
                ) : (
                  <div className="p-4 text-sm text-gray-400">无内容</div>
                )}
              </div>
            ) : (
              <div className="flex h-full items-center justify-center p-8 text-center">
                <div><div className="text-sm font-medium text-slate-600">选择一个版本查看内容</div><div className="mt-1 text-xs text-slate-400">勾选两个版本后，可在这里阅读精简差异</div></div>
              </div>
            )}
          </main>
        </div>
      )}
    </div>
  )
}
