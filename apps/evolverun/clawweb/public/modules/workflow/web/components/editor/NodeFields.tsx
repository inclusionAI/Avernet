import { useId, type ChangeEvent } from 'react'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from '../../editor/store'
import JsonDraftField, { isRecord } from './JsonDraftField'

export const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-primary disabled:opacity-50'
const buttonClass = 'rounded-md border border-input px-2 py-1 text-xs disabled:opacity-50'
export const configSummary = (value: unknown) => value === undefined ? '未配置' : JSON.stringify(value)

// Immediate form edits report rejection without throwing out of a DOM event.
function changeField(action: () => void) {
  try { action() }
  catch (cause) { useEditorStore.getState().setActionError(cause instanceof Error ? cause.message : String(cause)) }
}

export function TextField({ label, value, onChange, multiline = false, description, readOnly = false }: {
  label: string; value: unknown; onChange?: (value: string) => void
  multiline?: boolean; description?: string; readOnly?: boolean
}) {
  const id = useId()
  const pending = useEditorStore(state => Object.keys(state.pendingEdits).length > 0)
  const compatible = value === undefined || typeof value === 'string'
  const props = {
    id, value: typeof value === 'string' ? value : value === undefined ? '' : JSON.stringify(value),
    className: inputClass, readOnly: readOnly || !compatible, disabled: pending && !readOnly,
    onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      if (!pending && !readOnly && compatible) changeField(() => onChange?.(event.target.value))
    },
  }
  return <div className="space-y-2">
    <label htmlFor={id} className="block text-sm font-medium">{label}</label>
    {multiline ? <textarea {...props} rows={5} /> : <input {...props} />}
    {description && <p className="text-xs text-muted-foreground">{description}</p>}
    {!compatible && <p className="text-xs text-muted-foreground">保留原始值，请在完整 JSON 中编辑。</p>}
  </div>
}

export function SelectField({ label, value, options, onChange }: {
  label: string; value: unknown; options: readonly [string, string][]; onChange: (value: string | undefined) => void
}) {
  const id = useId()
  const pending = useEditorStore(state => Object.keys(state.pendingEdits).length > 0)
  const known = options.some(([key]) => key === value)
  return <div className="space-y-2">
    <label htmlFor={id} className="block text-sm font-medium">{label}</label>
    <select id={id} className={inputClass} disabled={pending} value={value === undefined ? 'missing' : JSON.stringify(value)}
      onChange={event => {
        if (pending) return
        if (event.target.value === 'missing') {
          if (value !== undefined && window.confirm(`确认删除「${label}」的显式配置，恢复引擎默认行为？`)) changeField(() => onChange(undefined))
        } else changeField(() => onChange(JSON.parse(event.target.value)))
      }}>
      <option value="missing">未显式配置（使用引擎默认）</option>
      {!known && value !== undefined && <option value={JSON.stringify(value)}>原始值：{JSON.stringify(value)}</option>}
      {options.map(([key, text]) => <option key={key} value={JSON.stringify(key)}>{text}</option>)}
    </select>
    <p className="text-xs text-muted-foreground">查看不会补默认值；选择后才写入。</p>
  </div>
}

