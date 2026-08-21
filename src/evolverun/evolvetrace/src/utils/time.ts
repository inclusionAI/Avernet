function parseTime(value: string | number | null): Date | null {
  if (value == null) return null
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isNaN(numeric) && numeric > 0 && numeric < 1e12) {
    return new Date(numeric * 1000)
  }
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatTime(value: string | number | null): string {
  const d = parseTime(value)
  return d ? d.toLocaleString() : '—'
}

export function formatTimeShort(value: string | number | null): string {
  const d = parseTime(value)
  if (!d) return '—'
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatDuration(ms: number | null): string {
  if (ms === null || ms === 0) return '—'
  if (ms < 1000) return `${ms}ms`

  const totalSeconds = Math.floor(ms / 1000)
  if (totalSeconds < 60) return `${totalSeconds}s`

  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  const parts: string[] = []
  if (days > 0) parts.push(`${days}d`)
  if (hours > 0) parts.push(`${hours}h`)

  if (days > 0) {
    if (parts.length === 1 && hours === 0) parts.push('0h')
  } else if (hours > 0) {
    parts.push(`${minutes}m`)
  } else {
    parts.push(`${minutes}m`)
    if (seconds > 0) parts.push(`${seconds}s`)
  }

  return parts.join(' ')
}
