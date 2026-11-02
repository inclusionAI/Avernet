import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from '../editor/store'
import { getEffectiveTriggerRule } from '../editor/contracts'
import { getNodeMeta } from '../editor/nodeCatalog'
import NodeFields, { TextField, SelectField, configSummary } from './editor/NodeFields'
import JsonDraftField, { isRecord } from './editor/JsonDraftField'
import ResultBranchesEditor, { validateBranchChange } from './editor/ResultBranchesEditor'

const buttonClass = 'rounded-md border border-input bg-background px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50'
const triggerOptions: [string, string][] = [
  ['all_success', '所有上游成功后'], ['one_success', '任一上游成功后'], ['all_done', '所有上游结束后（含失败或跳过）'],
]

export default function NodePropertyPanel() {
  const node = useEditorStore(state => state.spec?.nodes.find(item => item.id === state.selectedNodeId))
  return <aside aria-label="节点配置" className="flex h-full w-96 max-w-full shrink-0 flex-col border-l border-input bg-card text-foreground">
    {node ? <NodePanel key={node.id} node={node} /> :
      <div className="space-y-3 p-5"><h2 className="text-sm font-semibold">开始编排</h2><p className="text-sm text-muted-foreground">选择画布上的节点，编辑这一步的业务配置。</p></div>}
  </aside>
}

