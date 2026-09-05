import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { NotificationConfig, DingTalkUserTarget, DingTalkGroupTarget } from '../types'

interface NotificationPanelProps {
  workflowId: string
}

export default function NotificationPanel({ workflowId }: NotificationPanelProps) {
  const [config, setConfig] = useState<NotificationConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Form state
  const [robotCode, setRobotCode] = useState('')
  const [appSecret, setAppSecret] = useState('')
  const [users, setUsers] = useState<DingTalkUserTarget[]>([])
  const [groups, setGroups] = useState<DingTalkGroupTarget[]>([])
  const [messageTitle, setMessageTitle] = useState('')
  const [includeRunLink, setIncludeRunLink] = useState(true)
  const [saving, setSaving] = useState(false)

  const loadConfig = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.workflows.notificationConfig.get(workflowId)
      if (data) {
        setConfig(data)
        setRobotCode(data.robotCode)
        setAppSecret(data.appSecret)
        setUsers(data.onFailureUsers)
        setGroups(data.onFailureGroups)
        setMessageTitle(data.onFailureMessageTitle ?? '')
        setIncludeRunLink(data.onFailureMessageIncludeRunLink)
      } else {
        setConfig(null)
        setRobotCode('')
        setAppSecret('')
        setUsers([])
        setGroups([])
        setMessageTitle('')
        setIncludeRunLink(true)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载通知配置失败')
    } finally {
      setLoading(false)
    }
  }, [workflowId])

  useEffect(() => {
    void loadConfig()
  }, [loadConfig])

  const handleSave = useCallback(async () => {
    if (!robotCode.trim() || !appSecret.trim()) {
      setError('robotCode 和 appSecret 为必填项')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await api.workflows.notificationConfig.upsert(workflowId, {
        robotCode: robotCode.trim(),
        appSecret: appSecret.trim(),
        onFailureUsers: users,
        onFailureGroups: groups,
        onFailureMessageTitle: messageTitle.trim() || null,
        onFailureMessageIncludeRunLink: includeRunLink,
      })
      await loadConfig()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }, [workflowId, robotCode, appSecret, users, groups, messageTitle, includeRunLink, loadConfig])

  const handleDelete = useCallback(async () => {
    setSaving(true)
    setError(null)
    try {
      await api.workflows.notificationConfig.delete(workflowId)
      setConfig(null)
      setRobotCode('')
      setAppSecret('')
      setUsers([])
      setGroups([])
      setMessageTitle('')
      setIncludeRunLink(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    } finally {
      setSaving(false)
    }
  }, [workflowId])

  // User list management
  const addUser = useCallback(() => {
    setUsers((prev) => [...prev, { userId: '', name: '' }])
  }, [])

  const updateUser = useCallback((index: number, field: keyof DingTalkUserTarget, value: string) => {
    setUsers((prev) =>
      prev.map((u, i) => (i === index ? { ...u, [field]: value } : u)),
    )
  }, [])

  const removeUser = useCallback((index: number) => {
    setUsers((prev) => prev.filter((_, i) => i !== index))
  }, [])

  // Group list management
  const addGroup = useCallback(() => {
    setGroups((prev) => [...prev, { openConversationId: '', name: '' }])
  }, [])

  const updateGroup = useCallback((index: number, field: keyof DingTalkGroupTarget, value: string) => {
    setGroups((prev) =>
      prev.map((g, i) => (i === index ? { ...g, [field]: value } : g)),
    )
  }, [])

  const removeGroup = useCallback((index: number) => {
    setGroups((prev) => prev.filter((_, i) => i !== index))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-gray-400">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        加载通知配置…
      </div>
    )
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-700">失败通知配置（钉钉）</h4>
        {config && (
          <button
            onClick={() => void handleDelete()}
            disabled={saving}
            className="rounded-md border border-red-200 bg-red-50 px-2.5 py-1 text-xs font-medium text-red-600 transition-colors hover:bg-red-100 disabled:opacity-50"
          >
            删除配置
          </button>
        )}
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

      {/* Robot credentials */}
      <div className="mb-4 rounded-lg border border-orange-200 bg-white p-4">
        <h5 className="mb-2 text-xs font-semibold text-orange-700">🤖 机器人凭证</h5>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600">robotCode *</label>
            <input
              type="text"
              value={robotCode}
              onChange={(e) => setRobotCode(e.target.value)}
              placeholder="企业机器人 robotCode"
              className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm font-mono focus:border-orange-500 focus:ring-1 focus:ring-orange-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600">appSecret *</label>
            <input
              type="password"
              value={appSecret}
              onChange={(e) => setAppSecret(e.target.value)}
              placeholder="企业机器人 appSecret"
              className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm font-mono focus:border-orange-500 focus:ring-1 focus:ring-orange-500 focus:outline-none"
            />
          </div>
        </div>
      </div>

      {/* Failure notification users */}
      <div className="mb-4 rounded-lg border border-blue-200 bg-white p-4">
        <div className="mb-2 flex items-center justify-between">
          <h5 className="text-xs font-semibold text-blue-700">👤 单聊通知人（onFailure.users）</h5>
          <button
            onClick={addUser}
            className="rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
          >
            + 添加
          </button>
        </div>
        {users.length === 0 ? (
          <p className="text-xs text-gray-400">暂无通知人，点击"添加"新增。</p>
        ) : (
          <div className="space-y-2">
            {users.map((user, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  type="text"
                  value={user.userId}
                  onChange={(e) => updateUser(idx, 'userId', e.target.value)}
                  placeholder="userId *"
                  className="w-36 rounded-md border border-gray-300 px-2 py-1 text-xs font-mono focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none"
                />
                <input
                  type="text"
                  value={user.name ?? ''}
                  onChange={(e) => updateUser(idx, 'name', e.target.value)}
                  placeholder="姓名（可选）"
                  className="w-28 rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none"
                />
                <button
                  onClick={() => removeUser(idx)}
                  className="text-red-400 hover:text-red-600 text-xs"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Failure notification groups */}
      <div className="mb-4 rounded-lg border border-purple-200 bg-white p-4">
        <div className="mb-2 flex items-center justify-between">
          <h5 className="text-xs font-semibold text-purple-700">👥 群聊通知群（onFailure.groups）</h5>
          <button
            onClick={addGroup}
            className="rounded-md border border-purple-200 bg-purple-50 px-2 py-0.5 text-xs font-medium text-purple-700 hover:bg-purple-100"
          >
            + 添加
          </button>
        </div>
        {groups.length === 0 ? (
          <p className="text-xs text-gray-400">暂无通知群，点击"添加"新增。</p>
        ) : (
          <div className="space-y-2">
            {groups.map((group, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  type="text"
                  value={group.openConversationId}
                  onChange={(e) => updateGroup(idx, 'openConversationId', e.target.value)}
                  placeholder="openConversationId *"
                  className="w-48 rounded-md border border-gray-300 px-2 py-1 text-xs font-mono focus:border-purple-500 focus:ring-1 focus:ring-purple-500 focus:outline-none"
                />
                <input
                  type="text"
                  value={group.name ?? ''}
                  onChange={(e) => updateGroup(idx, 'name', e.target.value)}
                  placeholder="群名（可选）"
                  className="w-28 rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-purple-500 focus:ring-1 focus:ring-purple-500 focus:outline-none"
                />
                <button
                  onClick={() => removeGroup(idx)}
                  className="text-red-400 hover:text-red-600 text-xs"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Message customization */}
      <div className="mb-4 rounded-lg border border-green-200 bg-white p-4">
        <h5 className="mb-2 text-xs font-semibold text-green-700">💬 消息自定义</h5>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600">通知标题</label>
            <input
              type="text"
              value={messageTitle}
              onChange={(e) => setMessageTitle(e.target.value)}
              placeholder='默认: "⚠️ ClawFlow 工作流失败通知"'
              className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm focus:border-green-500 focus:ring-1 focus:ring-green-500 focus:outline-none"
            />
          </div>
          <div className="flex items-end pb-1.5">
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={includeRunLink}
                onChange={(e) => setIncludeRunLink(e.target.checked)}
                className="rounded border-gray-300"
              />
              包含运行链接
            </label>
          </div>
        </div>
      </div>

      {/* Save button */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => void handleSave()}
          disabled={saving || !robotCode.trim() || !appSecret.trim()}
          className="rounded-md bg-orange-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-orange-700 disabled:opacity-50"
        >
          {saving ? '保存中…' : config ? '更新配置' : '保存配置'}
        </button>
        {config && (
          <span className="text-xs text-gray-400">
            已配置 · 更新于 {new Date(config.onFailureMessageTitle ?? '').toLocaleString('zh-CN') || '—'}
          </span>
        )}
      </div>
    </div>
  )
}