import { useEffect, useRef, type ReactNode } from 'react'
import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'

export const button = 'rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-45 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600'
export const primary = `${button} border-blue-600 bg-blue-600 text-white hover:bg-blue-700`
export const states: Record<RepairInboxItem['state'], string> = {
  pending: '待处理', processing: '处理中', awaiting_verification: '待验证', verified: '已验证', ineffective: '未达预期', no_action: '暂不处理',
}
export const phases: Record<string, string> = { drafting: '生成中', review: '待审阅', blocked: '检查阻塞', publishing: '发布中', published: '已发布', no_change: '无需变更', failed: '失败', cancelled: '已取消' }
export const itemTitle = (item: RepairInboxItem) => typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction || item.itemId
export const isLegacyPreview = (item: RepairInboxItem) => !item.sourceAvailable && item.updatedAtMs === 0
export function exclusion(item: RepairInboxItem, taskId?: string) {
  if (isLegacyPreview(item)) return '历史预览，请在原处理流程查看'
  if (!item.sourceAvailable) return '原始来源已不可用'
  if (item.state === 'no_action') return '已暂不处理，可恢复后重新选择'
  if (item.state === 'processing') return taskId && item.activeTaskId === taskId ? null : '已由修复任务占用'
  if (item.state !== 'pending') return '当前状态不参与生成'
  return null
}
export function JsonDetails({ title, value }: { title: string; value: unknown }) {
  if (value == null) return null
  return <details className="mt-2 text-xs"><summary className="cursor-pointer text-slate-500">{title}</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-50 p-3 text-[11px]">{JSON.stringify(value, null, 2)}</pre></details>
}
export function RepairDialog({ title, children, onClose, busy = false }: { title: string; children: ReactNode; onClose: () => void; busy?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    if (ref.current?.showModal) ref.current.showModal()
    else ref.current?.setAttribute('open', '')
    return () => { previous?.focus() }
  }, [])
  return <dialog ref={ref} aria-label={title} onCancel={event => { event.preventDefault(); if (!busy) onClose() }}
    className="fixed inset-0 z-50 m-auto max-h-[90vh] w-[min(42rem,calc(100%-2rem))] overflow-auto rounded-2xl border border-slate-200 bg-white p-5 text-slate-800 shadow-2xl backdrop:bg-slate-950/35">
    <div className="mb-4 flex items-center justify-between gap-3"><h3 className="text-base font-semibold">{title}</h3><button type="button" className={button} disabled={busy} onClick={onClose}>关闭</button></div>
    {children}
  </dialog>
}
