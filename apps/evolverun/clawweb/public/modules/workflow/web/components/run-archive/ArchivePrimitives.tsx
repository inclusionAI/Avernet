import type { ReactNode } from 'react'
import { prettyJson, statusClass, statusLabel } from './archive-helpers'

export const buttonClass = 'inline-flex min-w-0 items-center justify-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'
export const inputClass = 'h-9 min-w-0 max-w-full rounded-md border border-input bg-card px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary'

export function Badge({ children, className = 'bg-muted text-muted-foreground' }: {
  children: ReactNode
  className?: string
}) {
  return <span className={`inline-flex max-w-full items-center rounded-md px-2 py-1 text-xs font-medium [overflow-wrap:anywhere] ${className}`}>{children}</span>
}

export function StatusBadge({ status }: { status: unknown }) {
  return <Badge className={statusClass(status)}>{statusLabel(status)}</Badge>
}

export function ViewButton({ active, children, onClick, label }: {
  active: boolean
  children: ReactNode
  onClick: () => void
  label?: string
}) {
  return (
    <button type="button" aria-label={label} aria-current={active ? 'page' : undefined} onClick={onClick}
      className={`${buttonClass} ${active ? 'border-primary/20 bg-primary/10 text-primary hover:bg-primary/10' : ''}`}>
      {children}
    </button>
  )
}

export function ActionButton({ children, onClick, primary = false }: {
  children: ReactNode
  onClick: () => void
  primary?: boolean
}) {
  return <button type="button" onClick={onClick} className={`${buttonClass} ${primary ? 'border-primary bg-primary text-primary-foreground hover:bg-primary/90' : ''}`}>{children}</button>
}

export function EmptyState({ title, description, children }: {
  title: string
  description: string
  children?: ReactNode
}) {
  return (
    <div className="min-w-0 rounded-lg border border-dashed border-border bg-muted/30 px-5 py-8 text-center">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">{description}</p>
      {children && <div className="mt-4 flex flex-wrap justify-center gap-2">{children}</div>}
    </div>
  )
}

export function CodeBlock({ value, label, empty }: { value: unknown; label: string; empty?: string }) {
  return (
    <pre tabIndex={0} aria-label={label} className="max-h-96 min-w-0 max-w-full overflow-auto rounded-lg border border-border bg-muted/60 p-3 font-mono text-xs leading-6 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
      {prettyJson(value, empty)}
    </pre>
  )
}

export function Disclosure({ title, children, open = false }: {
  title: string
  children: ReactNode
  open?: boolean
}) {
  return (
    <details open={open || undefined} className="min-w-0 rounded-lg border border-border bg-card">
      <summary className="cursor-pointer rounded-lg px-3 py-2 text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">{title}</summary>
      <div className="min-w-0 border-t border-border p-3">{children}</div>
    </details>
  )
}

export function Definition({ label, children, mono = false }: {
  label: string
  children: ReactNode
  mono?: boolean
}) {
  return <div className="min-w-0"><dt className="text-xs text-muted-foreground">{label}</dt><dd className={`mt-1 text-sm text-foreground [overflow-wrap:anywhere] ${mono ? 'font-mono' : ''}`}>{children}</dd></div>
}