function NodePanel({ node }: { node: WorkflowNode }) {
  const { pendingEdits, actionError, selectNode, duplicateNode, removeNode, spec } = useEditorStore()
  const pending = Object.keys(pendingEdits).length > 0
  const executor = isRecord(node.executor) ? node.executor : null
  const meta = getNodeMeta(typeof executor?.type === 'string' ? executor.type : '未知类型')
  const effectiveTrigger = getEffectiveTriggerRule(node)
  const prominentTrigger = (node.dependsOn?.length ?? 0) > 1 || effectiveTrigger !== 'all_success'

  function patch(updates: Partial<WorkflowNode>) {
    const store = useEditorStore.getState()
    const current = store.spec?.nodes.find(item => item.id === node.id)
    if (!current) throw new Error('节点已不存在，未应用草稿。')
    const error = validateBranchChange(current, { ...current, ...updates }, store.spec!.nodes)
    if (error) { store.setActionError(error); throw new Error(error) }
    store.setActionError(null)
    store.updateNode(node.id, updates)
    const failure = useEditorStore.getState().actionError
    if (failure) throw new Error(failure)
  }

  function rawError(value: unknown): string | null {
    if (!isRecord(value)) return '完整节点配置必须是 JSON 对象。'
    if (value.id !== node.id) return '节点 id 只读，不能修改或删除。'
    if (!isRecord(value.executor) || typeof value.executor.type !== 'string') return 'executor 必须是含 type 字符串的对象。'
    if ((executor && value.executor.type !== executor.type) || value.type !== node.type) return '节点 type 只读，不能修改或删除。'
    if (value._x !== node._x || value._y !== node._y) return '画布坐标请通过画布调整；原始坐标需保留。'
    return validateBranchChange(node, value as WorkflowNode, spec?.nodes ?? [])
  }

  function applyRaw(value: unknown) {
    const error = rawError(value)
    if (error) throw new Error(error)
    const next = value as WorkflowNode
    // updateNode is shallow. Explicit omissions must be patched as undefined,
    // not silently retained; JsonDraftField has already confirmed every deletion.
    const updates: Partial<WorkflowNode> = { ...next }
    for (const key of Object.keys(node)) {
      if (!Object.hasOwn(next, key)) updates[key] = undefined
    }
    // Imported legacy positions are preserved but never fed back to moveNode.
    delete updates._x
    delete updates._y
    patch(updates)
  }

  const trigger = <div className="space-y-2">
    <SelectField label="触发规则" value={node.triggerRule} options={triggerOptions} onChange={value => patch({ triggerRule: value as WorkflowNode['triggerRule'] })} />
    <p className="text-xs text-muted-foreground">有效规则：{effectiveTrigger}{node.triggerRule === undefined && node.join === 'any' ? '（兼容 join:any）' : ''}。未选择时不写入配置。</p>
  </div>
  const advancedEntries = Object.entries(node).filter(([key, value]) => value !== undefined && !['id', 'title', 'executor', 'dependsOn', '_x', '_y'].includes(key))
  const executorEntries = Object.entries(executor ?? {}).filter(([key]) => !['type', 'prompt', 'skillName', 'command', 'args', 'message', 'server', 'tool', 'outputMode'].includes(key))
  const existingTimeouts = ['timeoutMs', 'timeoutSeconds', 'timeout'].filter(key => executor && Object.hasOwn(executor, key))
  const timeoutKeys = existingTimeouts.length ? existingTimeouts :
    ['cli-script', 'mcp-call', 'baas-call'].includes(meta.type) ? ['timeoutMs'] :
      ['embedded-agent', 'subagent', 'approval', 'collaboration'].includes(meta.type) ? ['timeoutSeconds'] : []
  const advancedJson = (key: string, label: string) => <JsonDraftField label={label} value={node[key]} draftKey={`node:${node.id}:${key}`}
    onApply={value => patch({ [key]: value })} allowRemove={Object.hasOwn(node, key)} onRemove={() => patch({ [key]: undefined })} />

  return <>
    <header className="flex items-start gap-3 border-b border-input p-4">
      <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">{meta.icon}</span>
      <div className="min-w-0 flex-1"><h2 className="truncate text-sm font-semibold">{meta.label}</h2><p className="mt-1 truncate font-mono text-xs text-muted-foreground">{meta.type}</p></div>
      <button type="button" aria-label="关闭节点面板" disabled={pending} className={buttonClass} onClick={() => selectNode(null)}>关闭</button>
    </header>
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
      {actionError && <div role="alert" className="break-words rounded-md bg-destructive/10 p-3 text-xs text-destructive">{actionError}</div>}
      {pending && <div role="status" className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
        有未应用草稿。请先应用或放弃，再切换、复制、删除或关闭节点。
        <ul className="mt-1 list-inside list-disc">{Object.entries(pendingEdits).map(([key, message]) => <li key={key}>{message}</li>)}</ul>
      </div>}
      <TextField label="节点名称" value={node.title} onChange={title => patch({ title })} />
      {executor ? <NodeFields node={node} onPatch={patch} /> : <p className="text-xs text-destructive">执行器配置无效，请使用完整节点 JSON 修复。</p>}
      {prominentTrigger && trigger}
      <ResultBranchesEditor node={node} onPatch={patch} />
      <details className="rounded-lg border border-input p-3">
        <summary className="cursor-pointer text-sm font-semibold">高级配置
          <span className="mt-1 block truncate text-xs font-normal text-muted-foreground">
            {[...advancedEntries, ...executorEntries].length
              ? `已配置：${[...advancedEntries, ...executorEntries].map(([key, value]) => `${key}=${configSummary(value)}`).join(' · ')}`
              : '按需配置，不补默认值'}
          </span>
        </summary>
        <p className="mt-2 text-xs text-muted-foreground">{advancedEntries.length + executorEntries.length} 项已有配置 · 保留原值与原单位</p>
        <div className="mt-4 space-y-5">
          <TextField label="节点 ID" value={node.id} readOnly description="稳定标识；修改名称不会改变引用。" />
          <TextField label="阶段" value={node.phase} onChange={phase => patch({ phase })} description="阶段不是执行顺序；只在修改后写入。" />
          {!prominentTrigger && trigger}
          {executor && timeoutKeys.map(key => <JsonDraftField key={key}
            label={key === 'timeoutMs' ? '执行超时（毫秒）' : key === 'timeoutSeconds' ? '执行超时（秒）' : '执行超时（原始单位）'}
            value={executor[key]} draftKey={`node:${node.id}:executor.${key}`} rows={2}
            description={`保留 ${key} 的原始单位，不换算或覆盖其他超时字段。`}
            validate={value => key !== 'timeout' && (typeof value !== 'number' || !Number.isFinite(value) || value < 0) ? '超时必须是非负有限数字；清除请使用删除字段。' : null}
            onApply={value => patch({ executor: { ...node.executor, [key]: value } })}
            allowRemove={Object.hasOwn(executor, key)} onRemove={() => { const next = { ...node.executor }; delete next[key]; patch({ executor: next }) }} />)}
          {advancedJson('retry', '失败重试')}
          {advancedJson('outputContract', '输出契约')}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold">已有高级字段摘要</h3>
            <p className="text-xs text-muted-foreground">含隐藏、扩展与兼容字段；其他配置在完整节点 JSON 中编辑。</p>
            <dl className="space-y-2 text-xs">{[...advancedEntries, ...executorEntries.map(([key, value]) => [`executor.${key}`, value] as [string, unknown])].map(([key, value]) =>
              <div key={key}><dt className="font-mono font-medium">{key}</dt><dd className="truncate text-muted-foreground" title={configSummary(value)}>{configSummary(value)}</dd></div>)}</dl>
          </div>
        </div>
      </details>
      <details className="rounded-lg border border-input p-3">
        <summary className="cursor-pointer text-sm font-semibold">完整节点 JSON</summary>
        <div className="mt-4"><JsonDraftField label="完整节点配置" value={node} draftKey={`node:${node.id}:raw`} rows={16}
          validate={rawError} onApply={applyRaw}
          description="含全部隐藏与未知字段。id/type 只读；合法 JSON 显式应用后更新完整对象，删除字段须确认。" /></div>
      </details>
    </div>
    <footer className="flex gap-2 border-t border-input p-4">
      <button type="button" className={`${buttonClass} flex-1`} disabled={pending} onClick={() => duplicateNode(node.id)}>复制节点</button>
      <button type="button" className={`${buttonClass} text-destructive`} disabled={pending} onClick={() => {
        if (window.confirm(`确认删除节点「${node.title || node.id}」？已有引用将由编辑器检查，无法安全删除时会拒绝。`)) removeNode(node.id)
      }}>删除节点</button>
    </footer>
  </>
}