export default function NodeFields({ node, onPatch }: { node: WorkflowNode; onPatch: (patch: Partial<WorkflowNode>) => void }) {
  const executor = node.executor
  const pending = useEditorStore(state => Object.keys(state.pendingEdits).length > 0)
  function setField(key: string, value: unknown) {
    const next = { ...executor }
    if (value === undefined) delete next[key]
    else next[key] = value
    onPatch({ executor: next })
  }
  const text = (key: string, label: string, multiline = false, description?: string) =>
    <TextField label={label} value={executor[key]} multiline={multiline} description={description} onChange={value => setField(key, value)} />
  const json = (key: string, label: string, description?: string) =>
    <JsonDraftField label={label} value={executor[key]} draftKey={`node:${node.id}:executor.${key}`}
      onApply={value => setField(key, value)} description={description}
      allowRemove={Object.hasOwn(executor, key)} onRemove={() => setField(key, undefined)} />
  const structured = (key: string, label: string) => <details className="rounded-md border border-input p-3">
    <summary className="cursor-pointer text-sm font-medium">{label}<span className="ml-2 text-xs text-muted-foreground">{executor[key] === undefined ? '未配置' : '已配置'}</span></summary>
    <p className="my-2 truncate font-mono text-xs text-muted-foreground" title={configSummary(executor[key])}>{configSummary(executor[key])}</p>
    {json(key, label)}
  </details>
  const simpleArgs = executor.args === undefined || (Array.isArray(executor.args) && executor.args.every(value => typeof value === 'string'))
  const args = simpleArgs && Array.isArray(executor.args) ? executor.args as string[] : []
  const agent = executor.type === 'embedded-agent' || executor.type === 'subagent'
  const supported = ['embedded-agent', 'subagent', 'cli-script', 'mcp-call', 'human', 'approval', 'done'].includes(executor.type)

  return <div className="space-y-5">
    {agent && <>
      {text('skillName', executor.type === 'embedded-agent' ? '技能名称（可选）' : '技能名称', false, '填写已有技能名称；不会自动查找或预置技能。')}
      {text('prompt', '任务指令', true, '用文字描述这一步。模板与 JSON 形式的文字也按原文保存。')}
    </>}
    {executor.type === 'cli-script' && <>
      {text('command', '命令或脚本路径', false, '按原文保存，不按空格拆分命令，也不会执行脚本。')}
      {simpleArgs ? <div className="space-y-3">
        <p className="text-sm font-medium">参数列表 <span className="text-xs font-normal text-muted-foreground">每项独立传入；空格保留</span></p>
        {args.map((arg, index) => <div key={index} className="space-y-1">
          <TextField label={`参数 ${index + 1}`} value={arg}
            onChange={value => setField('args', args.map((item, i) => i === index ? value : item))} />
          <button type="button" className={buttonClass} disabled={pending} aria-label={`删除参数 ${index + 1}`}
            onClick={() => { if (window.confirm(`确认删除参数 ${index + 1}？`)) changeField(() => setField('args', args.filter((_, i) => i !== index))) }}>删除此参数</button>
        </div>)}
        <button type="button" className={buttonClass} disabled={pending} onClick={() => changeField(() => setField('args', [...args, '']))}>添加参数</button>
      </div> : <p className="text-xs text-muted-foreground">参数不是字符串数组，保留原始配置；请使用 JSON 编辑。</p>}
      <details className="rounded-md border border-input p-3" open={!simpleArgs}>
        <summary className="cursor-pointer text-sm font-medium">原始参数 JSON</summary>
        <div className="mt-3">{json('args', '完整参数', '完整保留数组、字典及模板参数，不进行 shell 拼接或空格拆分。')}</div>
      </details>
    </>}
    {executor.type === 'human' && <>
      {text('prompt', '希望用户做什么', true)}
      <p className="text-xs text-muted-foreground">人工输入会等待用户回应；确认、修订、拒绝与保存规则均保留，不等同于审批。</p>
      {structured('inputSchema', '输入字段定义')}
      {structured('actions', '确认、修订与拒绝动作')}
      {structured('saveAs', '输入保存规则')}
    </>}
    {executor.type === 'approval' && <>
      {text('message', '审批内容', true)}
      {text('skillName', '审批技能')}
      {structured('approvers', '审批人配置')}
      <SelectField label="通过规则" value={executor.approvalPolicy} options={[
        ['any', '任一审批人通过'], ['all', '全部审批人通过'], ['majority', '多数审批人通过'],
      ]} onChange={value => setField('approvalPolicy', value)} />
      {structured('delivery', '审批渠道')}
      <p className="text-xs text-muted-foreground">人员与渠道需填写实际配置，此面板不查询人员目录或发送审批。</p>
    </>}
    {executor.type === 'mcp-call' && <>
      {text('server', '工具服务')}{text('tool', '工具名称')}
      {json('args', '工具参数', '填写真实工具参数。此面板不提供虚构工具目录，也不会调用工具。')}
    </>}
    {executor.type === 'done' && <>
      {text('message', '完成文案', true)}
      <p className="rounded-md bg-muted p-3 text-xs text-muted-foreground">仅标记当前节点完成，不会取消其他分支或强制终止整个工作流。</p>
    </>}
    {['embedded-agent', 'cli-script', 'mcp-call'].includes(executor.type) &&
      <SelectField label="输出形式" value={executor.outputMode} options={[
        ['json', '结构化 JSON'], ['text', '文本'],
      ]} onChange={value => setField('outputMode', value)} />}
    {!supported && <div className="space-y-3">
      <p className="rounded-md bg-muted p-3 text-xs text-muted-foreground">兼容配置模式：{executor.type}。保留真实类型及全部字段，不自动转换类型。</p>
      <JsonDraftField label="执行器原始配置" value={executor} draftKey={`node:${node.id}:executor`}
        rows={12} validate={value => !isRecord(value) || value.type !== executor.type ? '执行器必须是对象，且 type 不可修改。' : null}
        onApply={value => onPatch({ executor: value as WorkflowNode['executor'] })} />
    </div>}
  </div>
}
