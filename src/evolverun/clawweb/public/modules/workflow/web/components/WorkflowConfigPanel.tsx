import { useState } from 'react'
import { useEditorStore } from '../editor/store'
import type { PostAction } from '@avernet/clawweb-shared/web/types'

// ── Collapsible Section (matches NodePropertyPanel style) ──
function CollapsibleSection({
  title,
  badge,
  defaultOpen = false,
  children,
}: {
  title: string
  badge?: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-md border border-gray-200">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
      >
        <span className="flex items-center gap-1.5">
          <svg className={`h-3 w-3 transition-transform ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          {title}
        </span>
        {badge && (
          <span className="rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">{badge}</span>
        )}
      </button>
      {open && <div className="border-t border-gray-200 px-3 py-2">{children}</div>}
    </div>
  )
}

function Field({ label, children, description }: { label: string; children: React.ReactNode; description?: string }) {
  return (
    <div className="mb-2">
      <label className="mb-1 block text-xs font-medium text-gray-500">
        {label}
        {description && <span className="ml-1 text-[10px] text-gray-400">({description})</span>}
      </label>
      {children}
    </div>
  )
}

// ── PostAction editor for lifecycle hooks ──
function LifecycleActionEditor({
  label,
  actions,
  onChange,
}: {
  label: string
  actions: PostAction[] | undefined
  onChange: (actions: PostAction[] | undefined) => void
}) {
  const items = Array.isArray(actions) ? actions : []
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-600">{label} ({items.length})</span>
        <button
          onClick={() => onChange([...items, { action: '' }])}
          className="text-xs text-blue-600 hover:text-blue-800"
        >
          + 添加
        </button>
      </div>
      {items.map((a, i) => (
        <div key={i} className="rounded border border-gray-100 bg-gray-50 p-2 space-y-1">
          <input
            type="text"
            value={a.id ?? ''}
            onChange={(e) => {
              const next = [...items]
              next[i] = { ...next[i], id: e.target.value || undefined }
              onChange(next)
            }}
            className="input-field text-xs"
            placeholder="Action ID"
          />
          <input
            type="text"
            value={a.action ?? ''}
            onChange={(e) => {
              const next = [...items]
              next[i] = { ...next[i], action: e.target.value }
              onChange(next)
            }}
            className="input-field text-xs"
            placeholder="Action name"
          />
          {a.args && (
            <textarea
              value={JSON.stringify(a.args, null, 2)}
              onChange={(e) => {
                try {
                  const parsed = JSON.parse(e.target.value)
                  const next = [...items]
                  next[i] = { ...next[i], args: parsed }
                  onChange(next)
                } catch { /* keep invalid */ }
              }}
              rows={3}
              className="input-field font-mono text-xs"
              placeholder="Args (JSON)"
            />
          )}
          <button
            onClick={() => {
              const next = items.filter((_, idx) => idx !== i)
              onChange(next.length > 0 ? next : undefined)
            }}
            className="text-xs text-red-500 hover:text-red-700"
          >
            移除
          </button>
        </div>
      ))}
    </div>
  )
}

export default function WorkflowConfigPanel() {
  const { spec, updateSpecField } = useEditorStore()

  if (!spec) {
    return (
      <div className="w-80 shrink-0 border-l border-gray-200 bg-white p-4">
        <p className="text-gray-400 text-sm">请选择一个工作流后再进行配置。</p>
      </div>
    )
  }

  function handleJsonFieldChange(field: string, value: string) {
    try {
      const parsed = JSON.parse(value)
      useEditorStore.getState().updateSpecField(field, parsed)
    } catch { /* keep invalid without updating */ }
  }

  // Safely read top-level fields with index signature
  const specAny = spec as Record<string, unknown>

  return (
    <div className="w-96 shrink-0 overflow-y-auto border-l border-gray-200 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-2">
        <h3 className="text-sm font-semibold text-gray-700">工作流配置</h3>
      </div>

      <div className="space-y-3 p-4">
        {/* 基本信息 */}
        <CollapsibleSection title="基本信息" defaultOpen>
          <Field label="ID">
            <input
              type="text"
              value={spec.id}
              onChange={(e) => updateSpecField('id', e.target.value)}
              className="input-field"
            />
          </Field>
          <Field label="版本">
            <input
              type="text"
              value={spec.version}
              onChange={(e) => updateSpecField('version', e.target.value)}
              className="input-field"
              placeholder="1.0.0"
            />
          </Field>
          <Field label="标题">
            <input
              type="text"
              value={spec.title}
              onChange={(e) => updateSpecField('title', e.target.value)}
              className="input-field"
            />
          </Field>
          <Field label="必填参数" description="comma-separated">
            <input
              type="text"
              value={(specAny.requiredParams as string[] ?? []).join(', ')}
              onChange={(e) => {
                const params = e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean)
                useEditorStore.getState().updateSpecField('requiredParams', params.length > 0 ? params : undefined)
              }}
              className="input-field"
              placeholder="param1, param2"
            />
          </Field>
        </CollapsibleSection>

        {/* Config (inline workflow config data, replaces configPath) */}
        <CollapsibleSection
          title="配置数据"
          badge={specAny.config ? '已配置' : undefined}
          defaultOpen={!!specAny.config}
        >
          {specAny.config ? (
            <div className="space-y-2">
              <p className="text-[10px] text-gray-400">
                内联 JSON 配置，可在节点提示词中通过 {'{{workflowData.config}}'} 使用。
              </p>
              <textarea
                value={typeof specAny.config === 'string' ? specAny.config : JSON.stringify(specAny.config, null, 2)}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value)
                    useEditorStore.getState().updateSpecField('config', parsed)
                  } catch {
                    useEditorStore.getState().updateSpecField('config', e.target.value)
                  }
                }}
                rows={10}
                className="input-field font-mono text-xs"
                placeholder='{"rules": [...], "default_action": {...}}'
              />
              <button
                onClick={() => updateSpecField('config', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除配置数据
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('config', {})}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加配置数据
            </button>
          )}
        </CollapsibleSection>

        {/* Input */}
        <CollapsibleSection
          title="输入"
          badge={spec.input ? '已配置' : undefined}
          defaultOpen={!!spec.input}
        >
          {spec.input ? (
            <div className="space-y-2">
              <Field label="模式">
                <select
                  value={spec.input.mode ?? ''}
                  onChange={(e) => {
                    updateSpecField('input', { ...spec.input, mode: e.target.value || undefined })
                  }}
                  className="input-field"
                >
                  <option value="">默认</option>
                  <option value="strict">严格</option>
                  <option value="relaxed">宽松</option>
                </select>
              </Field>
              <Field label="Schema (JSON)">
                <textarea
                  value={spec.input.schema ? JSON.stringify(spec.input.schema, null, 2) : '{}'}
                  onChange={(e) => {
                    try {
                      const parsed = JSON.parse(e.target.value)
                      updateSpecField('input', { ...spec.input, schema: parsed })
                    } catch { /* keep invalid */ }
                  }}
                  rows={6}
                  className="input-field font-mono text-xs"
                />
              </Field>
              <button
                onClick={() => updateSpecField('input', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除输入配置
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('input', { mode: 'relaxed', schema: {} })}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加输入配置
            </button>
          )}
        </CollapsibleSection>

        {/* 身份 */}
        <CollapsibleSection
          title="身份"
          badge={spec.identity ? '已配置' : undefined}
          defaultOpen={!!spec.identity}
        >
          {spec.identity ? (
            <div className="space-y-2">
              <Field label="键">
                <input
                  type="text"
                  value={spec.identity.key ?? ''}
                  onChange={(e) => updateSpecField('identity', { ...spec.identity, key: e.target.value || undefined })}
                  className="input-field"
                  placeholder="e.g. orderId"
                />
              </Field>
              <Field label="标签">
                <input
                  type="text"
                  value={spec.identity.label ?? ''}
                  onChange={(e) => updateSpecField('identity', { ...spec.identity, label: e.target.value || undefined })}
                  className="input-field"
                  placeholder="e.g. Order ID"
                />
              </Field>
              <Field label="重复策略">
                <select
                  value={spec.identity.duplicatePolicy ?? ''}
                  onChange={(e) => updateSpecField('identity', { ...spec.identity, duplicatePolicy: e.target.value || undefined })}
                  className="input-field"
                >
                  <option value="">默认（拒绝）</option>
                  <option value="reject">拒绝</option>
                  <option value="replace">替换</option>
                  <option value="ignore">忽略</option>
                </select>
              </Field>
              <button
                onClick={() => updateSpecField('identity', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除身份配置
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('identity', {})}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加身份配置
            </button>
          )}
        </CollapsibleSection>

        {/* Outputs */}
        <CollapsibleSection
          title="输出"
          badge={spec.outputs ? '已配置' : undefined}
          defaultOpen={!!spec.outputs}
        >
          {spec.outputs ? (
            <div className="space-y-2">
              <textarea
                value={JSON.stringify(spec.outputs, null, 2)}
                onChange={(e) => handleJsonFieldChange('outputs', e.target.value)}
                rows={6}
                className="input-field font-mono text-xs"
              />
              <button
                onClick={() => updateSpecField('outputs', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除输出配置
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('outputs', {})}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加输出映射
            </button>
          )}
        </CollapsibleSection>

        {/* Debug */}
        <CollapsibleSection
          title="运行摘要"
          badge={spec.debug ? '已配置' : undefined}
          defaultOpen={!!spec.debug}
        >
          {spec.debug ? (
            <div className="space-y-2">
              <Field label="摘要字段" description="逗号分隔">
                <input
                  type="text"
                  value={(spec.debug.summaryKeys ?? []).join(', ')}
                  onChange={(e) => {
                    const keys = e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean)
                    updateSpecField('debug', { ...spec.debug, summaryKeys: keys.length > 0 ? keys : undefined })
                  }}
                  className="input-field"
                  placeholder="result.status, result.score"
                />
              </Field>
              <button
                onClick={() => updateSpecField('debug', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除摘要配置
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('debug', { summaryKeys: [] })}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加摘要配置
            </button>
          )}
        </CollapsibleSection>

        {/* Defaults */}
        <CollapsibleSection
          title="默认值"
          badge={spec.defaults ? '已配置' : undefined}
          defaultOpen={!!spec.defaults}
        >
          {spec.defaults ? (
            <div className="space-y-2">
              <Field label="进度文案模板">
                <input
                  type="text"
                  value={(spec.defaults as Record<string, unknown>).progress as string ?? ''}
                  onChange={(e) => updateSpecField('defaults', { ...spec.defaults, progress: e.target.value || undefined })}
                  className="input-field"
                  placeholder="e.g. Processing..."
                />
              </Field>
              <Field label="默认用户">
                <input
                  type="text"
                  value={(spec.defaults as Record<string, unknown>).user as string ?? ''}
                  onChange={(e) => updateSpecField('defaults', { ...spec.defaults, user: e.target.value || undefined })}
                  className="input-field"
                  placeholder="e.g. system"
                />
              </Field>
              <Field label="上下文策略">
                <select
                  value={(spec.defaults as Record<string, unknown>).contextPolicy as string ?? ''}
                  onChange={(e) => updateSpecField('defaults', { ...spec.defaults, contextPolicy: e.target.value || undefined })}
                  className="input-field"
                >
                  <option value="">默认</option>
                  <option value="full">完整</option>
                  <option value="minimal">精简</option>
                  <option value="none">无</option>
                </select>
              </Field>
              <button
                onClick={() => updateSpecField('defaults', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除默认值
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('defaults', {})}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加默认值
            </button>
          )}
        </CollapsibleSection>

        {/* 协作 */}
        <CollapsibleSection
          title="协作"
          badge={spec.collaboration ? '已配置' : undefined}
          defaultOpen={!!spec.collaboration}
        >
          {spec.collaboration ? (
            <div className="space-y-2">
              <textarea
                value={JSON.stringify(spec.collaboration, null, 2)}
                onChange={(e) => handleJsonFieldChange('collaboration', e.target.value)}
                rows={6}
                className="input-field font-mono text-xs"
              />
              <button
                onClick={() => updateSpecField('collaboration', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除协作配置
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('collaboration', {})}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加协作配置
            </button>
          )}
        </CollapsibleSection>

        {/* 生命周期钩子 (preflight / onStart / onFinish) */}
        <CollapsibleSection
          title="生命周期钩子"
          badge={spec.workflow ? '已配置' : undefined}
          defaultOpen={!!spec.workflow}
        >
          {spec.workflow ? (
            <div className="space-y-3">
              <LifecycleActionEditor
                label="预检"
                actions={(spec.workflow as Record<string, unknown>).preflight as PostAction[] | undefined}
                onChange={(actions) => updateSpecField('workflow', { ...spec.workflow, preflight: actions })}
              />
              <LifecycleActionEditor
                label="启动时"
                actions={(spec.workflow as Record<string, unknown>).onStart as PostAction[] | undefined}
                onChange={(actions) => updateSpecField('workflow', { ...spec.workflow, onStart: actions })}
              />
              <LifecycleActionEditor
                label="完成时"
                actions={(spec.workflow as Record<string, unknown>).onFinish as PostAction[] | undefined}
                onChange={(actions) => updateSpecField('workflow', { ...spec.workflow, onFinish: actions })}
              />
              <button
                onClick={() => updateSpecField('workflow', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除生命周期钩子
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('workflow', {})}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加生命周期钩子
            </button>
          )}
        </CollapsibleSection>

        {/* 消息 */}
        <CollapsibleSection
          title="消息"
          badge={specAny.messages ? '已配置' : undefined}
          defaultOpen={!!specAny.messages}
        >
          {specAny.messages ? (
            <div className="space-y-2">
              <Field label="创建时">
                <input
                  type="text"
                  value={(specAny.messages as Record<string, unknown>).onCreated as string ?? ''}
                  onChange={(e) => {
                    const msgs = { ...(specAny.messages as Record<string, unknown>) }
                    if (e.target.value) msgs.onCreated = e.target.value
                    else delete msgs.onCreated
                    updateSpecField('messages', msgs)
                  }}
                  className="input-field"
                  placeholder="Workflow created message"
                />
              </Field>
              <Field label="完成时">
                <input
                  type="text"
                  value={(specAny.messages as Record<string, unknown>).onFinished as string ?? ''}
                  onChange={(e) => {
                    const msgs = { ...(specAny.messages as Record<string, unknown>) }
                    if (e.target.value) msgs.onFinished = e.target.value
                    else delete msgs.onFinished
                    updateSpecField('messages', msgs)
                  }}
                  className="input-field"
                  placeholder="Workflow finished message"
                />
              </Field>
              <button
                onClick={() => updateSpecField('messages', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除消息配置
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('messages', {})}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加消息配置
            </button>
          )}
        </CollapsibleSection>

        {/* Params (raw JSON) */}
        <CollapsibleSection
          title="参数"
          badge={spec.params ? '已配置' : undefined}
          defaultOpen={false}
        >
          {spec.params ? (
            <div className="space-y-2">
              <textarea
                value={JSON.stringify(spec.params, null, 2)}
                onChange={(e) => handleJsonFieldChange('params', e.target.value)}
                rows={6}
                className="input-field font-mono text-xs"
              />
              <button
                onClick={() => updateSpecField('params', undefined)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                移除参数
              </button>
            </div>
          ) : (
            <button
              onClick={() => updateSpecField('params', {})}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              + 添加参数
            </button>
          )}
        </CollapsibleSection>

        {/* Allowed Bots */}
        <CollapsibleSection
          title="允许执行的 Bot"
          badge={(specAny.allowedBots as unknown[] | undefined)?.length ? `${(specAny.allowedBots as unknown[]).length}` : undefined}
          defaultOpen={!!(specAny.allowedBots as unknown[] | undefined)?.length}
        >
          <Field label="Bot ID 列表" description="逗号分隔，留空表示允许任意 bot 执行">
            <input
              type="text"
              value={(specAny.allowedBots as string[] ?? []).join(', ')}
              onChange={(e) => {
                const bots = e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean)
                useEditorStore.getState().updateSpecField('allowedBots', bots.length > 0 ? bots : undefined)
              }}
              className="input-field"
              placeholder="bot-id-1, bot-id-2"
            />
          </Field>
        </CollapsibleSection>
      </div>

      <style>{`.input-field { width: 100%; border-radius: 0.375rem; border: 1px solid #d1d5db; padding: 0.25rem 0.5rem; font-size: 0.75rem; }
.input-field:focus { border-color: #3b82f6; outline: none; box-shadow: 0 0 0 1px #3b82f6; }`}</style>
    </div>
  )
}
