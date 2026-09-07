export type BenchSessionEvent = {
  type?: string
  timestamp?: string
  message?: {
    role?: string
    content?: Array<Record<string, unknown>>
    usage?: Record<string, unknown>
  }
  [key: string]: unknown
}

export type BenchSessionContentBlock = {
  label: string
  text: string
  tone?: 'default' | 'thinking' | 'tool'
}

export function eventLabel(event: BenchSessionEvent): string {
  if (event.type === 'message') {
    const role = event.message?.role
    if (role === 'user') return '用户消息'
    if (role === 'assistant') return '模型回复'
    if (role === 'toolResult') return '工具结果'
    return '消息'
  }
  if (event.type === 'session') return '会话开始'
  if (event.type === 'model_change') return '模型切换'
  if (event.type === 'thinking_level_change') return '思考级别'
  if (event.type === 'custom') return '自定义事件'
  return event.type || '事件'
}

export function eventPreview(event: BenchSessionEvent): string {
  const content = event.message?.content
  if (Array.isArray(content)) {
    const text = content
      .map((item) => {
        if (typeof item.text === 'string') return item.text
        if (typeof item.thinking === 'string') return item.thinking
        if (typeof item.name === 'string') return `调用 ${item.name}`
        if (typeof item.type === 'string') return item.type
        return ''
      })
      .filter(Boolean)
      .join('\n')
    if (text) return text
  }
  if (typeof event.customType === 'string') return event.customType
  return JSON.stringify(event).slice(0, 400)
}

export function eventRole(event: BenchSessionEvent): string {
  const role = event.message?.role
  return typeof role === 'string' && role ? role : '-'
}

export function eventUsageText(event: BenchSessionEvent): string {
  const usage = event.message?.usage
  if (!usage || typeof usage !== 'object') return ''
  const input = numberText(usage.input_tokens ?? usage.inputTokens ?? usage.input)
  const output = numberText(usage.output_tokens ?? usage.outputTokens ?? usage.output)
  const cacheRead = numberText(usage.cache_read_tokens ?? usage.cacheReadTokens ?? usage.cacheRead)
  const total = numberText(usage.total_tokens ?? usage.totalTokens)
  const parts = [
    total ? `total ${total}` : '',
    input ? `in ${input}` : '',
    output ? `out ${output}` : '',
    cacheRead ? `cache ${cacheRead}` : '',
  ].filter(Boolean)
  return parts.join(' · ')
}

export function eventContentBlocks(event: BenchSessionEvent): BenchSessionContentBlock[] {
  const content = event.message?.content
  if (!Array.isArray(content)) return [{ label: '内容', text: eventPreview(event) }]
  const blocks: BenchSessionContentBlock[] = []
  content.forEach((item, idx) => {
    if (typeof item.text === 'string' && item.text.trim()) {
      blocks.push({ label: `文本 ${idx + 1}`, text: item.text })
    } else if (typeof item.thinking === 'string' && item.thinking.trim()) {
      blocks.push({ label: `思考 ${idx + 1}`, text: item.thinking, tone: 'thinking' })
    } else if (typeof item.name === 'string') {
      const args = item.arguments ?? item.input ?? item.params
      blocks.push({
        label: `工具调用 ${idx + 1}`,
        text: `${item.name}${args !== undefined ? `\n${stringifyBlock(args)}` : ''}`,
        tone: 'tool',
      })
    } else if (item.result !== undefined || item.output !== undefined) {
      blocks.push({ label: `工具结果 ${idx + 1}`, text: stringifyBlock(item.result ?? item.output), tone: 'tool' })
    } else {
      blocks.push({ label: `内容 ${idx + 1}`, text: stringifyBlock(item) })
    }
  })
  return blocks.length ? blocks : [{ label: '内容', text: eventPreview(event) }]
}

function stringifyBlock(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function numberText(value: unknown): string {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n.toLocaleString() : ''
}
