import { useEffect, useRef, type ReactNode } from 'react'
import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'

const buttonBase = 'inline-flex items-center justify-center rounded-lg border px-3 py-2 text-xs font-medium enabled:cursor-pointer disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring'
export const button = `${buttonBase} border-input bg-card text-foreground enabled:hover:bg-muted/50 disabled:bg-muted disabled:text-muted-foreground`
export const primary = `${buttonBase} border-primary bg-primary text-primary-foreground enabled:hover:bg-primary/90 disabled:border-input disabled:bg-muted disabled:text-muted-foreground`
export const states: Record<RepairInboxItem['state'], string> = {
  pending: '待处理', processing: '处理中', awaiting_verification: '待验证', verified: '已验证', ineffective: '未达预期', no_action: '暂不处理',
}
export const phases: Record<string, string> = { drafting: '生成中', review: '待审阅', blocked: '检查阻塞', publishing: '发布中', published: '已发布', no_change: '无需变更', failed: '失败', cancelled: '已取消' }
export const itemTitle = (item: RepairInboxItem) => item.aggregation?.title || (typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction || item.itemId)
export const itemHeading = (item: RepairInboxItem) => {
  const title = itemTitle(item).replace(/\b(?:pending\s+)?suggestion(?:Id)?[\s:#_-]*\d+\b/gi, '关联建议').split('\n')[0].trim()
  return title.length > 96 ? title.slice(0, 96) + '…' : title
}
export const isLegacyPreview = (item: RepairInboxItem) => !item.sourceAvailable && item.updatedAtMs === 0
export const previouslyHandled = (item: RepairInboxItem) => ['awaiting_verification', 'verified', 'ineffective'].includes(item.state)
export function exclusion(item: RepairInboxItem, taskId?: string) {
  if (isLegacyPreview(item)) return '历史预览：原始建议或证据已不可用，无法重新生成；请查看历史记录'
  if (!item.sourceAvailable) return '原始来源已不可用，无法核对原建议与证据'
  if (item.state === 'no_action') return '已暂不处理，可恢复后重新选择'
  if (item.state === 'processing') return taskId && item.activeTaskId === taskId ? null : '已有任务正在处理，请查看该任务的生成进度'
  if (previouslyHandled(item)) return null
  if (item.state !== 'pending') return '当前处理状态不允许生成，请查看任务历史'
  return null
}
export function JsonDetails({ title, value }: { title: string; value: unknown }) {
  if (value == null) return null
  return <details className="mt-2 text-xs"><summary className="cursor-pointer text-muted-foreground">{title}</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/50 p-3 text-[11px]">{JSON.stringify(value, null, 2)}</pre></details>
}
export function RepairDialog({ title, children, onClose, busy = false, footer }: { title: string; children: ReactNode; onClose: () => void; busy?: boolean; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    if (ref.current?.showModal) ref.current.showModal()
    else ref.current?.setAttribute('open', '')
    return () => { previous?.focus() }
  }, [])
  return <dialog ref={ref} aria-label={title} onCancel={event => { event.preventDefault(); if (!busy) onClose() }}
    onKeyDown={event => {
      if (!footer || event.key !== 'Tab') return
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], summary, [tabindex]'))
        .filter(element => element.tabIndex >= 0 && !element.matches(':disabled, [hidden]'))
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }}
    className={`fixed inset-0 z-50 m-auto w-[min(42rem,calc(100%-2rem))] rounded-2xl border border-input bg-card text-foreground shadow-2xl backdrop:bg-foreground/35 ${footer ? 'max-h-[90dvh] overflow-hidden p-0 open:flex open:flex-col' : 'max-h-[90vh] overflow-auto p-5'}`}>
    <div className={`flex items-center justify-between gap-3 ${footer ? 'shrink-0 border-b border-input p-5' : 'mb-4'}`}><h3 className="text-base font-semibold">{title}</h3><button type="button" className={button} disabled={busy} onClick={onClose}>关闭</button></div>
    {footer ? <><div className="min-h-0 overflow-y-auto p-5">{children}</div><footer className="shrink-0 border-t border-input bg-card px-5 py-3">{footer}</footer></> : children}
  </dialog>
}
