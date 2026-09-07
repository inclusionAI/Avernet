import type { BenchTokenUsage } from '../types'

export function formatCompactNumber(value: unknown): string {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return new Intl.NumberFormat('zh-CN').format(Math.round(n))
}

export function formatTokenUsage(usage?: BenchTokenUsage | null): string {
  if (!usage) return '暂无 Token 数据'
  if (typeof usage.totalTokens === 'number') return formatCompactNumber(usage.totalTokens)
  const total = (usage.inputTokens ?? 0) + (usage.outputTokens ?? 0) + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0)
  return total > 0 ? formatCompactNumber(total) : '暂无 Token 数据'
}

export function tokenBreakdownText(usage?: BenchTokenUsage | null): string {
  if (!usage) return ''
  const parts = [
    usage.inputTokens !== undefined ? `输入 ${formatCompactNumber(usage.inputTokens)}` : '',
    usage.outputTokens !== undefined ? `输出 ${formatCompactNumber(usage.outputTokens)}` : '',
    usage.cacheReadTokens !== undefined ? `缓存 ${formatCompactNumber(usage.cacheReadTokens)}` : '',
    usage.requestCount !== undefined ? `请求 ${formatCompactNumber(usage.requestCount)}` : '',
  ].filter(Boolean)
  return parts.join(' · ')
}

export function tokenValue(usage: BenchTokenUsage | null | undefined, key: keyof BenchTokenUsage): string {
  const value = usage?.[key]
  return typeof value === 'number' ? formatCompactNumber(value) : '-'
}
