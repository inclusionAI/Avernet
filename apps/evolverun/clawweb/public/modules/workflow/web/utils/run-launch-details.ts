import type { RunLaunchMetadata } from '../../shared/run-launch-metadata'

export interface LaunchField {
  label: string
  source: string
  text: string | null
  json?: boolean
  note?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function hasInternalState(value: unknown): boolean {
  const pending = [value]
  while (pending.length > 0) {
    const current = pending.pop()
    if (current === null || typeof current !== 'object') continue
    for (const [key, child] of Object.entries(current)) {
      if (key === '_clawmind_state') return true
      pending.push(child)
    }
  }
  return false
}

function inputField(raw: string | null | undefined): LaunchField {
  const field: LaunchField = { label: '输入文本', source: 'flow_runs.input_json', text: null }
  if (!raw?.trim()) return field
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return { ...field, note: '输入记录格式异常，无法提取文本' }
  }
  if (typeof value === 'string') {
    return { ...field, text: value.trim() ? value : null, note: '字符串格式的输入记录' }
  }
  if (!isRecord(value)) return field
  // Only these user-facing fields are safe to display. The rest is runtime metadata.
  if (typeof value.message === 'string' && value.message.trim()) {
    return { ...field, source: 'flow_runs.input_json · message', text: value.message }
  }
  if (typeof value.command === 'string' && value.command.trim()) {
    return { ...field, label: '已存储命令', source: 'flow_runs.input_json · command', text: value.command }
  }
  return field
}

function paramsField(raw: string | null | undefined): LaunchField {
  const field: LaunchField = { label: '原始启动参数', source: 'flow_runs.params_json', text: null }
  if (!raw?.trim()) return field
  // Reject even malformed adapter envelopes rather than exposing execution state.
  if (raw.includes('_clawmind_state')) return { ...field, note: '仅有执行状态，未记录原始启动参数' }
  try {
    const value: unknown = JSON.parse(raw)
    if (hasInternalState(value)) return { ...field, note: '仅有执行状态，未记录原始启动参数' }
    if (value === null || (typeof value === 'string' && !value.trim())) return field
    return {
      ...field,
      text: typeof value === 'string' ? value : JSON.stringify(value, null, 2),
      json: typeof value !== 'string',
    }
  } catch {
    return { ...field, note: '启动参数记录格式异常，无法确认原始输入' }
  }
}

export function getRunLaunchDetails(run: Partial<Pick<RunLaunchMetadata, 'params_json' | 'input_json'>>) {
  // input_json.params can change on retry; it is never an original-parameter fallback.
  return { input: inputField(run.input_json), params: paramsField(run.params_json) }
}
