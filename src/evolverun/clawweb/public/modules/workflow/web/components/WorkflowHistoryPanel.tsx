import { useState, useEffect, useCallback } from 'react'
import { stringify as stringifyYaml } from 'yaml'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { DeployHistoryItem, VersionSnapshot } from '@avernet/clawweb-shared/web/types'
import WorkflowVersionDiff from './WorkflowVersionDiff'

interface WorkflowHistoryPanelProps {
  workflowId: string
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

export default function WorkflowHistoryPanel({ workflowId, onClose }: WorkflowHistoryPanelProps) {
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

  const loadHistory = useCallback(() => {
    setLoading(true)
    setError(null)
    api.workflows
      .getHistory(workflowId)
      .then((r) => setHistory(r.history ?? []))
      .catch((err) => setError(err instanceof Error ? err.message : '加载历史失败'))
      .finally(() => setLoading(false))
  }, [workflowId])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  const handleActivateVersion = useCallback(async (deployNumber: number, version: number) => {
    setActivating(version)
    try {
      await api.workflows.activateVersion(workflowId, version)
      loadHistory()
    } catch (err) {
      setError(err instanceof Error ? err.message : '激活版本失败')
    } finally {
      setActivating(null)
    }
  }, [workflowId, loadHistory])

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
      <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-800">版本历史</span>
          <span className="text-xs text-gray-400">{workflowId}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={loadHistory}
            className="rounded border border-gray-300 bg-white px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
          >
            刷新
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="rounded border border-gray-300 bg-white px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
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
        <div className="p-8 text-center text-sm text-gray-400">暂无部署记录</div>
      ) : (
        <div className="flex flex-1 overflow-hidden">
          {/* History list */}
          <div className="w-1/2 overflow-auto border-r border-gray-200">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-gray-50 text-gray-500">
                <tr>
                  <th className="w-8 px-2 py-1.5 text-left font-medium"></th>
                  <th className="px-2 py-1.5 text-left font-medium">版本</th>
                  <th className="px-2 py-1.5 text-left font-medium">部署</th>
                  <th className="px-2 py-1.5 text-left font-medium">状态</th>
                  <th className="px-2 py-1.5 text-left font-medium">操作</th>
                  <th className="px-2 py-1.5 text-left font-medium">时间</th>
                  <th className="px-2 py-1.5 text-left font-medium">触发者</th>
                  <th className="w-20 px-2 py-1.5 text-left font-medium">默认版本</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => {
                  const isViewing = viewDeploy === h.deployNumber
                  const isSelected = selected.includes(h.deployNumber)
                  return (
                    <tr
                      key={`${h.deployNumber}-${h.version}`}
                      className={`border-b border-gray-100 cursor-pointer ${
                        h.isActive ? 'bg-green-50/60' : ''
                      } ${isViewing ? '!bg-blue-50' : ''} hover:bg-blue-50/40`}
                      onClick={() => setViewDeploy(h.deployNumber)}
                    >
                      <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(h.deployNumber)}
                          className="cursor-pointer"
                        />
                      </td>
                      <td className="px-2 py-1.5 font-mono text-gray-800">v{h.version}</td>
                      <td className="px-2 py-1.5 font-mono text-gray-500">#{h.deployNumber}</td>
                      <td className="px-2 py-1.5">
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${actionClass(h.action)}`}>
                          {h.action}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-gray-500">{formatTime(h.gmtCreate)}</td>
                      <td className="px-2 py-1.5 text-gray-500">
                        {h.ownerId || h.botId ? [h.ownerId, h.botId].filter(Boolean).join('/') : '-'}
                      </td>
                      <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
                        {h.isActive ? (
                          <span className="inline-flex items-center gap-0.5 rounded bg-green-100 px-1.5 py-0.5 text-[10px] font-semibold text-green-700">
                            默认
                          </span>
                        ) : h.action === 'deploy' ? (
                          <button
                            onClick={() => void handleActivateVersion(h.deployNumber, h.version)}
                            disabled={activating === h.version}
                            className="rounded border border-blue-300 bg-white px-1.5 py-0.5 text-[10px] font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-50"
                            title={`将 v${h.version} 设为默认版本`}
                          >
                            {activating === h.version ? '设置中…' : '设为默认'}
                          </button>
                        ) : (
                          <span className="text-gray-300 text-[10px]">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            {/* Compare action bar */}
            <div className="sticky bottom-0 flex items-center justify-between border-t border-gray-200 bg-gray-50 px-3 py-1.5">
              <span className="text-[11px] text-gray-500">
                已选 {selected.length}/2 进行对比
              </span>
              <button
                onClick={handleCompare}
                disabled={selected.length !== 2}
                className="rounded bg-blue-600 px-2.5 py-0.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-40"
              >
                对比差异
              </button>
            </div>
          </div>

          {/* Right pane: single version snapshot OR diff */}
          <div className="flex-1 overflow-auto">
            {diffFromDeploy != null && diffToDeploy != null ? (
              <div className="flex h-full flex-col">
                <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-3 py-1">
                  <span className="text-xs font-medium text-gray-700">
                    部署对比: #{diffFromDeploy} → #{diffToDeploy}
                  </span>
                  <button
                    onClick={() => {
                      setDiffFromDeploy(null)
                      setDiffToDeploy(null)
                    }}
                    className="rounded border border-gray-300 bg-white px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
                  >
                    返回
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
                <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-3 py-1">
                  <span className="text-xs font-medium text-gray-700">
                    {snapshot
                      ? `v${snapshot.version} deploy #${snapshot.deployNumber} (${snapshot.action})`
                      : `deploy #${viewDeploy}`}
                  </span>
                </div>
                {snapshotLoading ? (
                  <div className="p-4 text-sm text-gray-500">加载中…</div>
                ) : snapshot ? (
                  <pre className="flex-1 overflow-auto bg-gray-900 p-3 font-mono text-xs leading-relaxed text-green-100">
                    {specJsonToText(snapshot.specJson)}
                  </pre>
                ) : (
                  <div className="p-4 text-sm text-gray-400">无内容</div>
                )}
              </div>
            ) : (
              <div className="flex h-full items-center justify-center p-8 text-center text-sm text-gray-400">
                点选左侧某行查看版本内容；勾选两行后点"对比差异"
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}