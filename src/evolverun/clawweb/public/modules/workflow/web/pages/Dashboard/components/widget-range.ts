export type WidgetRange = 'global' | '7d' | '30d'

export function resolveWidgetRange(
  value: WidgetRange,
  globalRange: { from: number; to: number; label: string },
): { from: number; to: number; label: string } {
  if (value === 'global') return globalRange
  const days = value === '7d' ? 7 : 30
  return { from: globalRange.to - days * 86400, to: globalRange.to, label: `近 ${days} 天` }
}
