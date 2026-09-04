import { useState } from 'react'
import { useEditorStore, EXECUTOR_TYPES } from '../editor/store'
import { useKnowledgeBases, useValidationTemplates } from '../api/hooks'
import type { WorkflowNode, PostAction, KnowledgeItem, RetryConfig } from '../types'

// ── Executor field definitions ──
// Each entry: key = dot-path into executor, label, type, optional placeholder/description

type FieldDef = {
  key: string
  label: string
  type: 'text' | 'textarea' | 'number' | 'json' | 'select'
  options?: string[]
  placeholder?: string
  description?: string
}

const EXECUTOR_FIELDS: Record<string, FieldDef[]> = {
  'embedded-agent': [
    { key: 'executor.skillName', label: 'Skill Name', type: 'text', placeholder: 'general-agent' },
    { key: 'executor.prompt', label: 'Prompt', type: 'textarea' },
    { key: 'executor.model', label: 'Model', type: 'text', placeholder: 'gpt-4o' },
    { key: 'executor.outputMode', label: 'Output Mode', type: 'select', options: ['text', 'json'] },
    { key: 'executor.timeoutSeconds', label: 'Timeout (s)', type: 'number', placeholder: '60' },
  ],
  action: [
    { key: 'executor.tool', label: 'Tool', type: 'text' },
    { key: 'executor.action', label: 'Action', type: 'text', placeholder: 'http-request' },
    { key: 'executor.input', label: 'Input (JSON)', type: 'json' },
    { key: 'executor.args', label: 'Args (JSON)', type: 'json' },
  ],
  human: [
    { key: 'executor.prompt', label: 'Prompt', type: 'textarea' },
    { key: 'executor.waitKind', label: 'Wait Kind', type: 'text', placeholder: 'gate' },
  ],
  'loop-group': [
    { key: 'executor.maxIterations', label: 'Max Iterations', type: 'number', placeholder: '10' },
    { key: 'executor.iterationVar', label: 'Iteration Variable', type: 'text', placeholder: 'itemIndex' },
    { key: 'executor.loopOver', label: 'Loop Over (expr)', type: 'text' },
    { key: 'executor.itemName', label: 'Item Name', type: 'text', placeholder: 'item' },
  ],
  collaboration: [
    { key: 'executor.taskKind', label: 'Task Kind', type: 'text', placeholder: 'analysis' },
    { key: 'executor.skillName', label: 'Skill Name', type: 'text', placeholder: 'data-analyst' },
    { key: 'executor.message', label: 'Message', type: 'textarea' },
    { key: 'executor.routeDisplayName', label: 'Route Display Name', type: 'text' },
    { key: 'executor.timeoutSeconds', label: 'Timeout (s)', type: 'number', placeholder: '120' },
  ],
  done: [
    { key: 'executor.message', label: 'Message', type: 'textarea' },
  ],
  subagent: [
    { key: 'executor.skillName', label: 'Skill Name', type: 'text' },
    { key: 'executor.prompt', label: 'Prompt', type: 'textarea' },
    { key: 'executor.timeoutSeconds', label: 'Timeout (s)', type: 'number', placeholder: '60' },
  ],
  'bcs-route': [
    { key: 'executor.target', label: 'Target', type: 'text' },
    { key: 'executor.message', label: 'Message', type: 'textarea' },
  ],
  'baas-call': [
    { key: 'executor.mode', label: '调用模式', type: 'select', options: ['run', 'message'] },
    { key: 'executor.botId', label: 'Bot ID', type: 'text', placeholder: 'real_bot_id:staff_no', description: 'mode=message 时必填' },
    { key: 'executor.message', label: '消息', type: 'textarea' },
    { key: 'executor.apiKeyRef', label: 'API Key 环境变量', type: 'text', placeholder: 'BAAS_API_KEY' },
    { key: 'executor.baseUrl', label: 'Base URL', type: 'text', placeholder: 'https://secbaas-prod.alipay.com' },
    { key: 'executor.timeoutMs', label: '超时(ms)', type: 'number', placeholder: '120000' },
    { key: 'executor.pollIntervalMs', label: '轮询间隔(ms)', type: 'number', placeholder: '3000' },
    { key: 'executor.outputMode', label: '输出模式', type: 'select', options: ['text', 'json'] },
  ],
  'mcp-call': [
    { key: 'executor.server', label: 'MCP Server', type: 'text', placeholder: 'mcp.ant.agentix.xxx' },
    { key: 'executor.tool', label: 'Tool Name', type: 'text', placeholder: 'risk_evaluation_toolkit' },
    { key: 'executor.args', label: 'Args (JSON)', type: 'json', placeholder: '{"key": "{{value}}"}' },
    { key: 'executor.outputMode', label: 'Output Mode', type: 'select', options: ['text', 'json'] },
    { key: 'executor.timeoutMs', label: 'Timeout (ms)', type: 'number', placeholder: '30000' },
  ],
  'cli-script': [
    { key: 'executor.command', label: 'Command', type: 'text' },
    { key: 'executor.args', label: 'Args (JSON)', type: 'json' },
    { key: 'executor.outputMode', label: 'Output Mode', type: 'select', options: ['text', 'json'] },
    { key: 'executor.timeoutMs', label: 'Timeout (ms)', type: 'number', placeholder: '30000' },
  ],
  subworkflow: [
    { key: 'executor.workflowId', label: 'Workflow ID', type: 'text' },
    { key: 'executor.packId', label: 'Pack ID', type: 'text', description: 'Optional' },
  ],
  approval: [
    { key: 'executor.skillName', label: 'Skill Name', type: 'text' },
    { key: 'executor.approvalType', label: 'Approval Type', type: 'text' },
    { key: 'executor.message', label: 'Message', type: 'textarea' },
    { key: 'executor.timeoutSeconds', label: 'Timeout (s)', type: 'number', placeholder: '300' },
  ],
}

type TabId = 'basic' | 'advanced' | 'actions' | 'alerts'

