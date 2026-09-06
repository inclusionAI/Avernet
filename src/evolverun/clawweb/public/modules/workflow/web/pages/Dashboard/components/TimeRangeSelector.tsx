import type { TimeRangeKey } from '../../../types/dashboard'

const OPTIONS: { key: TimeRangeKey; label: string }[] = [
  { key: 'today', label: '今天' },
  { key: '7d', label: '7天' },
  { key: '30d', label: '30天' },
]

interface TimeRangeSelectorProps {
  value: TimeRangeKey
  onChange: (v: TimeRangeKey) => void
}

export function TimeRangeSelector({ value, onChange }: TimeRangeSelectorProps) {
  return (
    <div className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white p-0.5 shadow-sm">
      {OPTIONS.map((opt) => (
        <button
          key={opt.key}
          onClick={() => onChange(opt.key)}
          className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
            value === opt.key
              ? 'bg-blue-600 text-white shadow-sm'
              : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}