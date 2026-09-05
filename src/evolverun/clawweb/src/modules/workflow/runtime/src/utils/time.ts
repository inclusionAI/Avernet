/**
 * Parse a time value that may be a Unix timestamp (seconds), ISO string, or null
 * into a Date object. Returns null for unparseable values.
 */
function parseTime(value: string | number | null): Date | null {
  if (value == null) return null
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isNaN(numeric) && numeric > 0 && numeric < 1e12) {
    return new Date(numeric * 1000)
  }
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d
}

/**
 * Format a time value as a full locale string.
 */
export function formatTime(value: string | number | null): string {
  const d = parseTime(value)
  return d ? d.toLocaleString() : '—'
}

/**
 * Format a time value as a concise locale string (month day, time).
 */
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

/**
 * Format a duration in milliseconds as a human-readable string.
 * Supports hours, minutes, and seconds. Returns '—' for null/zero.
 *
 * Examples:
 *   500        → '500ms'
 *   3000       → '3s'
 *   65000      → '1m 5s'
 *   3661000    → '1h 1m 1s'
 *   86400000   → '1d 0h'
 */
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
    // When showing days, include hours but skip minutes/seconds
    if (parts.length === 1 && hours === 0) parts.push('0h')
  } else if (hours > 0) {
    // When showing hours (no days), include minutes
    parts.push(`${minutes}m`)
  } else {
    // Only minutes and seconds
    parts.push(`${minutes}m`)
    if (seconds > 0) parts.push(`${seconds}s`)
  }

  return parts.join(' ')
}