export default function NodePropertyPanel() {
  const { spec, selectedNodeId, selectNode, updateNode, removeNode } = useEditorStore()
  const { data: knowledgeBases } = useKnowledgeBases(true)
  const { data: validationTemplates } = useValidationTemplates(true)
  const [activeTab, setActiveTab] = useState<TabId>('basic')
  const node: WorkflowNode | undefined = spec?.nodes.find((n) => n.id === selectedNodeId)

  if (!node || !spec) {
    return (
      <div className="w-80 shrink-0 border-l border-gray-200 bg-white p-4">
        <p className="text-gray-400 text-sm">Select a node to edit its properties.</p>
      </div>
    )
  }

  const executorType = node.executor?.type ?? ''
  const fields = EXECUTOR_FIELDS[executorType] ?? []

  function handleExecutorFieldChange(key: string, value: string) {
    const parts = key.split('.')
    if (parts.length === 2 && parts[0] === 'executor') {
      const field = parts[1]
      let parsedValue: unknown = value
      if (field === 'input' || field === 'args' || value.startsWith('{') || value.startsWith('[')) {
        try { parsedValue = JSON.parse(value) } catch { /* keep as string */ }
      }
      if (field === 'maxIterations' || field === 'timeoutSeconds' || field === 'timeoutMs' || field === 'pollIntervalMs') {
        parsedValue = value === '' ? undefined : Number(value)
      }
      updateNode(node.id, {
        executor: { ...node.executor, [field]: parsedValue },
      })
    }
  }

  function getExecutorFieldValue(key: string): string {
    const parts = key.split('.')
    if (parts.length === 2 && parts[0] === 'executor') {
      const val = (node.executor as Record<string, unknown>)?.[parts[1]]
      if (typeof val === 'string') return val
      if (val !== undefined && val !== null) return JSON.stringify(val, null, 2)
    }
    return ''
  }

  function handleExecutorTypeChange(newType: string) {
    updateNode(node.id, {
      executor: { ...node.executor, type: newType },
    })
  }

  // ── Retry handlers ──
  function handleRetryChange(field: keyof RetryConfig, value: string | number | undefined) {
    const current = (node.retry as RetryConfig | undefined) ?? {}
    updateNode(node.id, {
      retry: { ...current, [field]: value },
    })
  }

  function handleRetryRemove() {
    updateNode(node.id, { retry: undefined })
  }

  // ── Knowledge handlers ──
  function handleAddKnowledge() {
    const items: KnowledgeItem[] = Array.isArray(node.knowledge) ? [...node.knowledge] : []
    items.push({ type: 'text', content: '' })
    updateNode(node.id, { knowledge: items })
  }

  function handleKnowledgeChange(index: number, field: keyof KnowledgeItem, value: string) {
    const items = Array.isArray(node.knowledge) ? [...node.knowledge] : []
    items[index] = { ...items[index], [field]: value }
    updateNode(node.id, { knowledge: items })
  }

  function handleRemoveKnowledge(index: number) {
    const items = Array.isArray(node.knowledge) ? [...node.knowledge] : []
    items.splice(index, 1)
    updateNode(node.id, { knowledge: items.length > 0 ? items : undefined })
  }

  // ── OutputContract handler ──
  function handleOutputContractChange(value: string) {
    try {
      const parsed = JSON.parse(value)
      updateNode(node.id, { outputContract: parsed })
    } catch { /* keep invalid JSON in textarea without updating */ }
  }

  // ── Mock handler ──
  function handleMockChange(value: string) {
    try {
      const parsed = JSON.parse(value)
      updateNode(node.id, { mock: parsed })
    } catch { /* keep invalid JSON in textarea without updating */ }
  }

  // ── PostAction handlers ──
  function handleAddPostAction(type: 'onSuccess' | 'onFailure') {
    const actions: PostAction[] = Array.isArray(node[type]) ? [...(node[type] as PostAction[])] : []
    actions.push({ action: '', args: {} })
    updateNode(node.id, { [type]: actions })
  }

  function handlePostActionChange(type: 'onSuccess' | 'onFailure', index: number, field: keyof PostAction, value: string | boolean | Record<string, unknown>) {
    const actions = Array.isArray(node[type]) ? [...(node[type] as PostAction[])] : []
    actions[index] = { ...actions[index], [field]: value }
    updateNode(node.id, { [type]: actions })
  }

  function handleRemovePostAction(type: 'onSuccess' | 'onFailure', index: number) {
    const actions = Array.isArray(node[type]) ? [...(node[type] as PostAction[])] : []
    actions.splice(index, 1)
    updateNode(node.id, { [type]: actions.length > 0 ? actions : undefined })
  }

  // ── Alerting handler ──
  function handleAlertingChange(field: string, value: unknown) {
    const current = node.alerting
    updateNode(node.id, {
      alerting: { ...(current ?? {}), [field]: value },
    })
  }

  function handleAlertingRemove() {
    updateNode(node.id, { alerting: undefined })
  }

  // ── OnResult handler ──
  function handleAddOnResult() {
    const branches = Array.isArray(node.onResult) ? [...node.onResult] : []
    branches.push({ value: '', target: '' })
    updateNode(node.id, { onResult: branches })
  }

  function handleOnResultChange(index: number, field: 'value' | 'target', value: string) {
    const branches = Array.isArray(node.onResult) ? [...node.onResult] : []
    branches[index] = { ...branches[index], [field]: value }
    updateNode(node.id, { onResult: branches })
  }

  function handleRemoveOnResult(index: number) {
    const branches = Array.isArray(node.onResult) ? [...node.onResult] : []
    branches.splice(index, 1)
    updateNode(node.id, { onResult: branches.length > 0 ? branches : undefined })
  }

  // ── OnFeedback handler ──
  function handleOnFeedbackChange(field: string, value: string) {
    const current = node.onFeedback ?? {}
    updateNode(node.id, { onFeedback: { ...current, [field]: value || undefined } })
  }

  function handleOnFeedbackRemove() {
    updateNode(node.id, { onFeedback: undefined })
  }

  const tabs: { id: TabId; label: string; badge?: string }[] = [
    { id: 'basic', label: '基础' },
    { id: 'advanced', label: '高级', badge: hasAdvancedConfig(node) ? '已配置' : undefined },
    { id: 'actions', label: '动作', badge: hasPostActions(node) ? `${(node.onSuccess?.length ?? 0) + (node.onFailure?.length ?? 0)}` : undefined },
    { id: 'alerts', label: '告警', badge: node.alerting ? '已开启' : undefined },
  ]

  return (
    <div className="w-96 shrink-0 overflow-y-auto border-l border-gray-200 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-2">
        <h3 className="text-sm font-semibold text-gray-700 truncate" title={node.title}>
          {node.title || node.id}
        </h3>
        <button
          onClick={() => selectNode(null)}
          className="rounded-md border border-gray-200 bg-white p-1 text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-700"
          title="Close"
        >
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200 bg-gray-50">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex-1 px-2 py-2 text-xs font-medium transition-colors ${
              activeTab === tab.id
                ? 'border-b-2 border-blue-600 text-blue-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <span>{tab.label}</span>
            {tab.badge && (
              <span className="ml-1 inline-block rounded-full bg-blue-100 px-1.5 py-0.5 text-[9px] font-medium text-blue-700">
                {tab.badge}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="p-4 space-y-3">
        {/* ── Basic Tab ── */}
        {activeTab === 'basic' && (
          <>
            <Field label="ID">
              <input
                type="text"
                value={node.id}
                onChange={(e) => updateNode(node.id, { id: e.target.value })}
                className="input-field"
              />
            </Field>

            <Field label="Title">
              <input
                type="text"
                value={node.title}
                onChange={(e) => updateNode(node.id, { title: e.target.value })}
                className="input-field"
              />
            </Field>

            <Field label="Executor Type">
              <select
                value={executorType}
                onChange={(e) => handleExecutorTypeChange(e.target.value)}
                className="input-field"
              >
                <option value="" disabled>Select type…</option>
                {EXECUTOR_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </Field>

            <Field label="Phase">
              <input
                type="text"
                value={node.phase ?? ''}
                onChange={(e) => updateNode(node.id, { phase: e.target.value || undefined })}
                className="input-field"
                placeholder="e.g. intake, process, finalize"
              />
            </Field>

            <Field label="Depends On">
              <div className="flex flex-wrap gap-1">
                {(node.dependsOn ?? []).map((dep) => (
                  <span key={dep} className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 font-mono text-xs text-blue-700">
                    {dep}
                    <button
                      onClick={() => {
                        const newDeps = (node.dependsOn ?? []).filter((d) => d !== dep)
                        updateNode(node.id, { dependsOn: newDeps.length > 0 ? newDeps : undefined })
                      }}
                      className="text-blue-400 hover:text-red-500"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
              <DependsOnPicker
                currentNodeId={node.id}
                allNodes={spec.nodes}
                currentDeps={node.dependsOn ?? []}
                onAdd={(dep) => {
                  const newDeps = [...(node.dependsOn ?? []), dep]
                  updateNode(node.id, { dependsOn: newDeps })
                }}
              />
            </Field>

            <BranchIdField
              value={node.branchId}
              onChange={(val) => updateNode(node.id, { branchId: val })}
            />

            <div className="border-t border-gray-100 pt-2">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">
                执行器配置
              </h4>
            </div>

            {executorType === 'human' && (
              <CollapsibleSection title="门控动作" badge={node.executor?.actions ? '已配置' : undefined} defaultOpen={!!node.executor?.actions}>
                <HumanGateActionsEditor
                  actions={(node.executor as Record<string, unknown>)?.actions as Record<string, unknown> | undefined}
                  onChange={(actions) => updateNode(node.id, { executor: { ...node.executor, actions } })}
                />
              </CollapsibleSection>
            )}

            {executorType === 'collaboration' && (
              <CollapsibleSection title="路由配置" defaultOpen={false}>
                <CollaborationRouteEditor
                  executor={node.executor as Record<string, unknown>}
                  onChange={(updated) => updateNode(node.id, { executor: updated })}
                />
              </CollapsibleSection>
            )}

            {executorType === 'loop-group' && (
              <CollapsibleSection title="终止条件" defaultOpen={false}>
                <LoopUntilEditor
                  executor={node.executor as Record<string, unknown>}
                  onChange={(updated) => updateNode(node.id, { executor: updated })}
                />
              </CollapsibleSection>
            )}

            {fields
              .filter((f) => {
                // Hide fields already handled in dedicated sections
                if (executorType === 'human' && f.key === 'executor.prompt') return true
                if (executorType === 'human' && f.key === 'executor.waitKind') return true
                if (executorType === 'loop-group' && ['executor.maxIterations', 'executor.iterationVar'].includes(f.key)) return true
                if (executorType === 'collaboration' && ['executor.taskKind', 'executor.skillName', 'executor.message'].includes(f.key)) return true
                // baas-call: show botId only when mode is message
                if (executorType === 'baas-call' && f.key === 'executor.botId' && (node.executor as Record<string, unknown>)?.mode !== 'message') return false
                return true
              })
              .map((field) => (
                <Field key={field.key} label={field.label} description={field.description}>
                  {field.type === 'textarea' ? (
                    <textarea
                      value={getExecutorFieldValue(field.key)}
                      onChange={(e) => handleExecutorFieldChange(field.key, e.target.value)}
                      rows={3}
                      placeholder={field.placeholder}
                      className="input-field font-mono text-xs"
                    />
                  ) : field.type === 'json' ? (
                    <textarea
                      value={getExecutorFieldValue(field.key)}
                      onChange={(e) => handleExecutorFieldChange(field.key, e.target.value)}
                      rows={4}
                      placeholder={field.placeholder ?? '{"key": "value"}'}
                      className="input-field font-mono text-xs"
                    />
                  ) : field.type === 'select' ? (
                    <select
                      value={getExecutorFieldValue(field.key)}
                      onChange={(e) => handleExecutorFieldChange(field.key, e.target.value)}
                      className="input-field"
                    >
                      <option value="">—</option>
                      {field.options?.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type={field.type}
                      value={getExecutorFieldValue(field.key)}
                      onChange={(e) => handleExecutorFieldChange(field.key, e.target.value)}
                      placeholder={field.placeholder}
                      className="input-field"
                    />
                  )}
                </Field>
              ))}

            {/* Knowledge toggle / KB dropdown */}
            <div className="border-t border-gray-100 pt-2">
              <Field label="知识库">
                <select
                  value={node.knowledgeBaseId ?? ''}
                  onChange={(e) => {
                    const val = e.target.value
                    if (val) {
                      updateNode(node.id, { knowledgeBaseId: val, knowledge: undefined } as Partial<WorkflowNode>)
                    } else {
                      updateNode(node.id, { knowledgeBaseId: undefined } as Partial<WorkflowNode>)
                    }
                  }}
                  className="input-field"
                >
                  <option value="">None (no knowledge base)</option>
                  {knowledgeBases?.map((kb) => (
                    <option key={kb.kbId} value={kb.kbId}>
                      {kb.name} ({kb.kbId})
                    </option>
                  ))}
                </select>
              </Field>
              {node.knowledgeBaseId && (
                <Field label="Custom Query (optional)">
                  <input
                    type="text"
                    value={node.knowledgeQuery ?? ''}
                    onChange={(e) => updateNode(node.id, { knowledgeQuery: e.target.value || undefined } as Partial<WorkflowNode>)}
                    className="input-field"
                    placeholder="Override KB query keywords"
                  />
                </Field>
              )}
              {!node.knowledgeBaseId && (
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  <input
                    type="checkbox"
                    checked={node.knowledge === true || Array.isArray(node.knowledge)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        updateNode(node.id, { knowledge: true })
                      } else {
                        updateNode(node.id, { knowledge: undefined })
                      }
                    }}
                    className="rounded border-gray-300"
                  />
                  <span className="font-medium">Legacy Knowledge Injection</span>
                </label>
              )}
              {!node.knowledgeBaseId && (node.knowledge === true) && (
                <Field label="Custom Query (optional)">
                  <input
                    type="text"
                    value={node.knowledgeQuery ?? ''}
                    onChange={(e) => updateNode(node.id, { knowledgeQuery: e.target.value || undefined } as Partial<WorkflowNode>)}
                    className="input-field"
                    placeholder="Override KB query keywords"
                  />
                </Field>
              )}
            </div>

            {/* 验证模板 */}
            <div className="border-t border-gray-100 pt-2">
              <Field label="验证模板">
                <select
                  value={node.validationTemplateId ?? ''}
                  onChange={(e) => {
                    const val = e.target.value
                    updateNode(node.id, {
                      validationTemplateId: val || undefined,
                      validationMinScore: val ? (node.validationMinScore ?? 60) : undefined,
                    } as Partial<WorkflowNode>)
                  }}
                  className="input-field"
                >
                  <option value="">None (no validation)</option>
                  {validationTemplates?.map((vt) => (
                    <option key={vt.templateId} value={vt.templateId}>
                      {vt.name} ({vt.templateId})
                    </option>
                  ))}
                </select>
              </Field>
              {node.validationTemplateId && (
                <Field label="Min Score (0-100)">
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={node.validationMinScore ?? 60}
                    onChange={(e) => {
                      const val = e.target.value === '' ? undefined : Number(e.target.value)
                      updateNode(node.id, { validationMinScore: val } as Partial<WorkflowNode>)
                    }}
                    className="input-field"
                    placeholder="60"
                  />
                  <p className="mt-1 text-[10px] text-gray-400">
                    Score below this threshold will trigger alerting (if configured on Alerts tab).
                  </p>
                </Field>
              )}
            </div>
          </>
        )}

        {/* ── Advanced Tab ── */}
        {activeTab === 'advanced' && (
          <>
            {/* Retry */}
            <CollapsibleSection
              title="重试"
              badge={node.retry ? '已配置' : undefined}
              defaultOpen={!!node.retry}
            >
              {node.retry ? (
                <div className="space-y-2">
                  <Field label="Max Attempts">
                    <input
                      type="number"
                      min={1}
                      value={(node.retry as RetryConfig).maxAttempts ?? ''}
                      onChange={(e) => handleRetryChange('maxAttempts', e.target.value ? Number(e.target.value) : undefined)}
                      className="input-field"
                    />
                  </Field>
                  <Field label="Delay (ms)">
                    <input
                      type="number"
                      min={0}
                      value={(node.retry as RetryConfig).delayMs ?? ''}
                      onChange={(e) => handleRetryChange('delayMs', e.target.value ? Number(e.target.value) : undefined)}
                      className="input-field"
                    />
                  </Field>
                  <Field label="Backoff">
                    <select
                      value={(node.retry as RetryConfig).backoff ?? ''}
                      onChange={(e) => handleRetryChange('backoff', (e.target.value || undefined) as RetryConfig['backoff'])}
                      className="input-field"
                    >
                      <option value="">None</option>
                      <option value="fixed">Fixed</option>
                      <option value="exponential">Exponential</option>
                      <option value="linear">Linear</option>
                    </select>
                  </Field>
                  <button onClick={handleRetryRemove} className="text-xs text-red-500 hover:text-red-700">
                    移除重试配置
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => updateNode(node.id, { retry: { maxAttempts: 3, delayMs: 1000, backoff: 'exponential' } })}
                  className="text-xs text-blue-600 hover:text-blue-800"
                >
                  + 添加重试配置
                </button>
              )}
            </CollapsibleSection>

            {/* Knowledge Items (when knowledge is an array) */}
            <CollapsibleSection
              title="知识条目"
              badge={Array.isArray(node.knowledge) ? `${node.knowledge.length}` : undefined}
              defaultOpen={Array.isArray(node.knowledge) && node.knowledge.length > 0}
            >
              {Array.isArray(node.knowledge) && node.knowledge.length > 0 ? (
                <div className="space-y-2">
                  {node.knowledge.map((item, i) => (
                    <div key={i} className="rounded border border-gray-200 bg-gray-50 p-2">
                      <div className="mb-1 flex items-center justify-between">
                        <select
                          value={item.type}
                          onChange={(e) => handleKnowledgeChange(i, 'type', e.target.value)}
                          className="rounded border border-gray-300 px-1.5 py-0.5 text-xs"
                        >
                          <option value="text">text</option>
                          <option value="file">file</option>
                          <option value="url">url</option>
                        </select>
                        <button onClick={() => handleRemoveKnowledge(i)} className="text-gray-400 hover:text-red-500">
                          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                      <textarea
                        value={item.content}
                        onChange={(e) => handleKnowledgeChange(i, 'content', e.target.value)}
                        rows={3}
                        placeholder="Knowledge content..."
                        className="input-field font-mono text-xs"
                      />
                    </div>
                  ))}
                  <button onClick={handleAddKnowledge} className="text-xs text-blue-600 hover:text-blue-800">
                    + Add knowledge item
                  </button>
                </div>
              ) : (
                <button onClick={handleAddKnowledge} className="text-xs text-blue-600 hover:text-blue-800">
                  + Add knowledge items (overrides boolean knowledge)
                </button>
              )}
            </CollapsibleSection>

            {/* Output Contract */}
            <CollapsibleSection
              title="输出契约"
              badge={node.outputContract ? '已配置' : undefined}
              defaultOpen={!!node.outputContract}
            >
              {node.outputContract ? (
                <div className="space-y-2">
                  <label className="flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      checked={!!(node.outputContract as Record<string, unknown>).required}
                      onChange={(e) =>
                        updateNode(node.id, {
                          outputContract: { ...node.outputContract, required: e.target.checked },
                        })
                      }
                      className="rounded border-gray-300"
                    />
                    <span className="text-xs text-gray-600">Required</span>
                  </label>
                  <textarea
                    value={JSON.stringify(
                      Object.fromEntries(Object.entries(node.outputContract as Record<string, unknown>).filter(([key]) => key !== 'required')),
                      null,
                      2,
                    )}
                    onChange={(e) => handleOutputContractChange(e.target.value)}
                    rows={6}
                    className="input-field font-mono text-xs"
                  />
                  <button
                    onClick={() => updateNode(node.id, { outputContract: undefined })}
                    className="text-xs text-red-500 hover:text-red-700"
                  >
                    移除输出契约
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => updateNode(node.id, { outputContract: { type: 'object', properties: {} } })}
                  className="text-xs text-blue-600 hover:text-blue-800"
                >
                  + 添加输出契约
                </button>
              )}
            </CollapsibleSection>

            {/* Mock Output */}
            <CollapsibleSection
              title="模拟输出"
              badge={node.mock ? '已配置' : undefined}
              defaultOpen={!!node.mock}
            >
              {node.mock ? (
                <div className="space-y-2">
                  <textarea
                    value={JSON.stringify(node.mock, null, 2)}
                    onChange={(e) => handleMockChange(e.target.value)}
                    rows={6}
                    placeholder='{"output": {}}'
                    className="input-field font-mono text-xs"
                  />
                  <button
                    onClick={() => updateNode(node.id, { mock: undefined })}
                    className="text-xs text-red-500 hover:text-red-700"
                  >
                    移除模拟输出
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => updateNode(node.id, { mock: { output: {} } })}
                  className="text-xs text-blue-600 hover:text-blue-800"
                >
                  + 添加模拟输出
                </button>
              )}
            </CollapsibleSection>

            {/* OnFeedback (collaboration/approval nodes) */}
            {(executorType === 'collaboration' || executorType === 'approval') && (
              <CollapsibleSection
                title="反馈处理"
                badge={node.onFeedback ? '已配置' : undefined}
                defaultOpen={!!node.onFeedback}
              >
                {node.onFeedback ? (
                  <div className="space-y-2">
                    <Field label="Target Node">
                      <input
                        type="text"
                        value={node.onFeedback.target ?? ''}
                        onChange={(e) => handleOnFeedbackChange('target', e.target.value)}
                        className="input-field"
                        placeholder="Node ID to rerun on feedback"
                      />
                    </Field>
                    <Field label="Feedback Path">
                      <input
                        type="text"
                        value={node.onFeedback.feedbackPath ?? ''}
                        onChange={(e) => handleOnFeedbackChange('feedbackPath', e.target.value)}
                        className="input-field"
                        placeholder="e.g. feedback.instructions"
                      />
                    </Field>
                    <Field label="Feedback Mode">
                      <select
                        value={node.onFeedback.feedbackMode ?? ''}
                        onChange={(e) => handleOnFeedbackChange('feedbackMode', e.target.value)}
                        className="input-field"
                      >
                        <option value="">—</option>
                        <option value="replace">replace</option>
                        <option value="append-line">append-line</option>
                      </select>
                    </Field>
                    <Field label="Reset">
                      <select
                        value={node.onFeedback.reset ?? ''}
                        onChange={(e) => handleOnFeedbackChange('reset', e.target.value)}
                        className="input-field"
                      >
                        <option value="">—</option>
                        <option value="target-and-descendants">target-and-descendants</option>
                      </select>
                    </Field>
                    <button onClick={handleOnFeedbackRemove} className="text-xs text-red-500 hover:text-red-700">
                      Remove onFeedback
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => updateNode(node.id, { onFeedback: { target: '' } })}
                    className="text-xs text-blue-600 hover:text-blue-800"
                  >
                    + Add onFeedback
                  </button>
                )}
              </CollapsibleSection>
            )}

            {/* Join / Trigger Rule */}
            <CollapsibleSection title="汇合规则" defaultOpen={false}>
              <div className="space-y-2">
                <Field label="Join Mode">
                  <select
                    value={node.join ?? ''}
                    onChange={(e) => updateNode(node.id, { join: (e.target.value || undefined) as 'all' | 'any' | undefined })}
                    className="input-field"
                  >
                    <option value="">Default (all)</option>
                    <option value="all">All predecessors</option>
                    <option value="any">Any predecessor</option>
                  </select>
                </Field>
                <Field label="Trigger Rule">
                  <select
                    value={node.triggerRule ?? ''}
                    onChange={(e) => updateNode(node.id, { triggerRule: (e.target.value || undefined) as 'all_success' | 'one_success' | 'all_done' | undefined })}
                    className="input-field"
                  >
                    <option value="">Default (all_success)</option>
                    <option value="all_success">all_success</option>
                    <option value="one_success">one_success</option>
                    <option value="all_done">all_done</option>
                  </select>
                </Field>
                <p className="text-[10px] text-gray-400">
                  <strong>all_success</strong>: all deps must succeed. <strong>one_success</strong>: any dep succeeds. <strong>all_done</strong>: all deps finished (regardless of status).
                </p>
              </div>
            </CollapsibleSection>

            {/* Progress Message */}
            <CollapsibleSection title="进度消息" defaultOpen={false}>
              <Field label="Message Template">
                <textarea
                  value={node.progressMessage ?? ''}
                  onChange={(e) => updateNode(node.id, { progressMessage: e.target.value || undefined })}
                  className="input-field text-xs"
                  rows={2}
                  placeholder="Progress update shown while node runs…"
                />
              </Field>
            </CollapsibleSection>

            {/* OnResult (conditional branching) */}
            <CollapsibleSection
              title="结果分支"
              badge={Array.isArray(node.onResult) && node.onResult.length > 0 ? `${node.onResult.length}` : undefined}
              defaultOpen={Array.isArray(node.onResult) && node.onResult.length > 0}
            >
              {Array.isArray(node.onResult) && node.onResult.length > 0 ? (
                <div className="space-y-2">
                  {node.onResult.map((branch, i) => (
                    <div key={i} className="flex items-start gap-1.5 rounded border border-gray-100 bg-gray-50 p-2">
                      <div className="flex-1 space-y-1">
                        <input
                          type="text"
                          value={branch.value}
                          onChange={(e) => handleOnResultChange(i, 'value', e.target.value)}
                          className="input-field text-xs"
                          placeholder="Value (e.g. proceed, retry)"
                        />
                        <input
                          type="text"
                          value={branch.target}
                          onChange={(e) => handleOnResultChange(i, 'target', e.target.value)}
                          className="input-field text-xs"
                          placeholder="Target node ID"
                        />
                      </div>
                      <button
                        onClick={() => handleRemoveOnResult(i)}
                        className="mt-0.5 shrink-0 text-red-400 hover:text-red-600"
                        title="Remove branch"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                  <button
                    onClick={handleAddOnResult}
                    className="text-xs text-blue-600 hover:text-blue-800"
                  >
                    + Add branch
                  </button>
                  <p className="text-[10px] text-gray-400">
                    When the node result matches <code>value</code>, route to the <code>target</code> node. Used with <code>done</code> nodes for conditional branching.
                  </p>
                </div>
              ) : (
                <button
                  onClick={handleAddOnResult}
                  className="text-xs text-blue-600 hover:text-blue-800"
                >
                  + Add onResult branch
                </button>
              )}
            </CollapsibleSection>

            {/* Business Status */}
            <CollapsibleSection title="业务状态" defaultOpen={!!node.businessStatus}>
              <Field label="Status Value">
                <select
                  value={node.businessStatus ?? ''}
                  onChange={(e) => updateNode(node.id, { businessStatus: e.target.value || undefined })}
                  className="input-field"
                >
                  <option value="">None</option>
                  <option value="pending">Pending</option>
                  <option value="in_progress">In Progress</option>
                  <option value="completed">Completed</option>
                  <option value="failed">Failed</option>
                  <option value="cancelled">Cancelled</option>
                </select>
              </Field>
              <p className="text-[10px] text-gray-400">
                Business status is a user-facing label for tracking workflow progress.
              </p>
            </CollapsibleSection>
          </>
        )}

        {/* ── Actions Tab ── */}
        {activeTab === 'actions' && (
          <div className="space-y-3">
            {/* onSuccess */}
            <div>
              <div className="mb-1 flex items-center justify-between">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                  On Success ({node.onSuccess?.length ?? 0})
                </h4>
                <button
                  onClick={() => handleAddPostAction('onSuccess')}
                  className="text-xs text-blue-600 hover:text-blue-800"
                >
                  + Add
                </button>
              </div>
              {Array.isArray(node.onSuccess) && (node.onSuccess as PostAction[]).map((action, i) => (
                <PostActionCard
                  key={`success-${i}`}
                  action={action}
                  index={i}
                  onChange={(field, value) => handlePostActionChange('onSuccess', i, field, value)}
                  onRemove={() => handleRemovePostAction('onSuccess', i)}
                />
              ))}
            </div>

            {/* onFailure */}
            <div>
              <div className="mb-1 flex items-center justify-between">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                  On Failure ({node.onFailure?.length ?? 0})
                </h4>
                <button
                  onClick={() => handleAddPostAction('onFailure')}
                  className="text-xs text-blue-600 hover:text-blue-800"
                >
                  + Add
                </button>
              </div>
              {Array.isArray(node.onFailure) && (node.onFailure as PostAction[]).map((action, i) => (
                <PostActionCard
                  key={`failure-${i}`}
                  action={action}
                  index={i}
                  onChange={(field, value) => handlePostActionChange('onFailure', i, field, value)}
                  onRemove={() => handleRemovePostAction('onFailure', i)}
                />
              ))}
            </div>

            {/* Quick-add DingTalk template */}
            <div className="border-t border-gray-200 pt-2">
              <p className="mb-1 text-[10px] text-gray-400 uppercase tracking-wider">Quick Templates</p>
              <button
                onClick={() => {
                  const actions: PostAction[] = Array.isArray(node.onSuccess) ? [...(node.onSuccess as PostAction[])] : []
                  actions.push({
                    id: 'notify-dingtalk',
                    action: 'send-notification',
                    required: false,
                    args: {
                      channel: 'dingtalk',
                      webhook: '{{env.DINGTALK_WEBHOOK_URL}}',
                      message: `✅ Node {{node.id}} succeeded in workflow {{workflow.id}}`,
                      keywords: ['clawflow-alert'],
                    },
                  })
                  updateNode(node.id, { onSuccess: actions })
                }}
                className="mr-2 rounded border border-gray-200 bg-gray-50 px-2 py-1 text-[10px] text-gray-600 hover:bg-gray-100"
              >
                + DingTalk on Success
              </button>
              <button
                onClick={() => {
                  const actions: PostAction[] = Array.isArray(node.onFailure) ? [...(node.onFailure as PostAction[])] : []
                  actions.push({
                    id: 'alert-dingtalk',
                    action: 'send-notification',
                    required: true,
                    args: {
                      channel: 'dingtalk',
                      webhook: '{{env.DINGTALK_WEBHOOK_URL}}',
                      message: `❌ Node {{node.id}} FAILED in workflow {{workflow.id}}`,
                      severity: 'critical',
                      keywords: ['clawflow-alert', 'node-failed'],
                    },
                  })
                  updateNode(node.id, { onFailure: actions })
                }}
                className="rounded border border-red-200 bg-red-50 px-2 py-1 text-[10px] text-red-600 hover:bg-red-100"
              >
                + DingTalk on Failure
              </button>
            </div>
          </div>
        )}

        {/* ── Alerts Tab ── */}
        {activeTab === 'alerts' && (
          <div className="space-y-3">
            <p className="text-[10px] text-gray-400">
              Per-node alerting overrides. These override the global alerting configuration for this node.
            </p>

            {node.alerting ? (
              <>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={node.alerting.dingtalk !== false}
                    onChange={(e) => handleAlertingChange('dingtalk', e.target.checked)}
                    className="rounded border-gray-300"
                  />
                  <span className="text-xs font-medium text-gray-700">DingTalk Notifications</span>
                </label>

                <Field label="Alert Severity">
                  <select
                    value={node.alerting.severity ?? ''}
                    onChange={(e) => handleAlertingChange('severity', e.target.value || undefined)}
                    className="input-field"
                  >
                    <option value="">Default (warning)</option>
                    <option value="info">Info</option>
                    <option value="warning">Warning</option>
                    <option value="critical">Critical</option>
                  </select>
                </Field>

                <Field label="Extra Keywords">
                  <div className="space-y-1">
                    {(node.alerting.keywords ?? []).map((kw, i) => (
                      <span key={i} className="mr-1 inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
                        {kw}
                        <button
                          onClick={() => {
                            const alerting = node.alerting
                            const kws = (alerting?.keywords ?? []).filter((_, idx) => idx !== i)
                            handleAlertingChange('keywords', kws.length > 0 ? kws : undefined)
                          }}
                          className="text-amber-400 hover:text-red-500"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                    <KeywordInput
                      onAdd={(kw) => {
                        const alerting = node.alerting
                        const kws = [...(alerting?.keywords ?? []), kw]
                        handleAlertingChange('keywords', kws)
                      }}
                    />
                  </div>
                </Field>

                <button onClick={handleAlertingRemove} className="text-xs text-red-500 hover:text-red-700">
                  Remove alerting overrides
                </button>
              </>
            ) : (
              <button
                onClick={() => {
                  updateNode(node.id, {
                    alerting: { dingtalk: true, severity: 'warning' },
                  })
                }}
                className="text-xs text-blue-600 hover:text-blue-800"
              >
                + Add alerting overrides
              </button>
            )}

            <div className="border-t border-gray-200 pt-3">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">
                Alert Rule Reference
              </h4>
              <div className="space-y-2 rounded bg-gray-50 p-2 text-[10px] text-gray-500">
                <div className="flex items-start gap-2">
                  <span className="shrink-0 rounded bg-red-100 px-1 py-0.5 text-[9px] font-medium text-red-600">RUNTIME</span>
                  <div>
                    <p className="font-medium text-gray-700">node_failure_exhausted</p>
                    <p>Triggered when a node fails after all retry attempts</p>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <span className="shrink-0 rounded bg-orange-100 px-1 py-0.5 text-[9px] font-medium text-orange-600">RUNTIME</span>
                  <div>
                    <p className="font-medium text-gray-700">output_contract_failed</p>
                    <p>Triggered when output contract validation fails</p>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <span className="shrink-0 rounded bg-orange-100 px-1 py-0.5 text-[9px] font-medium text-orange-600">RUNTIME</span>
                  <div>
                    <p className="font-medium text-gray-700">required_hook_failed</p>
                    <p>Triggered when a required hook action fails</p>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <span className="shrink-0 rounded bg-blue-100 px-1 py-0.5 text-[9px] font-medium text-blue-600">ANALYSIS</span>
                  <div>
                    <p className="font-medium text-gray-700">threshold_healthScore</p>
                    <p>Health score dropped below threshold (default &lt; 0.6)</p>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <span className="shrink-0 rounded bg-blue-100 px-1 py-0.5 text-[9px] font-medium text-blue-600">ANALYSIS</span>
                  <div>
                    <p className="font-medium text-gray-700">threshold_toolFailureRate</p>
                    <p>Tool failure rate exceeded threshold (default &gt; 0.3)</p>
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <span className="shrink-0 rounded bg-blue-100 px-1 py-0.5 text-[9px] font-medium text-blue-600">ANALYSIS</span>
                  <div>
                    <p className="font-medium text-gray-700">threshold_incompleteRate</p>
                    <p>Incomplete (failed + retried) rate exceeded threshold (default &gt; 0.3)</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 删除节点 — visible on all tabs */}
        <div className="border-t border-gray-200 pt-3">
          <button
            onClick={() => removeNode(node.id)}
            className="w-full rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-600 transition-colors hover:bg-red-50"
          >
            删除节点
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Helper: check if node has advanced config ──
function hasAdvancedConfig(node: WorkflowNode): boolean {
  return !!(
    node.retry ||
    Array.isArray(node.knowledge) ||
    node.knowledgeBaseId ||
    node.outputContract ||
    node.mock ||
    node.onFeedback ||
    node.join ||
    node.triggerRule ||
    node.progressMessage ||
    (Array.isArray(node.onResult) && node.onResult.length > 0) ||
    node.businessStatus ||
    node.validationTemplateId
  )
}

function hasPostActions(node: WorkflowNode): boolean {
  return (Array.isArray(node.onSuccess) && node.onSuccess.length > 0) ||
    (Array.isArray(node.onFailure) && node.onFailure.length > 0)
}

// ── Sub-components ──

function Field({ label, children, description }: { label: string; children: React.ReactNode; description?: string }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-gray-500">
        {label}
        {description && <span className="ml-1 text-[10px] text-gray-400">({description})</span>}
      </label>
      {children}
      <style>{`.input-field { width: 100%; border-radius: 0.375rem; border: 1px solid #d1d5db; padding: 0.25rem 0.5rem; font-size: 0.75rem; }
.input-field:focus { border-color: #3b82f6; outline: none; box-shadow: 0 0 0 1px #3b82f6; }`}</style>
    </div>
  )
}

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
          <svg
            className={`h-3 w-3 transition-transform ${open ? 'rotate-90' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          {title}
        </span>
        {badge && (
          <span className="rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">
            {badge}
          </span>
        )}
      </button>
      {open && <div className="border-t border-gray-200 px-3 py-2">{children}</div>}
    </div>
  )
}

function PostActionCard({
  action,
  index,
  onChange,
  onRemove,
}: {
  action: PostAction
  index: number
  onChange: (field: keyof PostAction, value: string | boolean | Record<string, unknown>) => void
  onRemove: () => void
}) {
  return (
    <div className="mb-2 rounded border border-gray-200 bg-gray-50 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase text-gray-400">Action {index + 1}</span>
        <button onClick={onRemove} className="text-gray-400 hover:text-red-500">
          <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      <Field label="ID">
        <input
          type="text"
          value={action.id ?? ''}
          onChange={(e) => onChange('id', e.target.value)}
          className="input-field"
        />
      </Field>
      <Field label="动作">
        <input
          type="text"
          value={action.action ?? ''}
          onChange={(e) => onChange('action', e.target.value)}
          className="input-field"
          placeholder="e.g. send-notification, save-result"
        />
      </Field>
      <label className="flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={action.required ?? true}
          onChange={(e) => onChange('required', e.target.checked)}
          className="rounded border-gray-300"
        />
        <span className="text-xs text-gray-600">Required</span>
      </label>
      <Field label="Args (JSON)">
        <textarea
          value={action.args ? JSON.stringify(action.args, null, 2) : ''}
          onChange={(e) => {
            try {
              const parsed = JSON.parse(e.target.value)
              onChange('args', parsed)
            } catch { /* keep as-is */ }
          }}
          rows={4}
          className="input-field font-mono text-xs"
        />
      </Field>
      <Field label="Save As (JSON)">
        <textarea
          value={action.saveAs ? JSON.stringify(action.saveAs, null, 2) : ''}
          onChange={(e) => {
            try {
              const parsed = JSON.parse(e.target.value)
              onChange('saveAs', parsed)
            } catch { /* keep as-is */ }
          }}
          rows={2}
          placeholder='{"key": "variableName"}'
          className="input-field font-mono text-xs"
        />
      </Field>
    </div>
  )
}

function DependsOnPicker({
  currentNodeId,
  allNodes,
  currentDeps,
  onAdd,
}: {
  currentNodeId: string
  allNodes: WorkflowNode[]
  currentDeps: string[]
  onAdd: (dep: string) => void
}) {
  const available = allNodes
    .filter((n) => n.id !== currentNodeId && !currentDeps.includes(n.id))
  if (available.length === 0) return null
  return (
    <div className="mt-1">
      <select
        value=""
        onChange={(e) => {
          if (e.target.value) onAdd(e.target.value)
        }}
        className="input-field text-xs"
      >
        <option value="">+ Add dependency…</option>
        {available.map((n) => (
          <option key={n.id} value={n.id}>
            {n.title || n.id}
          </option>
        ))}
      </select>
    </div>
  )
}

function BranchIdField({
  value,
  onChange,
}: {
  value: string | undefined
  onChange: (val: string | undefined) => void
}) {
  return (
    <Field label="Branch ID" description="Matches onResult branch to route this node">
      <input
        type="text"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="input-field"
        placeholder="e.g. matched, unmatched"
      />
    </Field>
  )
}

function KeywordInput({ onAdd }: { onAdd: (kw: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <div className="flex gap-1">
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && value.trim()) {
            onAdd(value.trim())
            setValue('')
          }
        }}
        placeholder="Add keyword…"
        className="input-field flex-1 text-xs"
      />
      <button
        onClick={() => {
          if (value.trim()) {
            onAdd(value.trim())
            setValue('')
          }
        }}
        className="rounded bg-blue-50 px-2 py-1 text-xs text-blue-600 hover:bg-blue-100"
      >
        Add
      </button>
    </div>
  )
}

function HumanGateActionsEditor({
  actions,
  onChange,
}: {
  actions: Record<string, unknown> | undefined
  onChange: (actions: Record<string, unknown>) => void
}) {
  const confirm = actions?.confirm as Record<string, unknown> | undefined
  const revise = actions?.revise as Record<string, unknown> | undefined
  const reject = actions?.reject as Record<string, unknown> | undefined

  return (
    <div className="space-y-2">
      <label className="flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={!!confirm}
          onChange={(e) => {
            const updated = { ...actions }
            if (e.target.checked) {
              updated.confirm = { next: 'succeed-current' }
            } else {
              delete updated.confirm
            }
            onChange(updated)
          }}
          className="rounded border-gray-300"
        />
        <span className="text-xs font-medium text-green-700">Confirm Action</span>
      </label>
      {confirm && (
        <div className="ml-4 space-y-1 rounded border border-green-100 bg-green-50 p-2">
          <Field label="Next">
            <select
              value={(confirm.next as string) ?? ''}
              onChange={(e) => {
                onChange({ ...actions, confirm: { ...confirm, next: e.target.value } })
              }}
              className="input-field"
            >
              <option value="succeed-current">succeed-current</option>
            </select>
          </Field>
        </div>
      )}

      <label className="flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={!!revise}
          onChange={(e) => {
            const updated = { ...actions }
            if (e.target.checked) {
              updated.revise = { target: '', feedbackPath: '', next: 'rerun-target' }
            } else {
              delete updated.revise
            }
            onChange(updated)
          }}
          className="rounded border-gray-300"
        />
        <span className="text-xs font-medium text-amber-700">Revise Action</span>
      </label>
      {revise && (
        <div className="ml-4 space-y-1 rounded border border-amber-100 bg-amber-50 p-2">
          <Field label="Target Node">
            <input
              type="text"
              value={(revise.target as string) ?? ''}
              onChange={(e) => onChange({ ...actions, revise: { ...revise, target: e.target.value } })}
              className="input-field"
              placeholder="Node ID to rerun"
            />
          </Field>
          <Field label="Feedback Path">
            <input
              type="text"
              value={(revise.feedbackPath as string) ?? ''}
              onChange={(e) => onChange({ ...actions, revise: { ...revise, feedbackPath: e.target.value } })}
              className="input-field"
            />
          </Field>
          <Field label="Feedback Mode">
            <select
              value={(revise.feedbackMode as string) ?? ''}
              onChange={(e) => onChange({ ...actions, revise: { ...revise, feedbackMode: e.target.value || undefined } })}
              className="input-field"
            >
              <option value="">Default (replace)</option>
              <option value="replace">replace</option>
              <option value="append-line">append-line</option>
            </select>
          </Field>
          <Field label="Reset">
            <select
              value={(revise.reset as string) ?? ''}
              onChange={(e) => onChange({ ...actions, revise: { ...revise, reset: e.target.value || undefined } })}
              className="input-field"
            >
              <option value="">None</option>
              <option value="target-and-descendants">target-and-descendants</option>
            </select>
          </Field>
        </div>
      )}

      <label className="flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={!!reject}
          onChange={(e) => {
            const updated = { ...actions }
            if (e.target.checked) {
              updated.reject = { next: 'fail-flow' }
            } else {
              delete updated.reject
            }
            onChange(updated)
          }}
          className="rounded border-gray-300"
        />
        <span className="text-xs font-medium text-red-700">Reject Action</span>
      </label>
      {reject && (
        <div className="ml-4 space-y-1 rounded border border-red-100 bg-red-50 p-2">
          <Field label="Next">
            <select
              value={(reject.next as string) ?? ''}
              onChange={(e) => onChange({ ...actions, reject: { ...reject, next: e.target.value } })}
              className="input-field"
            >
              <option value="fail-flow">fail-flow</option>
              <option value="block-flow">block-flow</option>
            </select>
          </Field>
        </div>
      )}
    </div>
  )
}

function CollaborationRouteEditor({
  executor,
  onChange,
}: {
  executor: Record<string, unknown>
  onChange: (updated: Record<string, unknown>) => void
}) {
  const route = executor.route as Record<string, unknown> | undefined
  return (
    <div className="space-y-2">
      <label className="flex items-center gap-1.5 text-xs text-gray-600">
        <input
          type="checkbox"
          checked={!!route}
          onChange={(e) => {
            if (e.target.checked) {
              onChange({ ...executor, route: { provider: 'bcs', mode: 'auto' } })
            } else {
              const updated = { ...executor }
              delete updated.route
              onChange(updated)
            }
          }}
          className="rounded border-gray-300"
        />
        <span className="font-medium">Custom Route</span>
      </label>
      {route && (
        <div className="space-y-1 rounded border border-blue-100 bg-blue-50 p-2">
          <Field label="Provider">
            <select
              value={(route.provider as string) ?? 'bcs'}
              onChange={(e) => onChange({ ...executor, route: { ...route, provider: e.target.value } })}
              className="input-field"
            >
              <option value="bcs">bcs</option>
            </select>
          </Field>
          <Field label="Mode">
            <select
              value={(route.mode as string) ?? ''}
              onChange={(e) => onChange({ ...executor, route: { ...route, mode: e.target.value || undefined } })}
              className="input-field"
            >
              <option value="">Default</option>
              <option value="auto">auto</option>
              <option value="tool">tool</option>
              <option value="cli">cli</option>
            </select>
          </Field>
          <Field label="Reason">
            <input
              type="text"
              value={(route.reason as string) ?? ''}
              onChange={(e) => onChange({ ...executor, route: { ...route, reason: e.target.value || undefined } })}
              className="input-field"
              placeholder="Routing reason"
            />
          </Field>
        </div>
      )}
    </div>
  )
}

function LoopUntilEditor({
  executor,
  onChange,
}: {
  executor: Record<string, unknown>
  onChange: (updated: Record<string, unknown>) => void
}) {
  const until = executor.until as Record<string, unknown> | undefined
  const onMax = executor.onMaxIterations as Record<string, unknown> | undefined
  return (
    <div className="space-y-2">
      <label className="flex items-center gap-1.5 text-xs text-gray-600">
        <input
          type="checkbox"
          checked={!!until}
          onChange={(e) => {
            if (e.target.checked) {
              onChange({ ...executor, until: { node: '', path: '', equals: true } })
            } else {
              const updated = { ...executor }
              delete updated.until
              onChange(updated)
            }
          }}
          className="rounded border-gray-300"
        />
        <span className="font-medium">终止条件</span>
      </label>
      {until && (
        <div className="space-y-1 rounded border border-blue-100 bg-blue-50 p-2">
          <Field label="Node">
            <input
              type="text"
              value={(until.node as string) ?? ''}
              onChange={(e) => onChange({ ...executor, until: { ...until, node: e.target.value } })}
              className="input-field"
              placeholder="Node ID within loop body"
            />
          </Field>
          <Field label="Path">
            <input
              type="text"
              value={(until.path as string) ?? ''}
              onChange={(e) => onChange({ ...executor, until: { ...until, path: e.target.value } })}
              className="input-field"
              placeholder="e.g. result.complete"
            />
          </Field>
          <Field label="Equals">
            <input
              type="text"
              value={String(until.equals ?? '')}
              onChange={(e) => {
                let val: unknown = e.target.value
                if (val === 'true') val = true
                else if (val === 'false') val = false
                else if (val !== '' && !isNaN(Number(val))) val = Number(val)
                onChange({ ...executor, until: { ...until, equals: val } })
              }}
              className="input-field"
              placeholder="true / false / value"
            />
          </Field>
        </div>
      )}

      <label className="flex items-center gap-1.5 text-xs text-gray-600">
        <input
          type="checkbox"
          checked={!!onMax}
          onChange={(e) => {
            if (e.target.checked) {
              onChange({ ...executor, onMaxIterations: { action: 'continue' } })
            } else {
              const updated = { ...executor }
              delete updated.onMaxIterations
              onChange(updated)
            }
          }}
          className="rounded border-gray-300"
        />
        <span className="font-medium">On Max Iterations</span>
      </label>
      {onMax && (
        <div className="space-y-1 rounded border border-amber-100 bg-amber-50 p-2">
          <Field label="动作">
            <select
              value={(onMax.action as string) ?? 'continue'}
              onChange={(e) => onChange({ ...executor, onMaxIterations: { ...onMax, action: e.target.value } })}
              className="input-field"
            >
              <option value="continue">Continue</option>
              <option value="fail">Fail</option>
            </select>
          </Field>
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={!!onMax.saveLastIteration}
              onChange={(e) => onChange({ ...executor, onMaxIterations: { ...onMax, saveLastIteration: e.target.checked } })}
              className="rounded border-gray-300"
            />
            <span className="text-xs text-gray-600">Save Last Iteration</span>
          </label>
        </div>
      )}
    </div>
  )
}
