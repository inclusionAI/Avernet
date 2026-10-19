import type { NormalizedTrialInputDefinition } from '../../../../server/contracts/repair-trial-input-definition'
import { button } from './repair-view'
import TrialValueInput, { completeTrialValue, trialAllowsDynamicFields, trialPropertySchema, trialPropertyValue, trialValueDefaults, trialValueType, type TrialValueSchema } from './TrialValueInput'

type Field = TrialValueSchema | boolean
export type TrialParameterEntry = { key: string; value: unknown }
function fields(schema: Record<string, unknown> | null): Record<string, Field> {
  return (schema?.properties ?? {}) as Record<string, Field>
}
export function trialInputDefaults(schema: Record<string, unknown> | null): Record<string, unknown> {
  const defaults = trialValueDefaults((schema ?? {}) as TrialValueSchema)
  return defaults && typeof defaults === 'object' && !Array.isArray(defaults) ? defaults as Record<string, unknown> : {}
}
export function trialInputEntries(schema: Record<string, unknown> | null, input: Record<string, unknown>): TrialParameterEntry[] {
  const entries = Object.entries(input).filter(([key]) => !Object.hasOwn(fields(schema), key)).map(([key, value]) => ({ key, value }))
  return entries.length ? entries : [{ key: '', value: '' }]
}
export function readTrialFormInput(definition: NormalizedTrialInputDefinition, params: Record<string, unknown>, entries: TrialParameterEntry[], checkRequired = true): Record<string, unknown> {
  if (definition.inputDefinition === 'none') return {}
  const properties = fields(definition.inputSchema)
  const present = entries.filter(entry => entry.key.trim() || entry.value !== undefined && entry.value !== '')
  if (present.some(entry => !entry.key.trim())) throw new Error('请填写参数名。')
  if (new Set(present.map(entry => entry.key.trim())).size !== present.length) throw new Error('参数名不能重复。')
  if (present.some(entry => Object.hasOwn(properties, entry.key.trim()))) throw new Error('参数名已声明，请编辑对应字段。')
  const rawInput = { ...Object.fromEntries(Object.entries(params).filter(([key]) => Object.hasOwn(properties, key))), ...Object.fromEntries(present.map(entry => [entry.key.trim(), entry.value])) }
  const input = completeTrialValue((definition.inputSchema ?? {}) as TrialValueSchema, rawInput) as Record<string, unknown>
  for (const key of checkRequired ? (definition.inputSchema?.required ?? []) as string[] : []) {
    if (trialValueType(properties[key] ?? {}) === 'boolean' && input[key] === undefined) input[key] = false
    if (!Object.hasOwn(input, key)) throw new Error(`请填写必填参数：${key}`)
  }
  return input
}
export function parseTrialJson(json: string): Record<string, unknown> {
  const input = JSON.parse(json)
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('参数需要 JSON 对象。')
  return input
}

export default function RepairTrialInputs({ definition, params, entries, advanced, json, locked, onParams, onEntries, onJson, onToggle }: {
  definition: NormalizedTrialInputDefinition; params: Record<string, unknown>; entries: TrialParameterEntry[]; advanced: boolean; json: string; locked: boolean;
  onParams: (params: Record<string, unknown>) => void; onEntries: (entries: TrialParameterEntry[]) => void; onJson: (json: string) => void; onToggle: () => void;
}) {
  const properties = fields(definition.inputSchema)
  const showDynamic = trialAllowsDynamicFields((definition.inputSchema ?? {}) as TrialValueSchema) || entries.some(entry => entry.key.trim())
  const update = (index: number, entry: Partial<TrialParameterEntry>) => onEntries(entries.map((previous, i) => i === index ? { ...previous, ...entry } : previous))
  return <div className="space-y-3">
    {advanced ? <label className="block text-sm">输入参数 JSON<textarea aria-label="输入参数 JSON" className="mt-2 block min-h-28 w-full rounded border border-input bg-background p-3 font-mono" disabled={locked} value={json} onChange={event => onJson(event.target.value)} /></label>
      : definition.inputDefinition === 'none' ? <p className="text-sm text-muted-foreground">此工作流无需参数。</p>
      : <>
        {Object.keys(properties).map(key => <TrialValueInput key={key} schema={trialPropertySchema((definition.inputSchema ?? {}) as TrialValueSchema, key)} name={key} value={params[key]} required={Array.isArray(definition.inputSchema?.required) && definition.inputSchema.required.includes(key)} disabled={locked} onChange={value => onParams({ ...params, [key]: value })} />)}
        {showDynamic && <><p className="text-sm text-muted-foreground">{Object.keys(properties).length ? '可填写其他业务参数。' : '未声明可展示的参数字段，可按业务名称填写参数。'}</p>
        {entries.map((entry, index) => <div key={index} className="flex flex-wrap gap-2">
          <input aria-label={`参数名 ${index + 1}`} placeholder="参数名" className="min-w-0 flex-1 rounded border border-input bg-background p-2" disabled={locked} value={entry.key} onChange={event => update(index, { key: event.target.value, value: trialPropertyValue((definition.inputSchema ?? {}) as TrialValueSchema, entry.key.trim(), event.target.value.trim(), entry.value) })} />
          <TrialValueInput schema={trialPropertySchema((definition.inputSchema ?? {}) as TrialValueSchema, entry.key.trim())} name={`参数值 ${index + 1}`} value={entry.value} disabled={locked} sensitive={/password|token|secret|key/i.test(entry.key)} onChange={value => update(index, { value })} />
          <button aria-label={`移除参数 ${index + 1}`} className={button} disabled={locked} onClick={() => onEntries(entries.filter((_, i) => i !== index))}>移除</button>
        </div>)}
        <button className={button} disabled={locked} onClick={() => onEntries([...entries, { key: '', value: '' }])}>添加参数</button>
        </>}
      </>}
    <button className={button} disabled={locked} onClick={onToggle}>{advanced ? '返回业务表单' : '高级：编辑 JSON'}</button>
  </div>
}
