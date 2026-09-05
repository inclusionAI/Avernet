import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { HttpCallbackConfig, NotifyEvent } from '../types'

const ALL_NOTIFY_EVENTS: { value: NotifyEvent; label: string }[] = [
  { value: 'workflow_started', label: '工作流启动' },
  { value: 'node_started', label: '节点开始' },
  { value: 'node_succeeded', label: '节点成功' },
  { value: 'node_failed', label: '节点失败' },
  { value: 'node_skipped', label: '节点跳过' },
  { value: 'workflow_succeeded', label: '工作流成功' },
  { value: 'workflow_failed', label: '工作流失败' },
]

/**
 * Reserved workflow id used by platform-level HTTP callback configs.
 * Stored in `http_callback_configs.workflow_id` as a sentinel value; not a real
 * workflow. Server-side workflow creation/import rejects this prefix pattern.
 */
export const PLATFORM_WORKFLOW_ID = '__platform__'

interface HttpCallbackPanelProps {
  workflowId: string
  /**
   * When true, the panel operates on the platform-level (`__platform__`)
   * configuration rather than a specific workflow. Affects titles, copy,
   * and the description hint shown to admins.
   */
  isPlatform?: boolean
}

interface FormData {
  name: string
  url: string
  secret: string
  enabled: boolean
  notifyOn: NotifyEvent[]
  timeoutMs: number
  maxRetries: number
  retryDelayMs: number
  includeNodeOutput: boolean
}

const DEFAULT_FORM: FormData = {
  name: '',
  url: '',
  secret: '',
  enabled: true,
  notifyOn: ['workflow_started', 'workflow_succeeded', 'workflow_failed'],
  timeoutMs: 5000,
  maxRetries: 2,
  retryDelayMs: 1000,
  includeNodeOutput: false,
}

function configToForm(cfg: HttpCallbackConfig): FormData {
  return {
    name: cfg.name,
    url: cfg.url,
    secret: cfg.secret,
    enabled: cfg.enabled,
    notifyOn: Array.isArray(cfg.notifyOn) ? cfg.notifyOn : [],
    timeoutMs: cfg.timeoutMs,
    maxRetries: cfg.maxRetries,
    retryDelayMs: cfg.retryDelayMs,
    includeNodeOutput: cfg.includeNodeOutput,
  }
}

