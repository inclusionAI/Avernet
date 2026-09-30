export type WidgetRange = 'global' | 'yesterday' | '7d' | '30d'

export function resolveWidgetRange(
  value: WidgetRange,
  globalRange: { from: number; to: number; label: string },
): { from: number; to: number; label: string } {
  if (value === 'global') return globalRange
  if (value === 'yesterday') {
    const now = Date.now()
    const localStart = new Date(now)
    localStart.setHours(0, 0, 0, 0)
    const startOfToday = Math.floor(localStart.getTime() / 1000)
    const daySec = 86400
    return { from: startOfToday - daySec, to: startOfToday - 1, label: '昨天' }
  }
  const days = value === '7d' ? 7 : 30
  return { from: globalRange.to - days * 86400, to: globalRange.to, label: `近 ${days} 天` }
}
