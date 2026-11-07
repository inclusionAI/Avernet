import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const FOCUSABLE = 'a[href], area[href], button, input:not([type="hidden"]), select, textarea, iframe, summary, audio[controls], video[controls], [contenteditable="true"], [tabindex]'

function isFocusable(element: HTMLElement): boolean {
  if (element.tabIndex < 0 || element.matches(':disabled') || element.closest('[hidden], [inert], [aria-hidden="true"]')) return false
  const style = getComputedStyle(element)
  if (style.visibility === 'hidden' || style.display === 'none' || element.getClientRects().length === 0) return false
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    if (parent instanceof HTMLDetailsElement && !parent.open) {
      const summary = Array.from(parent.children).find(child => child.tagName === 'SUMMARY')
      if (!summary?.contains(element) && element !== summary) return false
    }
  }
  return true
}

/** The child has an independent query cache; closing it never resets the parent. */
export default function ChildRunDrawer({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const [client] = useState(() => new QueryClient())
  const panel = useRef<HTMLDivElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeButton.current?.focus()
    return () => { void client.cancelQueries(); client.clear(); previous?.focus() }
  }, [client])

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') { event.stopPropagation(); onClose(); return }
    if (event.key !== 'Tab') return
    const elements = Array.from(panel.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(isFocusable)
      .sort((a, b) => (a.tabIndex > 0 ? a.tabIndex : Infinity) - (b.tabIndex > 0 ? b.tabIndex : Infinity))
    const first = elements[0], last = elements[elements.length - 1]
    if (!first || !last) { event.preventDefault(); return }
    if (!elements.includes(document.activeElement as HTMLElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus() }
    else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    event.stopPropagation()
  }

  return <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
    <div ref={panel} role="dialog" aria-modal="true" aria-label="子运行详情" onKeyDown={onKeyDown} onClick={event => event.stopPropagation()} className="h-full w-full max-w-5xl overflow-y-auto bg-background shadow-xl">
      <div className="sticky top-0 z-10 flex justify-end border-b border-border bg-background px-4 py-2">
        <button ref={closeButton} type="button" onClick={onClose} className="rounded px-3 py-2 text-sm text-primary">关闭子运行详情</button>
      </div>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </div>
  </div>
}
