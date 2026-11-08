import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from '../../editor/store'
import JsonDraftField from './JsonDraftField'

export default function WorkflowSettingsPanel({ onClose }: { onClose: () => void }) {
  const { spec, updateSpecField, pendingEdits, setActionError } = useEditorStore()
  if (!spec) return null
  const { nodes: _nodes, ...settings } = spec
  const patch = (key: string, value: unknown) => {
    const store = useEditorStore.getState()
    store.setActionError(null)
    store.updateSpecField(key, value)
    const error = useEditorStore.getState().actionError
    if (error) throw new Error(error)
  }
  const close = () => {
    if (Object.keys(pendingEdits).length) { setActionError('请先应用或放弃未完成的配置草稿'); return }
    onClose()
  }
  return <aside className="flex w-80 shrink-0 flex-col overflow-hidden border-l border-border bg-card" aria-label="流程设置">
    <header className="flex items-center justify-between border-b border-border px-4 py-4">
      <h2 className="text-sm font-semibold text-foreground">流程设置</h2>
      <button type="button" onClick={close} className="rounded border border-input px-2 py-1 text-xs text-muted-foreground">返回节点配置</button>
    </header>
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
      <p className="text-xs leading-5 text-muted-foreground">工作流输入、身份和默认配置集中在这里。已有配置会保留，不影响节点的简洁编辑。</p>
      <label className="block text-xs text-foreground">流程名称
        <input className="mt-2 w-full rounded-md border border-input bg-background p-2" value={spec.title}
          onChange={event => updateSpecField('title', event.target.value)} />
      </label>
      <label className="block text-xs text-foreground">流程标识
        <input className="mt-2 w-full rounded-md border border-input bg-muted p-2 font-mono text-muted-foreground" readOnly value={spec.id} />
      </label>
      <p className="text-[11px] text-muted-foreground">版本：{spec.version ?? '未显式配置'}。保存与部署沿用现有版本管理流程。</p>
      <JsonDraftField label="工作流输入" value={spec.input} draftKey="workflow:input"
        description="保留原有参数与schema；未配置时不自动补值。" onApply={value => patch('input', value)} />
      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer text-xs font-medium text-foreground">身份与输出 <span className="ml-2 font-normal text-muted-foreground">{spec.identity ? '身份已配置' : '未显式配置身份'}</span></summary>
        <div className="mt-4 space-y-4">
          <JsonDraftField label="执行身份" value={spec.identity} draftKey="workflow:identity" onApply={value => patch('identity', value)}
            description="不会自动替换或移除身份，不在此展示任何外部凭据默认值。" />
          <JsonDraftField label="工作流输出" value={spec.outputs} draftKey="workflow:outputs" onApply={value => patch('outputs', value)} />
        </div>
      </details>
      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer text-xs font-medium text-foreground">完整流程配置 <span className="ml-2 font-normal text-muted-foreground">不含节点</span></summary>
        <div className="mt-4">
          <JsonDraftField label="完整配置（显式应用）" value={settings} draftKey="workflow:settings" rows={16}
            description="包含未映射扩展字段。删除某个键会明确移除该配置；节点列表不在此编辑。"
            validate={value => {
              if (!value || typeof value !== 'object' || Array.isArray(value)) return '配置必须是对象'
              const item = value as Record<string, unknown>
              if (item.id !== spec.id || item.version !== spec.version) return '流程标识和版本请使用现有保存/版本管理入口修改'
              if (typeof item.title !== 'string' || !item.title.trim()) return '流程名称不能为空'
              if ('nodes' in item) return '节点请通过画布或 YAML 编辑'
              return null
            }}
            onApply={value => {
              const store = useEditorStore.getState()
              if (!store.spec || store.spec.id !== spec.id) throw new Error('工作流已切换，未应用草稿')
              store.setActionError(null)
              // Explicit whole-settings replacement is one atomic undoable import.
              store.importYaml({ ...(value as Record<string, unknown>), nodes: store.spec.nodes } as WorkflowSpec)
              const error = useEditorStore.getState().actionError
              if (error) throw new Error(error)
            }} />
        </div>
      </details>
    </div>
  </aside>
}
