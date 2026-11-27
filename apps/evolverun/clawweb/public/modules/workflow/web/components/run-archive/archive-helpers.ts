import type { RunArchiveData } from '@avernet/clawweb-shared/web/types'

export type SectionId = 'overview' | 'process' | 'errors' | 'analysis' | 'yaml'
export type ProcessView = 'info' | 'nodes' | 'events' | 'logs'
export type Navigate = (section: SectionId, view?: ProcessView) => void

export const STATUS_ORDER = [
  'succeeded', 'failed', 'running', 'pending', 'waiting', 'blocked',
  'skipped', 'cancelled', 'timeout', 'unknown',
] as const
export type NodeStatus = typeof STATUS_ORDER[number]

const STATUS_LABELS: Record<NodeStatus, string> = {
  succeeded: '成功', failed: '失败', running: '运行中', pending: '待执行',
  waiting: '等待中', blocked: '已阻塞', skipped: '已跳过',
  cancelled: '已中止', timeout: '已超时', unknown: '未知',
}

export function text(value: unknown, fallback = '未记录'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

export function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null
  if (typeof value === 'string' && value.trim() === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function recordedCount(value: unknown): string {
  const count = finiteNumber(value)
  return count !== null && count >= 0 && Number.isInteger(count) ? String(count) : '未记录'
}

/** Stored timestamps may be epoch seconds, epoch milliseconds, ISO or SQL text. */
export function parseTimestamp(value: unknown): Date | null {
  if (value === null || value === undefined || value === '') return null
  let timestamp: string | number
  if (typeof value === 'number') timestamp = value
  else if (typeof value === 'string' && value.trim()) {
    const trimmed = value.trim()
    timestamp = /^-?\d+(\.\d+)?$/.test(trimmed)
      ? Number(trimmed)
      : trimmed.replace(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})/, '$1T$2')
  } else return null
  if (typeof timestamp === 'number') {
    if (!Number.isFinite(timestamp)) return null
    timestamp = Math.abs(timestamp) < 100_000_000_000 ? timestamp * 1000 : timestamp
  }
  const date = new Date(timestamp)
  return Number.isFinite(date.getTime()) ? date : null
}

export function formatTime(value: unknown): string {
  if (value === null || value === undefined || (typeof value === 'string' && !value.trim())) return '未记录'
  const date = parseTimestamp(value)
  return date ? date.toLocaleString('zh-CN', { hour12: false }) : '无效时间'
}

export function formatDuration(value: unknown): string {
  const ms = finiteNumber(value)
  if (ms === null || ms < 0) return '未记录'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m${Math.floor((ms % 60_000) / 1000)}s`
}

export function nodeStatus(value: unknown): NodeStatus {
  const status = typeof value === 'string' ? value.trim().toLowerCase() : ''
  if (status === 'canceled') return 'cancelled'
  return STATUS_ORDER.includes(status as NodeStatus) ? status as NodeStatus : 'unknown'
}

export function statusLabel(value: unknown): string {
  const key = nodeStatus(value)
  const raw = typeof value === 'string' ? value.trim() : ''
  return key === 'unknown' && raw && raw !== 'unknown' ? `未知（${raw}）` : STATUS_LABELS[key]
}

export function statusClass(value: unknown): string {
  switch (nodeStatus(value)) {
    case 'succeeded': return 'bg-success/10 text-success'
    case 'failed': case 'timeout': return 'bg-destructive/10 text-destructive'
    case 'running': return 'bg-primary/10 text-primary'
    default: return 'bg-muted text-muted-foreground'
  }
}

/** Malformed or truncated JSON remains inspectable as the exact stored text. */
export function prettyJson(value: unknown, empty = '未记录'): string {
  if (value === null || value === undefined || value === '') return empty
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2) }
    catch { return value }
  }
  try { return JSON.stringify(value, null, 2) ?? String(value) }
  catch { return String(value) }
}

export function analysisLabel(analysis: RunArchiveData['analysis']): string {
  if (!analysis) return '未采集分析'
  if (analysis.status === 'failed') return '分析失败'
  if (analysis.status === 'skipped') return '分析已跳过'
  return `分析已完成 · ${analysis.diagnoses.length} 条诊断`
}

export function analysisSource(source: string): string {
  const labels: Record<string, string> = {
    reused: '复用已有分析', preview: '本次预览分析', failed: '分析来源不可用',
  }
  return labels[source] ?? source
}

type SummaryFailure = RunArchiveData['failureSummary']['failedNodes'][number]
export type FailureEntry = Omit<SummaryFailure, 'attempt'> & {
  attempt: number | null
  execution?: Record<string, unknown>
}

/** Preserve recorded attempts; do not infer failure from absent executions. */
export function failureEntries(data: RunArchiveData): FailureEntry[] {
  const entries: FailureEntry[] = data.failureSummary.failedNodes.map(failure => {
    const attempt = finiteNumber(failure.attempt)
    const matching = data.nodeExecutions.filter(node => text(node.node_id, '') === failure.nodeId)
    const execution = attempt === null
      ? (matching.length === 1 ? matching[0] : undefined)
      : matching.find(node => finiteNumber(node.attempt) === attempt)
    return { ...failure, attempt, execution }
  })
  for (const node of data.nodeExecutions) {
    if (nodeStatus(node.status) !== 'failed') continue
    const nodeId = text(node.node_id)
    const attempt = finiteNumber(node.attempt)
    if (entries.some(entry => entry.nodeId === nodeId && entry.attempt === attempt)) continue
    entries.push({
      nodeId, attempt, execution: node,
      nodeTitle: node.node_title == null ? null : String(node.node_title),
      executorType: node.executor_type == null ? null : String(node.executor_type),
      error: node.error_text == null ? null : String(node.error_text),
      embeddedSessionKey: node.embedded_session_key == null ? null : String(node.embedded_session_key),
    })
  }
  return entries
}

export function failureCount(data: RunArchiveData): number {
  return Math.max(finiteNumber(data.failureSummary.failedNodeCount) ?? 0, failureEntries(data).length)
}

export function isErrorEvent(event: Record<string, unknown>): boolean {
  return Boolean(event.error_text) || /failed|error|timeout/i.test(text(event.event_type, ''))
}
