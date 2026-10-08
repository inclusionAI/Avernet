import { useEffect, useRef, type ReactNode } from 'react'

/** One scroll surface and native modal focus handling for both issue and suggestion details. */
export default function DetailDrawer({ title, header, navigation, footer, children, onClose }: {
  title: string; header: ReactNode; navigation?: ReactNode; footer?: ReactNode; children: ReactNode; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    if (ref.current?.showModal) ref.current.showModal()
    else ref.current?.setAttribute('open', '')
    return () => { if (previous?.isConnected) previous.focus() }
  }, [])
  return <dialog ref={ref} aria-label={title} onCancel={event => { event.preventDefault(); onClose() }}
    className="fixed inset-y-0 left-auto right-0 z-40 m-0 h-dvh max-h-none w-full max-w-[640px] border-0 border-l border-slate-200 bg-white p-0 shadow-2xl backdrop:bg-slate-950/20">
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-start gap-4 border-b border-slate-200 px-5 py-4">
        <div className="min-w-0 flex-1">{header}</div>
        <button type="button" aria-label="关闭" onClick={onClose} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-lg text-slate-500 hover:bg-slate-100">×</button>
      </header>
      {navigation}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">{children}</div>
      {footer}
    </div>
  </dialog>
}
