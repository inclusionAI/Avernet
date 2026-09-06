import { useEffect, useRef, useState } from 'react'
import { evolveBotOptionKey, type EvolveBotPickerOption } from './evolveBotIdentity'

function providerLabel(bot: EvolveBotPickerOption): string {
  if (bot.deviceProvider?.toLowerCase() === 'baas') return 'BaaS · 推荐'
  if (bot.deviceProvider?.toLowerCase() === 'arca') return 'ARCA · 不推荐'
  return '平台未知'
}

function providerBadgeClass(bot: EvolveBotPickerOption): string {
  if (bot.deviceProvider?.toLowerCase() === 'arca') return 'bg-red-50 text-red-700'
  if (bot.deviceProvider?.toLowerCase() === 'baas') return 'bg-blue-50 text-blue-700'
  return 'bg-gray-100 text-gray-600'
}

export default function EvolveBotPicker({
  bots,
  value,
  onChange,
  disabled = false,
  emptyText = '当前没有可用 Bot',
  ariaLabel = '选择 Bot',
  emptyOption,
  onClear,
  disableUnsupported = true,
  compact = false,
}: {
  bots: EvolveBotPickerOption[]
  value: string
  onChange: (key: string, bot: EvolveBotPickerOption) => void
  disabled?: boolean
  emptyText?: string
  ariaLabel?: string
  emptyOption?: { label: string; description?: string }
  onClear?: () => void
  disableUnsupported?: boolean
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const clearRef = useRef<HTMLButtonElement>(null)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const selected = bots.find((bot) => evolveBotOptionKey(bot) === value)
  useEffect(() => {
    if (!open) return
    const selectable = (bot: EvolveBotPickerOption) => !disableUnsupported || !bot.activeEngine || bot.activeEngine.toLowerCase() === 'openclaw'
    const selectedIndex = bots.findIndex((bot) => evolveBotOptionKey(bot) === value && selectable(bot))
    const focusIndex = selectedIndex >= 0 ? selectedIndex : Math.max(0, bots.findIndex(selectable))
    queueMicrotask(() => value === '' && emptyOption ? clearRef.current?.focus() : optionRefs.current[focusIndex]?.focus())
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [bots, disableUnsupported, emptyOption, open, value])
  if (bots.length === 0 && !emptyOption) {
    return <div className="rounded-xl border border-dashed border-gray-200 px-4 py-6 text-center text-xs text-gray-400">{emptyText}</div>
  }
  return (
    <div ref={rootRef} className="relative">
      <button type="button" disabled={disabled} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((current) => !current)} className={`flex w-full items-center justify-between gap-3 border border-gray-200 bg-white text-left shadow-sm transition hover:border-blue-300 disabled:cursor-not-allowed disabled:opacity-50 ${compact ? 'rounded-lg px-3 py-2' : 'rounded-xl px-4 py-3'}`}>
        <span className="min-w-0"><span className="block truncate text-sm font-medium text-gray-900">{selected?.botName || selected?.botId || emptyOption?.label || '请选择 Bot'}</span>{!compact && <span className="mt-1 block truncate font-mono text-[10px] text-gray-500">{selected ? `${selected.ownerId ? `${selected.ownerId} / ` : ''}${selected.botId} / ${selected.env || '环境未知'}` : emptyOption?.description || '展开查看可用 Bot'}</span>}</span>
        <span aria-hidden="true" className={`text-xs text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`}>⌄</span>
      </button>
      {open && <div role="radiogroup" aria-label={ariaLabel} className="absolute z-30 mt-2 max-h-72 w-full space-y-2 overflow-y-auto rounded-xl border border-gray-200 bg-gray-50 p-2 shadow-xl">
      {emptyOption && <button ref={clearRef} type="button" role="radio" aria-checked={value === ''} onClick={() => { onClear?.(); setOpen(false) }} onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); setOpen(false); rootRef.current?.querySelector<HTMLButtonElement>('[aria-haspopup="listbox"]')?.focus() } else if (event.key === 'ArrowDown') { event.preventDefault(); optionRefs.current.find(Boolean)?.focus() } }} className={`flex w-full items-start justify-between gap-3 rounded-lg border px-3 py-3 text-left transition ${value === '' ? 'border-blue-500 bg-blue-50 shadow-sm ring-1 ring-blue-500/10' : 'border-transparent bg-white hover:border-gray-300'}`}><span><span className="block text-sm font-medium text-gray-900">{emptyOption.label}</span>{emptyOption.description && <span className="mt-1 block text-[10px] text-gray-500">{emptyOption.description}</span>}</span><span aria-hidden="true" className={`mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${value === '' ? 'border-blue-600 bg-blue-600' : 'border-gray-300 bg-white'}`}>{value === '' && <span className="h-1.5 w-1.5 rounded-full bg-white" />}</span></button>}
      {bots.map((bot, index) => {
        const key = evolveBotOptionKey(bot)
        const isSelected = key === value
        const unsupported = Boolean(disableUnsupported && bot.activeEngine && bot.activeEngine.toLowerCase() !== 'openclaw')
        return (
          <button
            key={key}
            ref={(node) => { optionRefs.current[index] = node }}
            type="button"
            role="radio"
            aria-checked={isSelected}
            disabled={disabled || unsupported}
            onClick={() => { onChange(key, bot); setOpen(false) }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') { event.preventDefault(); setOpen(false); rootRef.current?.querySelector<HTMLButtonElement>('[aria-haspopup="listbox"]')?.focus(); return }
              if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
              event.preventDefault()
              const offset = event.key === 'ArrowDown' ? 1 : -1
              for (let step = 1; step <= bots.length; step += 1) {
                const nextIndex = (index + offset * step + bots.length) % bots.length
                if (!disableUnsupported || !bots[nextIndex].activeEngine || bots[nextIndex].activeEngine?.toLowerCase() === 'openclaw') {
                  optionRefs.current[nextIndex]?.focus()
                  break
                }
              }
            }}
            className={`flex w-full items-start justify-between gap-3 rounded-lg border px-3 py-3 text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${isSelected ? 'border-blue-500 bg-blue-50 shadow-sm ring-1 ring-blue-500/10' : 'border-transparent bg-white hover:border-gray-300'}`}
          >
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-gray-900">{bot.botName || bot.botId}</span>
              <span className="mt-1 block break-all font-mono text-[10px] text-gray-500">{bot.ownerId ? `${bot.ownerId} / ` : ''}{bot.botId} / {bot.env || '环境未知'}</span>
              <span className="mt-2 flex flex-wrap gap-1.5">
                <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${providerBadgeClass(bot)}`}>{providerLabel(bot)}</span>
                <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[9px] font-medium text-emerald-700">{bot.activeEngine || '引擎未知'}</span>
                <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[9px] font-medium text-gray-600">{bot.hasServiceBot ? '已有服务 Bot' : bot.botType === 'service' ? '服务型 Bot' : '普通 Bot'}</span>
                {bot.accessType === 'collaborator' && <span className="rounded bg-violet-50 px-1.5 py-0.5 text-[9px] font-medium text-violet-700">协作 Bot</span>}
                {unsupported && <span className="rounded bg-red-50 px-1.5 py-0.5 text-[9px] font-medium text-red-700">当前进化仅支持 OpenClaw</span>}
              </span>
            </span>
            <span aria-hidden="true" className={`mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${isSelected ? 'border-blue-600 bg-blue-600 text-white' : 'border-gray-300 bg-white'}`}>
              {isSelected && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
            </span>
          </button>
        )
      })}
      </div>}
    </div>
  )
}
