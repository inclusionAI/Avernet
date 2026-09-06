interface KpiCardProps {
  icon?: string
  label: string
  value: number | string
  color: 'blue' | 'indigo' | 'emerald' | 'amber' | 'violet' | 'rose'
  delta?: number | null
  pulse?: boolean
  sub?: string
  onClick?: () => void
}

const COLOR_MAP = {
  blue: { dot: 'bg-blue-500', value: 'text-slate-950' },
  indigo: { dot: 'bg-indigo-500', value: 'text-slate-950' },
  emerald: { dot: 'bg-emerald-500', value: 'text-emerald-700' },
  amber: { dot: 'bg-amber-500', value: 'text-amber-700' },
  violet: { dot: 'bg-violet-500', value: 'text-slate-950' },
  rose: { dot: 'bg-rose-500', value: 'text-rose-700' },
}

export function KpiCard({ icon, label, value, color, delta, pulse, sub, onClick }: KpiCardProps) {
  const c = COLOR_MAP[color]
  const isClickable = !!onClick

  return (
    <div
      role={isClickable ? 'button' : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onClick={onClick}
      onKeyDown={isClickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') onClick() } : undefined}
      className={`group relative overflow-hidden rounded-xl border border-slate-200 bg-white p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all
        ${isClickable ? 'cursor-pointer hover:border-slate-300 hover:shadow-[0_8px_24px_rgba(15,23,42,0.07)]' : ''}
        ${pulse ? 'ring-2 ring-amber-300' : ''}
      `}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${c.dot}`} aria-hidden="true" />
          {icon && <span className="text-sm text-slate-400" aria-hidden="true">{icon}</span>}
        </div>
        {delta !== null && delta !== undefined && (
          <span className={`text-xs font-medium ${delta >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
            {delta >= 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(1)}pp
          </span>
        )}
      </div>
      <div className={`mt-3 text-2xl font-semibold tabular-nums tracking-tight ${c.value}`}>
        {value}
      </div>
      <div className="mt-1 text-xs font-medium text-slate-600">
        {label}
      </div>
      {sub && (
        <div className="mt-1 min-h-4 text-[11px] leading-4 text-slate-400">{sub}</div>
      )}
      {isClickable && (
        <div className="absolute bottom-2 right-3 text-[10px] font-medium text-slate-400 opacity-0 transition-opacity group-hover:opacity-100">
          查看明细
        </div>
      )}
    </div>
  )
}
