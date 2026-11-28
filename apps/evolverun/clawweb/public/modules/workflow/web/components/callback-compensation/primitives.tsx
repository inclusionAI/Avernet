import { createContext, useContext, useEffect, useRef, type ReactNode, type ButtonHTMLAttributes,
  type InputHTMLAttributes, type TextareaHTMLAttributes, type HTMLAttributes, type SVGProps } from 'react'
import { createPortal } from 'react-dom'

/** Native HTML controls styled with the shared web theme; no cross-app UI dependency. */
export function Button({ variant = 'default', size = 'default', loading, className = '', children, disabled,
  ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string; size?: string; loading?: boolean }) {
  const tone = variant === 'outline' ? 'border border-input bg-background hover:bg-muted' :
    variant === 'ghost' ? 'hover:bg-muted text-muted-foreground' : 'bg-primary text-primary-foreground hover:opacity-90'
  return <button type="button" {...props} disabled={disabled || loading} aria-busy={loading || undefined}
    className={`inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50 ${size === 'sm' ? 'px-3 py-1.5 text-xs' : 'px-4 py-2 text-sm'} ${tone} ${className}`}>
    {loading && <span aria-hidden className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}
    {children}
  </button>
}
export function Badge({ tone = 'neutral', className = '', ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: string }) {
  const tones = { primary: 'bg-primary/10 text-primary', success: 'bg-success/10 text-success',
    warning: 'bg-warning/10 text-warning-foreground', error: 'bg-destructive/10 text-destructive', neutral: 'bg-muted text-muted-foreground' }
  return <span {...props} className={`inline-flex rounded-full px-2 py-1 text-[11px] font-medium ${tones[tone as keyof typeof tones] ?? tones.neutral} ${className}`} />
}
const field = 'w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground disabled:opacity-50'
export function Input(props: InputHTMLAttributes<HTMLInputElement>) { return <input {...props} className={`${field} ${props.className ?? ''}`} /> }
export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) { return <textarea rows={4} {...props} className={`${field} ${props.className ?? ''}`} /> }

const ModalContext = createContext({ open: false, onOpenChange: (_open: boolean) => {} })
export function Modal({ open, onOpenChange, children }: { open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  return <ModalContext.Provider value={{ open, onOpenChange }}>{children}</ModalContext.Provider>
}
const focusable = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary, [tabindex="0"]'
export function ModalContent({ children, closeLabel, className = '' }: { children: ReactNode; closeLabel: string; className?: string; size?: string }) {
  const { open, onOpenChange } = useContext(ModalContext)
  const panel = useRef<HTMLDivElement>(null), close = useRef<HTMLButtonElement>(null)
  const onClose = useRef(onOpenChange)
  onClose.current = onOpenChange
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    close.current?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose.current(false); return }
      if (event.key !== 'Tab') return
      const elements = [...panel.current?.querySelectorAll<HTMLElement>(focusable) ?? []].filter(element => {
        if (element.closest('[hidden], [inert], [aria-hidden="true"]')) return false
        const details = element.closest('details:not([open])')
        return !details || element.tagName === 'SUMMARY'
      })
      const first = elements[0], last = elements.at(-1)
      if (!first || !last) { event.preventDefault(); return }
      if (!elements.includes(document.activeElement as HTMLElement)) { event.preventDefault(); first.focus() }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keydown, true)
    return () => {
      document.body.style.overflow = overflow
      document.removeEventListener('keydown', keydown, true)
      if (previous?.isConnected) previous.focus()
    }
  }, [open])
  if (!open) return null
  return createPortal(<div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
    onPointerDown={event => { if (event.target === event.currentTarget) onOpenChange(false) }}>
    <div ref={panel} role="dialog" aria-modal="true" aria-label="成功回调补偿" className={`relative w-full border border-border bg-background text-foreground shadow-xl ${className}`}>
      <button ref={close} type="button" aria-label={closeLabel} onClick={() => onOpenChange(false)}
        className="absolute right-3 top-3 z-10 rounded-md px-2 py-1 text-muted-foreground hover:bg-muted">×</button>
      {children}
    </div>
  </div>, document.body)
}
export function ModalHeader(props: HTMLAttributes<HTMLDivElement>) { return <div {...props} /> }
export function ModalTitle(props: HTMLAttributes<HTMLHeadingElement>) { return <h2 {...props} /> }
export function ModalDescription(props: HTMLAttributes<HTMLParagraphElement>) { return <p {...props} /> }
export function BellRing(props: SVGProps<SVGSVGElement>) {
  return <svg {...props} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4M2 6l2-3m18 3-2-3" /></svg>
}
export function ShieldCheck(props: SVGProps<SVGSVGElement>) {
  return <svg {...props} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Zm-4 10 3 3 5-6" /></svg>
}