export default function HttpCallbackPanel({ workflowId, isPlatform = false }: HttpCallbackPanelProps) {
  const [configs, setConfigs] = useState<HttpCallbackConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Edit/add state: null = list view, 'new' = adding, string = editing configId
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<FormData>(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)

  const loadConfigs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await api.workflows.callbackConfigs.list(workflowId)
      setConfigs(Array.isArray(rows) ? rows : [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载回调配置失败')
    } finally {
      setLoading(false)
    }
  }, [workflowId])

  useEffect(() => {
    void loadConfigs()
  }, [loadConfigs])

  const startAdd = useCallback(() => {
    setForm(DEFAULT_FORM)
    setEditingId('new')
    setError(null)
  }, [])

  const startEdit = useCallback((cfg: HttpCallbackConfig) => {
    setForm(configToForm(cfg))
    setEditingId(cfg.configId)
    setError(null)
  }, [])

  const cancelEdit = useCallback(() => {
    setEditingId(null)
    setError(null)
  }, [])

  const toggleNotifyOn = useCallback((event: NotifyEvent) => {
    setForm((prev) => ({
      ...prev,
      notifyOn: prev.notifyOn.includes(event)
        ? prev.notifyOn.filter((e) => e !== event)
        : [...prev.notifyOn, event],
    }))
  }, [])

  const handleSave = useCallback(async () => {
    if (!form.name.trim() || !form.url.trim()) {
      setError('名称和 URL 为必填项')
      return
    }
    if (form.notifyOn.length === 0) {
      setError('至少选择一个通知事件')
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (editingId === 'new') {
        const configId = `hcb_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
        await api.workflows.callbackConfigs.create(workflowId, {
          configId,
          workflowId,
          name: form.name.trim(),
          url: form.url.trim(),
          ...(form.secret.trim() ? { secret: form.secret.trim() } : {}),
          enabled: form.enabled,
          notifyOn: form.notifyOn,
          timeoutMs: form.timeoutMs,
          maxRetries: form.maxRetries,
          retryDelayMs: form.retryDelayMs,
          includeNodeOutput: form.includeNodeOutput,
        })
      } else {
        await api.workflows.callbackConfigs.update(workflowId, editingId, {
          name: form.name.trim(),
          url: form.url.trim(),
          ...(form.secret.trim() ? { secret: form.secret.trim() } : {}),
          enabled: form.enabled,
          notifyOn: form.notifyOn,
          timeoutMs: form.timeoutMs,
          maxRetries: form.maxRetries,
          retryDelayMs: form.retryDelayMs,
          includeNodeOutput: form.includeNodeOutput,
        })
      }
      setEditingId(null)
      await loadConfigs()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }, [editingId, form, workflowId, loadConfigs])

  const handleDelete = useCallback(async (configId: string) => {
    if (!confirm('确定删除此回调配置？')) return
    setSaving(true)
    setError(null)
    try {
      await api.workflows.callbackConfigs.delete(workflowId, configId)
      await loadConfigs()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    } finally {
      setSaving(false)
    }
  }, [workflowId, loadConfigs])

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-gray-400">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-teal-600" />
        加载回调配置…
      </div>
    )
  }

  // Edit/add form view
  if (editingId !== null) {
    return (
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h4 className="text-sm font-medium text-gray-700">
            {editingId === 'new'
              ? (isPlatform ? '➕ 新增平台级 HTTP 回调' : '➕ 新增 HTTP 回调')
              : '✏️ 编辑 HTTP 回调'}
          </h4>
          <button
            onClick={cancelEdit}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
          >
            取消
          </button>
        </div>

        {error && (
          <div className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
            <button onClick={() => setError(null)} className="ml-2 font-medium hover:text-red-900">关闭</button>
          </div>
        )}

        {/* Basic config */}
        <div className="mb-4 rounded-lg border border-teal-200 bg-white p-4">
          <h5 className="mb-2 text-xs font-semibold text-teal-700">📡 基本配置</h5>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600">名称 *</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                placeholder="如：审批系统回调"
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600">URL *</label>
              <input
                type="url"
                value={form.url}
                onChange={(e) => setForm((p) => ({ ...p, url: e.target.value }))}
                placeholder="https://example.com/webhook"
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm font-mono focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none"
              />
            </div>
          </div>
          <div className="mt-3">
            <label className="block text-xs font-medium text-gray-600">签名密钥 (Secret) <span className="text-gray-400 font-normal">（可选）</span></label>
            <input
              type="password"
              value={form.secret}
              onChange={(e) => setForm((p) => ({ ...p, secret: e.target.value }))}
              placeholder={editingId === 'new' ? '留空则不携带 HMAC-SHA256 签名' : '留空则不修改'}
              className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm font-mono focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none"
            />
          </div>
          <div className="mt-3 flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm((p) => ({ ...p, enabled: e.target.checked }))}
                className="rounded border-gray-300"
              />
              启用
            </label>
          </div>
        </div>

        {/* Notify events */}
        <div className="mb-4 rounded-lg border border-indigo-200 bg-white p-4">
          <h5 className="mb-2 text-xs font-semibold text-indigo-700">🔔 通知事件（notifyOn）</h5>
          <div className="flex flex-wrap gap-2">
            {ALL_NOTIFY_EVENTS.map((ev) => (
              <button
                key={ev.value}
                type="button"
                onClick={() => toggleNotifyOn(ev.value)}
                className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                  form.notifyOn.includes(ev.value)
                    ? 'border-indigo-400 bg-indigo-50 text-indigo-700'
                    : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50'
                }`}
              >
                {ev.label}
              </button>
            ))}
          </div>
        </div>

        {/* Advanced settings */}
        <div className="mb-4 rounded-lg border border-amber-200 bg-white p-4">
          <h5 className="mb-2 text-xs font-semibold text-amber-700">⚙️ 高级设置</h5>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600">超时 (ms)</label>
              <input
                type="number"
                value={form.timeoutMs}
                onChange={(e) => setForm((p) => ({ ...p, timeoutMs: Number(e.target.value) || 5000 }))}
                min={1000}
                step={1000}
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm font-mono focus:border-amber-500 focus:ring-1 focus:ring-amber-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600">最大重试</label>
              <input
                type="number"
                value={form.maxRetries}
                onChange={(e) => setForm((p) => ({ ...p, maxRetries: Number(e.target.value) || 2 }))}
                min={0}
                max={10}
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm font-mono focus:border-amber-500 focus:ring-1 focus:ring-amber-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600">重试间隔 (ms)</label>
              <input
                type="number"
                value={form.retryDelayMs}
                onChange={(e) => setForm((p) => ({ ...p, retryDelayMs: Number(e.target.value) || 1000 }))}
                min={100}
                step={500}
                className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm font-mono focus:border-amber-500 focus:ring-1 focus:ring-amber-500 focus:outline-none"
              />
            </div>
          </div>
          <div className="mt-3">
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={form.includeNodeOutput}
                onChange={(e) => setForm((p) => ({ ...p, includeNodeOutput: e.target.checked }))}
                className="rounded border-gray-300"
              />
              在 ext_info 中包含节点输出（includeNodeOutput）
            </label>
          </div>
        </div>

        {/* Save */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => void handleSave()}
            disabled={saving || !form.name.trim() || !form.url.trim() || form.notifyOn.length === 0}
            className="rounded-md bg-teal-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-teal-700 disabled:opacity-50"
          >
            {saving ? '保存中…' : editingId === 'new' ? '创建回调' : '更新回调'}
          </button>
        </div>
      </div>
    )
  }

  // List view
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-700">
          {isPlatform ? 'HTTP 回调通知（平台级 - 默认对所有工作流生效）' : 'HTTP 回调通知'}
        </h4>
        <button
          onClick={startAdd}
          className="rounded-md border border-teal-200 bg-teal-50 px-2.5 py-1 text-xs font-medium text-teal-700 hover:bg-teal-100"
        >
          + 新增回调
        </button>
      </div>

      {isPlatform && (
        <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          平台级回调会对所有工作流生效（工作流级同名事件配置会覆盖平台级配置）。请谨慎配置，避免大范围触发。
        </div>
      )}

      {error && (
        <div className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
          <button onClick={() => setError(null)} className="ml-2 font-medium hover:text-red-900">关闭</button>
        </div>
      )}

      {configs.length === 0 ? (
        <p className="text-xs text-gray-400">
          {isPlatform
            ? '暂无平台级回调配置，点击"新增回调"创建（将默认对所有工作流生效）。'
            : '暂无 HTTP 回调配置，点击"新增回调"创建。'}
        </p>
      ) : (
        <div className="space-y-2">
          {configs.map((cfg) => (
            <div
              key={cfg.configId}
              className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900">{cfg.name}</span>
                  <span
                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      cfg.enabled
                        ? 'bg-green-50 text-green-700'
                        : 'bg-gray-100 text-gray-500'
                    }`}
                  >
                    {cfg.enabled ? '启用' : '禁用'}
                  </span>
                </div>
                <p className="mt-0.5 truncate text-xs font-mono text-gray-400">{cfg.url}</p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {(cfg.notifyOn ?? []).map((ev) => (
                    <span
                      key={ev}
                      className="inline-flex items-center rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600"
                    >
                      {ev.replace(/_/g, ' ')}
                    </span>
                  ))}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <button
                  onClick={() => startEdit(cfg)}
                  className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-600 transition-colors hover:bg-gray-50"
                >
                  编辑
                </button>
                <button
                  onClick={() => void handleDelete(cfg.configId)}
                  disabled={saving}
                  className="rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-600 transition-colors hover:bg-red-100 disabled:opacity-50"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}