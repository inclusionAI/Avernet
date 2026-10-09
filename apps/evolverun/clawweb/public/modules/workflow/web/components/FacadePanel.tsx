import { useState, useCallback } from 'react'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { FacadeBinding } from '@avernet/clawweb-shared/web/types'

interface FacadePanelProps {
  workflowId: string
  facades: FacadeBinding[]
  onFacadeChanged: () => void
}

export default function FacadePanel({
  workflowId,
  facades,
  onFacadeChanged,
}: FacadePanelProps) {
  const [editingCommand, setEditingCommand] = useState<string | null>(null)
  const [editRemark, setEditRemark] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // New facade form state
  const [showAdd, setShowAdd] = useState(false)
  const [newCommand, setNewCommand] = useState('')
  const [newRemark, setNewRemark] = useState('')

  const handleUnbind = useCallback(
    async (command: string) => {
      try {
        await api.facades.delete(command)
        onFacadeChanged()
      } catch (err) {
        setError(err instanceof Error ? err.message : '解绑失败')
      }
    },
    [onFacadeChanged],
  )

  const handleUpdateRemark = useCallback(
    async (command: string) => {
      setSaving(true)
      try {
        await api.facades.update(command, { remark: editRemark })
        setEditingCommand(null)
        onFacadeChanged()
      } catch (err) {
        setError(err instanceof Error ? err.message : '更新失败')
      } finally {
        setSaving(false)
      }
    },
    [editRemark, onFacadeChanged],
  )

  const handleCreate = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      if (!newCommand.trim()) return
      setSaving(true)
      try {
        await api.facades.create({
          command: newCommand.trim(),
          workflowId,
          remark: newRemark.trim() || undefined,
        })
        setShowAdd(false)
        setNewCommand('')
        setNewRemark('')
        onFacadeChanged()
      } catch (err) {
        setError(err instanceof Error ? err.message : '创建失败')
      } finally {
        setSaving(false)
      }
    },
    [newCommand, newRemark, workflowId, onFacadeChanged],
  )

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-700">Facade 命令绑定</h4>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="rounded-md border border-purple-200 bg-purple-50 px-2.5 py-1 text-xs font-medium text-purple-700 transition-colors hover:bg-purple-100"
        >
          {showAdd ? '取消' : '+ 绑定命令'}
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

      {/* Add facade form */}
      {showAdd && (
        <form
          onSubmit={handleCreate}
          className="mb-4 rounded-lg border border-purple-200 bg-white p-4"
        >
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600">
                命令（command）*
              </label>
              <input
                type="text"
                value={newCommand}
                onChange={(e) => setNewCommand(e.target.value)}
                placeholder="/your_command"
                required
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm font-mono focus:border-purple-500 focus:ring-1 focus:ring-purple-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600">
                备注（remark）
              </label>
              <input
                type="text"
                value={newRemark}
                onChange={(e) => setNewRemark(e.target.value)}
                placeholder="命令用途说明"
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm focus:border-purple-500 focus:ring-1 focus:ring-purple-500 focus:outline-none"
              />
            </div>
          </div>
          <div className="mt-3 flex justify-end">
            <button
              type="submit"
              disabled={saving || !newCommand.trim()}
              className="rounded-md bg-purple-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-purple-700 disabled:opacity-50"
            >
              {saving ? '绑定中…' : '绑定'}
            </button>
          </div>
        </form>
      )}

      {/* Facade list */}
      {facades.length === 0 ? (
        <p className="py-2 text-xs text-gray-400">
          此工作流未绑定任何 Facade 命令。
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200 text-xs">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-500">
                  命令
                </th>
                <th className="px-3 py-2 text-left font-medium text-gray-500">
                  Pack
                </th>
                <th className="px-3 py-2 text-left font-medium text-gray-500">
                  备注
                </th>
                <th className="px-3 py-2 text-right font-medium text-gray-500">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {facades.map((facade) => (
                <tr key={facade.command}>
                  <td className="px-3 py-2 font-mono text-gray-700">
                    {facade.command}
                  </td>
                  <td className="px-3 py-2 text-gray-500">
                    {facade.packId ?? '—'}
                  </td>
                  <td className="px-3 py-2 text-gray-500">
                    {editingCommand === facade.command ? (
                      <input
                        type="text"
                        value={editRemark}
                        onChange={(e) => setEditRemark(e.target.value)}
                        className="w-full rounded border border-gray-300 px-2 py-1 text-sm focus:border-purple-500 focus:ring-1 focus:ring-purple-500 focus:outline-none"
                      />
                    ) : (
                      facade.remark ?? '—'
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      {editingCommand === facade.command ? (
                        <>
                          <button
                            onClick={() => void handleUpdateRemark(facade.command)}
                            disabled={saving}
                            className="text-purple-600 hover:text-purple-800 font-medium disabled:opacity-50"
                          >
                            {saving ? '保存中…' : '保存'}
                          </button>
                          <button
                            onClick={() => setEditingCommand(null)}
                            className="text-gray-500 hover:text-gray-700"
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            onClick={() => {
                              setEditingCommand(facade.command)
                              setEditRemark(facade.remark ?? '')
                            }}
                            className="text-blue-600 hover:text-blue-800 font-medium"
                          >
                            编辑备注
                          </button>
                          <button
                            onClick={() => void handleUnbind(facade.command)}
                            className="text-red-500 hover:text-red-700 font-medium"
                          >
                            解绑
                          </button>
                        </>
                      )}
                    </div>
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