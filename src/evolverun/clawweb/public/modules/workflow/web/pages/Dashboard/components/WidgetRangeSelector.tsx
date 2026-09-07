import type { WidgetRange } from './widget-range'

export function WidgetRangeSelector({
  ariaLabel,
  value,
  globalLabel,
  onChange,
}: {
  ariaLabel: string
  value: WidgetRange
  globalLabel: string
  onChange: (value: WidgetRange) => void
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(event) => onChange(event.target.value as WidgetRange)}
      className="h-7 rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-600 outline-none transition focus:border-blue-400"
    >
      <option value="global">跟随全局（{globalLabel}）</option>
      <option value="7d">近 7 天</option>
      <option value="30d">近 30 天</option>
    </select>
  )
}
