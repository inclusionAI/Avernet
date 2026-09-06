import type { CompletionState } from '../../types/insight'

export const failureClassText: Record<string, string> = {
  TOOL_FAILURE: '工具或环境失败',
  USER_INTERRUPTION: '用户中断',
  AGENT_FAILURE: 'Agent 执行失败',
  REQUIREMENT_MISUNDERSTANDING: '需求理解偏差',
  SYSTEM_FAILURE: '系统异常',
  TIMEOUT: '执行超时',
  UNKNOWN: '待归类',
  COMPLETED: '已完成',
}

export const completionText: Record<CompletionState, string> = {
  0: '未完成',
  1: '已完成',
  2: '未知',
  3: '已中止',
}

export function formatRate(value: number | null): string {
  return value == null ? '—' : `${(value * 100).toFixed(value * 100 % 1 === 0 ? 0 : 1)}%`
}

export function formatCompactDate(value: string): string {
  const compact = value.replaceAll('-', '').slice(0, 8)
  if (!/^\d{8}$/.test(compact)) return value
  return `${compact.slice(4, 6)}/${compact.slice(6, 8)}`
}

export function formatDateTime(value: string | number | null | undefined): string {
  if (value == null || value === '') return '—'
  const date = typeof value === 'number'
    ? new Date(value < 10_000_000_000 ? value * 1000 : value)
    : new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const value = Math.max(0, Math.round(seconds))
  if (value < 60) return `${value} 秒`
  if (value < 3600) return `${Math.floor(value / 60)} 分 ${value % 60} 秒`
  return `${Math.floor(value / 3600)} 小时 ${Math.floor((value % 3600) / 60)} 分`
}

export function formatMessageRange(range: [number, number]): string {
  return `${range[0]}–${Math.max(range[0], range[1] - 1)}`
}

export function jsonText(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value ?? '')
  }
}

export function createRequestId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
}
