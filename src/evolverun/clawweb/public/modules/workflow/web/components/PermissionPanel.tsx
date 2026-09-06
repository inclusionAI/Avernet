import { useState, useEffect, useCallback } from 'react'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { BotPermission, BotPermissionUpsert } from '@avernet/clawweb-shared/web/types'

interface PermissionPanelProps {
  workflowId: string
}

export default function PermissionPanel({ workflowId }: PermissionPanelProps) {
  const [permissions, setPermissions] = useState<BotPermission[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)

  // Add form state
  const [newBotOwnerId, setNewBotOwnerId] = useState('')
  const [newBotId, setNewBotId] = useState('')
  const [newCanView, setNewCanView] = useState(1)
  const [newCanExecute, setNewCanExecute] = useState(1)
  const [newCanEdit, setNewCanEdit] = useState(0)
  const [submitting, setSubmitting] = useState(false)

  const loadPermissions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await api.workflows.botPermissions.list(workflowId)
      setPermissions(list)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载权限失败')
    } finally {
      setLoading(false)
    }
  }, [workflowId])

  useEffect(() => {
    void loadPermissions()
  }, [loadPermissions])

  const handleUpsert = useCallback(
    async (data: BotPermissionUpsert) => {
      setSubmitting(true)
      try {
        await api.workflows.botPermissions.upsert(workflowId, data)
        await loadPermissions()
        setShowAdd(false)
        setNewBotOwnerId('')
        setNewBotId('')
        setNewCanView(1)
        setNewCanExecute(1)
        setNewCanEdit(0)
      } catch (err) {
        setError(err instanceof Error ? err.message : '保存权限失败')
      } finally {
        setSubmitting(false)
      }
    },
    [workflowId, loadPermissions],
  )

  const handleDelete = useCallback(
    async (permissionId: number) => {
      try {
        await api.workflows.botPermissions.delete(workflowId, permissionId)
        await loadPermissions()
      } catch (err) {
        setError(err instanceof Error ? err.message : '删除权限失败')
      }
    },
    [workflowId, loadPermissions],
  )

  const handleAddSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault()
      if (!newBotOwnerId.trim()) return
      void handleUpsert({
        botId: newBotId.trim() || null,
        botOwnerId: newBotOwnerId.trim(),
        canView: newCanView,
        canExecute: newCanExecute,
        canEdit: newCanEdit,
      })
    },
    [handleUpsert, newBotOwnerId, newBotId, newCanView, newCanExecute, newCanEdit],
  )

  const isGlobalWildcard = (perm: BotPermission) =>
    perm.botOwnerId === '*' && !perm.botId

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-gray-400">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        加载权限…
      </div>
    )
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-700">Bot 权限配置</h4>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="rounded-md border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-100"
        >
          {showAdd ? '取消' : '+ 添加权限'}
        </button>
      </div>

      {error && (
        <div className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-2 font-medium hover:text-red-900"
          >
            关闭
          </button>
        </div>
      )}

      {/* Add form */}
      {showAdd && (
        <form
          onSubmit={handleAddSubmit}
          className="mb-4 rounded-lg border border-blue-200 bg-white p-4"
        >
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600">
                botOwnerId *
              </label>
              <input
                type="text"
                value={newBotOwnerId}
                onChange={(e) => setNewBotOwnerId(e.target.value)}
                placeholder="用户 ID（* 表示所有人）"
                required
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600">
                botId
              </label>
              <input
                type="text"
                value={newBotId}
                onChange={(e) => setNewBotId(e.target.value)}
                placeholder="Bot ID（留空为 owner 级）"
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none"
              />
            </div>
          </div>
          <div className="mt-3 flex items-center gap-4 text-sm">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={newCanView === 1}
                onChange={(e) => setNewCanView(e.target.checked ? 1 : 0)}
                className="rounded border-gray-300"
              />
              查看
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={newCanExecute === 1}
                onChange={(e) => setNewCanExecute(e.target.checked ? 1 : 0)}
                className="rounded border-gray-300"
              />
              执行
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={newCanEdit === 1}
                onChange={(e) => setNewCanEdit(e.target.checked ? 1 : 0)}
                className="rounded border-gray-300"
              />
              编辑
            </label>
            <button
              type="submit"
              disabled={submitting || !newBotOwnerId.trim()}
              className="ml-auto rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {submitting ? '保存中…' : '保存'}
            </button>
          </div>
        </form>
      )}

      {/* Permission table */}
      {permissions.length === 0 ? (
        <p className="py-2 text-xs text-gray-400">暂无权限配置，所有人可访问此工作流。</p>
      ) : (
        <div className="overflow-x-auto rounded border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200 text-xs">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-500">
                  botOwnerId
                </th>
                <th className="px-3 py-2 text-left font-medium text-gray-500">
                  botId
                </th>
                <th className="px-3 py-2 text-center font-medium text-gray-500">
                  查看
                </th>
                <th className="px-3 py-2 text-center font-medium text-gray-500">
                  执行
                </th>
                <th className="px-3 py-2 text-center font-medium text-gray-500">
                  编辑
                </th>
                <th className="px-3 py-2 text-right font-medium text-gray-500">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {permissions.map((perm, idx) => (
                <tr key={`${perm.botOwnerId}-${perm.botId ?? 'owner'}-${idx}`} className={isGlobalWildcard(perm) ? 'bg-green-50' : ''}>
                  <td className="px-3 py-2 font-mono text-gray-700">
                    {perm.botOwnerId}
                    {isGlobalWildcard(perm) && (
                      <span className="ml-1.5 rounded bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800">所有人</span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-gray-500">
                    {perm.botId ?? (
                      <span className="italic text-gray-400">（owner 级）</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {perm.canView ? '✅' : '—'}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {perm.canExecute ? '✅' : '—'}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {perm.canEdit ? '✅' : '—'}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => void handleDelete(perm.id)}
                      className="text-red-500 hover:text-red-700 transition-colors"
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
