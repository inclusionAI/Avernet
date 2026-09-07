import { useState, useCallback } from 'react'
import PermissionPanel from '@avernet/workflow/web/components/PermissionPanel'
import FacadePanel from '@avernet/workflow/web/components/FacadePanel'
import NotificationPanel from '@avernet/workflow/web/components/NotificationPanel'
import HttpCallbackPanel from '@avernet/workflow/web/components/HttpCallbackPanel'
import VersionSelector from './VersionSelector'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { FacadeBinding } from '@avernet/clawweb-shared/web/types'

interface WorkflowCardProps {
  workflowId: string
  title: string
  packId: string | null
  updatedAt: string | null
  facades: FacadeBinding[]
  onDelete: (workflowId: string) => Promise<void>
}

type ExpandPanel = 'none' | 'permissions' | 'facades' | 'notification' | 'callback' | 'version'

export default function WorkflowCard({
  workflowId,
  title,
  packId,
  updatedAt,
  facades,
  onDelete,
}: WorkflowCardProps) {
  const [expanded, setExpanded] = useState<ExpandPanel>('none')
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [activating, setActivating] = useState(false)
  const [versionError, setVersionError] = useState<string | null>(null)
  const [versionSuccess, setVersionSuccess] = useState<string | null>(null)

  const handleToggle = useCallback((panel: ExpandPanel) => {
    setExpanded((prev) => (prev === panel ? 'none' : panel))
  }, [])

  const handleDeleteClick = useCallback(() => {
    if (!confirmingDelete) {
      setConfirmingDelete(true)
      return
    }
    setDeleting(true)
    void onDelete(workflowId).finally(() => {
      setDeleting(false)
      setConfirmingDelete(false)
    })
  }, [confirmingDelete, onDelete, workflowId])

  const handleCancelDelete = useCallback(() => {
    setConfirmingDelete(false)
  }, [])

  const handleActivateVersion = useCallback(async () => {
    if (selectedVersion == null) return
    setActivating(true)
    setVersionError(null)
    setVersionSuccess(null)
    try {
      await api.workflows.activateVersion(workflowId, selectedVersion)
      setVersionSuccess(`v${selectedVersion} 已设为默认版本`)
    } catch (err) {
      setVersionError(err instanceof Error ? err.message : '激活版本失败')
    } finally {
      setActivating(false)
    }
  }, [selectedVersion, workflowId])

  const formattedDate = updatedAt
    ? new Date(updatedAt).toLocaleString('zh-CN')
    : '—'

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm transition-shadow hover:shadow-md">
      {/* Card header */}
      <div className="flex items-center gap-4 px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-gray-900">
              {title}
            </h3>
            {packId && (
              <span className="inline-flex shrink-0 items-center rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                {packId}
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-gray-400 font-mono">
            {workflowId}
          </p>
        </div>
        <div className="shrink-0 text-xs text-gray-400">
          更新: {formattedDate}
        </div>

        {/* Action buttons */}
        <div className="flex shrink-0 items-center gap-2">
          {/* Permissions toggle */}
          <button
            onClick={() => handleToggle('permissions')}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
              expanded === 'permissions'
                ? 'border-blue-300 bg-blue-50 text-blue-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-800'
            }`}
          >
            <span className="mr-1">🔒</span>权限
          </button>

          {/* Facade toggle */}
          <button
            onClick={() => handleToggle('facades')}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
              expanded === 'facades'
                ? 'border-purple-300 bg-purple-50 text-purple-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-800'
            }`}
          >
            <span className="mr-1">⚡</span>命令
            {facades.length > 0 && (
              <span className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full bg-purple-200 text-[10px] leading-none text-purple-800">
                {facades.length}
              </span>
            )}
          </button>

          {/* Notification toggle */}
          <button
            onClick={() => handleToggle('notification')}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
              expanded === 'notification'
                ? 'border-orange-300 bg-orange-50 text-orange-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-800'
            }`}
          >
            <span className="mr-1">🔔</span>通知
          </button>

          {/* HTTP Callback toggle */}
          <button
            onClick={() => handleToggle('callback')}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
              expanded === 'callback'
                ? 'border-teal-300 bg-teal-50 text-teal-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-800'
            }`}
          >
            <span className="mr-1">📡</span>回调
          </button>

          {/* Version toggle */}
          <button
            onClick={() => handleToggle('version')}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
              expanded === 'version'
                ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-800'
            }`}
          >
            <span className="mr-1">🔖</span>版本
          </button>

          {/* Delete */}
          {confirmingDelete ? (
            <>
              <button
                onClick={handleDeleteClick}
                disabled={deleting}
                className="rounded-md bg-red-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? '删除中…' : '确认删除'}
              </button>
              <button
                onClick={handleCancelDelete}
                className="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
              >
                取消
              </button>
            </>
          ) : (
            <button
              onClick={handleDeleteClick}
              className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-500 transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-600"
            >
              删除
            </button>
          )}
        </div>
      </div>

      {/* Expanded panels */}
      {expanded === 'permissions' && (
        <div className="border-t border-gray-100 bg-gray-50 px-5 py-4">
          <PermissionPanel workflowId={workflowId} />
        </div>
      )}
      {expanded === 'facades' && (
        <div className="border-t border-gray-100 bg-gray-50 px-5 py-4">
          <FacadePanel
            workflowId={workflowId}
            facades={facades}
            onFacadeChanged={() => {
              /* parent reloads data via refresh */
            }}
          />
        </div>
      )}
      {expanded === 'notification' && (
        <div className="border-t border-gray-100 bg-gray-50 px-5 py-4">
          <NotificationPanel workflowId={workflowId} />
        </div>
      )}
      {expanded === 'callback' && (
        <div className="border-t border-gray-100 bg-gray-50 px-5 py-4">
          <HttpCallbackPanel workflowId={workflowId} />
        </div>
      )}
      {expanded === 'version' && (
        <div className="border-t border-gray-100 bg-gray-50 px-5 py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1">
              <label className="mb-1 block text-xs font-medium text-gray-700">默认版本</label>
              <VersionSelector
                workflowId={workflowId}
                value={selectedVersion}
                onChange={setSelectedVersion}
                includeInactive
                disabled={activating}
                placeholder="选择要设为默认的版本…"
                className="max-w-md"
              />
            </div>
            <button
              onClick={() => void handleActivateVersion()}
              disabled={selectedVersion == null || activating}
              className="inline-flex items-center rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {activating ? '设置中…' : '设为默认'}
            </button>
          </div>
          {versionError && (
            <p className="mt-2 text-xs text-red-600">{versionError}</p>
          )}
          {versionSuccess && (
            <p className="mt-2 text-xs text-green-600">{versionSuccess}</p>
          )}
        </div>
      )}
    </div>
  )
}