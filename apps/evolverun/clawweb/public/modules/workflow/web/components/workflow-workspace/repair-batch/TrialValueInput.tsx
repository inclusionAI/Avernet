import { useState } from 'react'
import Ajv from 'ajv'
import { button } from './repair-view'

export type TrialValueSchema = {
  type?: string | string[]; title?: string; description?: string; enum?: unknown[]; default?: unknown;
  sensitive?: boolean; writeOnly?: boolean; format?: string; required?: string[];
  properties?: Record<string, TrialValueSchema | boolean>; items?: TrialValueSchema | boolean | TrialValueSchema[];
  additionalProperties?: TrialValueSchema | boolean; patternProperties?: Record<string, TrialValueSchema | boolean>;
}
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value) }
export function trialValueType(schema: TrialValueSchema | boolean, value?: unknown): string {
  const spec = typeof schema === 'boolean' ? {} : schema
  const declared = Array.isArray(spec.type) ? spec.type.find(type => type !== 'null') ?? 'null' : spec.type
  return declared ?? (spec.properties ? 'object' : spec.items ? 'array' : Array.isArray(value) ? 'array' : value !== null && typeof value === 'object' ? 'object' : typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : 'string')
}
function properties(schema: TrialValueSchema): Record<string, TrialValueSchema | boolean> {
  const fields = { ...schema.properties }
  for (const name of schema.required ?? []) if (!Object.hasOwn(fields, name)) fields[name] = { type: 'string' }
  return fields
}
/** Control hints combine matching rules; the original schema remains the validation authority. */
export function trialPropertySchema(schema: TrialValueSchema, key: string): TrialValueSchema | boolean {
  const matched: (TrialValueSchema | boolean)[] = []
  if (Object.hasOwn(schema.properties ?? {}, key)) matched.push(schema.properties![key])
  try {
    for (const [pattern, child] of Object.entries(schema.patternProperties ?? {})) if (new RegExp(pattern, 'u').test(key)) matched.push(child)
  } catch { return false }
  if (!matched.length) return schema.additionalProperties ?? {}
  if (matched.includes(false)) return false
  return Object.assign({}, ...matched.filter(child => typeof child !== 'boolean'))
}
export function trialAllowsDynamicFields(schema: TrialValueSchema): boolean {
  return schema.additionalProperties !== false || Object.keys(schema.patternProperties ?? {}).length > 0
}
export function trialPropertyValue(schema: TrialValueSchema, previousKey: string, key: string, value: unknown): unknown {
  const previous = trialPropertySchema(schema, previousKey), next = trialPropertySchema(schema, key)
  return value === undefined || value === '' || trialValueType(previous, value) !== trialValueType(next, value) ? newValue(next) : value
}
function itemSchema(schema: TrialValueSchema, index: number): TrialValueSchema | boolean {
  return Array.isArray(schema.items) ? schema.items[index] ?? {} : schema.items ?? {}
}
export function trialValueDefaults(schema: TrialValueSchema | boolean): unknown {
  if (typeof schema === 'boolean') return undefined
  if (Object.hasOwn(schema, 'default')) return structuredClone(schema.default)
  if (trialValueType(schema) !== 'object') return undefined
  const defaults = Object.fromEntries(Object.entries(properties(schema)).map(([key, spec]) => [key, trialValueDefaults(spec)]).filter(([, value]) => value !== undefined))
  return Object.keys(defaults).length ? defaults : undefined
}
function newValue(schema: TrialValueSchema | boolean): unknown {
  const defaults = trialValueDefaults(schema)
  if (defaults !== undefined) return defaults
  if (typeof schema !== 'boolean' && schema.enum?.length) return structuredClone(schema.enum[0])
  switch (trialValueType(schema)) {
    case 'object': return {}
    case 'array': return []
    case 'number': case 'integer': return 0
    case 'boolean': return false
    case 'null': return null
    default: return ''
  }
}
function clearedString(schema: TrialValueSchema, required: boolean | undefined): unknown {
  try {
    const validate = new Ajv({ strict: false, ownProperties: true, logger: false }).compile(schema)
    if (validate('')) return ''
    return required ? validate(null) ? null : '' : undefined
  } catch { return required ? '' : undefined }
}
/** Remove cleared optional fields before hashing/submission; untouched required checkboxes represent false. */
export function completeTrialValue(schema: TrialValueSchema | boolean, value: unknown): unknown {
  if (typeof schema === 'boolean') return value
  if (Array.isArray(value)) return value.map((item, index) => completeTrialValue(itemSchema(schema, index), item))
  if (!record(value)) return value
  const fields = properties(schema)
  const completed = Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined).map(([key, item]) => [key, completeTrialValue(trialPropertySchema(schema, key), item)]))
  for (const key of schema.required ?? []) if (completed[key] === undefined && trialValueType(fields[key] ?? {}) === 'boolean') completed[key] = false
  return completed
}

