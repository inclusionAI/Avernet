const TIME_RANGE_OPTIONS = [
  { value: '', label: '全部时间' },
  { value: '1d', label: '最近1天' },
  { value: '3d', label: '最近3天' },
  { value: '7d', label: '最近7天' },
] as const

interface TimeRangeFilterProps {
  value: string
  onChange: (value: string) => void
}

export function toTimeRange(value: string): { from?: string; to?: string } {
  if (!value) return {}
  const now = new Date()
  const from = new Date()
  switch (value) {
    case '1d':
      from.setDate(now.getDate() - 1)
      break
    case '3d':
      from.setDate(now.getDate() - 3)
      break
    case '7d':
      from.setDate(now.getDate() - 7)
      break
    case '1h':
      from.setHours(now.getHours() - 1)
      break
    case '24h':
      from.setDate(now.getDate() - 1)
      break
    default:
      return {}
  }
  return { from: from.toISOString(), to: now.toISOString() }
}

export default function TimeRangeFilter({ value, onChange }: TimeRangeFilterProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
    >
      {TIME_RANGE_OPTIONS.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  )
}