const control = 'mt-1 block w-full min-w-0 rounded border border-input bg-background p-2'
type Props = { schema: TrialValueSchema | boolean; name: string; value: unknown; disabled: boolean; required?: boolean; sensitive?: boolean; onChange: (value: unknown) => void }
function OpenObjectInput({ schema, name, value, disabled, sensitive, onChange }: Props & { schema: TrialValueSchema }) {
  const [key, setKey] = useState('')
  const [newItem, setNewItem] = useState<unknown>('')
  const [error, setError] = useState('')
  const current = record(value) ? value : {}
  return <div className="space-y-2">
    {Object.entries(current).filter(([key]) => !Object.hasOwn(properties(schema), key)).map(([key, item]) => <div key={key} className="space-y-1 rounded border border-input p-2">
      <TrialValueInput schema={trialPropertySchema(schema, key)} name={`${name}.${key}`} value={item} required={schema.required?.includes(key)} disabled={disabled} sensitive={sensitive} onChange={next => onChange({ ...current, [key]: next })} />
      <button type="button" aria-label={`移除 ${name}.${key}`} className={button} disabled={disabled} onClick={() => onChange(Object.fromEntries(Object.entries(current).filter(([other]) => other !== key)))}>移除字段</button>
    </div>)}
    <div className="flex flex-wrap gap-2">
      <input aria-label={`${name} 新字段名`} placeholder="字段名" className={`${control} flex-1`} value={key} disabled={disabled} onChange={event => {
        setNewItem(trialPropertyValue(schema, key.trim(), event.target.value.trim(), newItem)); setKey(event.target.value); setError('')
      }} />
      <TrialValueInput schema={trialPropertySchema(schema, key.trim())} name={`${name} 新字段值`} value={newItem} disabled={disabled} sensitive={sensitive || /password|token|secret|key/i.test(key)} onChange={setNewItem} />
      <button type="button" aria-label={`${name} 添加字段`} className={button} disabled={disabled || !key.trim()} onClick={() => {
        if (Object.hasOwn(properties(schema), key.trim()) || Object.hasOwn(current, key.trim())) { setError('字段名不能重复。'); return }
        onChange({ ...current, [key.trim()]: newItem }); setKey(''); setNewItem(''); setError('')
      }}>添加字段</button>
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </div>
}
export default function TrialValueInput({ schema, name, value, disabled, required, sensitive, onChange }: Props) {
  const spec = typeof schema === 'boolean' ? {} : schema
  const type = trialValueType(schema, value)
  const masked = sensitive || spec.sensitive || spec.writeOnly || spec.format === 'password' || /password|token|secret|key/i.test(name)
  const label = <>{spec.title ?? name.split('.').at(-1)}{required ? ' *' : ''}</>
  const description = spec.description && <span className="mt-1 block text-muted-foreground">{spec.description}</span>
  if (schema === false) return <p className="text-sm text-muted-foreground">{label}：此字段不接受输入。</p>
  if (!spec.enum && type === 'object') {
    const fields = properties(spec), current = record(value) ? value : {}
    return <fieldset aria-label={name} className="min-w-0 space-y-3 rounded border border-input p-3"><legend className="px-1 text-sm">{label}</legend>
      {Object.entries(fields).map(([key, child]) => <TrialValueInput key={key} schema={Object.hasOwn(spec.properties ?? {}, key) ? trialPropertySchema(spec, key) : child} name={`${name}.${key}`} value={current[key]} required={spec.required?.includes(key)} disabled={disabled} sensitive={masked} onChange={next => onChange({ ...current, [key]: next })} />)}
      {(trialAllowsDynamicFields(spec) || Object.keys(current).some(key => !Object.hasOwn(fields, key))) && <OpenObjectInput schema={spec} name={name} value={current} disabled={disabled} sensitive={masked} onChange={onChange} />}
      {description}
    </fieldset>
  }
  if (!spec.enum && type === 'array') {
    const values = Array.isArray(value) ? value : []
    return <fieldset aria-label={name} className="min-w-0 space-y-3 rounded border border-input p-3"><legend className="px-1 text-sm">{label}</legend>
      {values.map((item, index) => <div key={index} className="space-y-2 rounded border border-input p-2">
        <TrialValueInput schema={itemSchema(spec, index)} name={`${name}.${index + 1}`} value={item} required disabled={disabled} sensitive={masked} onChange={next => onChange(values.map((previous, i) => i === index ? next : previous))} />
        <button type="button" aria-label={`移除 ${name} 第 ${index + 1} 项`} className={button} disabled={disabled} onClick={() => onChange(values.filter((_, i) => i !== index))}>移除条目</button>
      </div>)}
      <button type="button" aria-label={`添加 ${name} 条目`} className={button} disabled={disabled || spec.items === false} onClick={() => onChange([...values, newValue(itemSchema(spec, values.length))])}>添加条目</button>
      {description}
    </fieldset>
  }
  return <label className="block break-words text-sm">{label}
    {spec.enum ? <select aria-label={name} className={control} disabled={disabled} value={value === undefined ? '' : JSON.stringify(value)} onChange={event => onChange(event.target.value === '' ? undefined : JSON.parse(event.target.value))}>
      <option value="" disabled={required}>请选择</option>{spec.enum.map((item, index) => <option key={JSON.stringify(item)} value={JSON.stringify(item)}>{typeof item === 'object' ? `选项 ${index + 1}` : String(item)}</option>)}
    </select> : type === 'boolean' ? <input aria-label={name} type="checkbox" checked={value === true} disabled={disabled} onChange={event => onChange(event.target.checked)} />
      : <input aria-label={name} type={masked ? 'password' : ['number', 'integer'].includes(type) ? 'number' : 'text'} step={type === 'integer' ? 1 : 'any'} className={control} disabled={disabled} value={value == null ? '' : String(value)}
        onChange={event => {
          const text = event.target.value
          onChange(['number', 'integer'].includes(type) ? text !== '' ? Number(text)
            : Array.isArray(spec.type) && spec.type.includes('null') ? null : required ? '' : undefined : text === '' ? clearedString(spec, required) : text)
        }} />}
    {description}
  </label>
}
