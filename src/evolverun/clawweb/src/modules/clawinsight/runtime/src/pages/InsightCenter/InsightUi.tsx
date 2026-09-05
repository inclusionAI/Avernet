import type { ReactNode } from 'react'

type IconName = 'chart' | 'warning' | 'clipboard' | 'bot' | 'users' | 'arrow' | 'close' | 'refresh' | 'check' | 'clock' | 'message' | 'code' | 'judge' | 'plus' | 'external' | 'edit' | 'search' | 'database'

export function InsightIcon({ name, className = 'h-4 w-4' }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    chart: <><path d="M4 19V9M10 19V5M16 19v-7M22 19H2" /><path d="m4 8 5-3 6 4 5-5" /></>,
    warning: <><path d="M12 3 2.8 19h18.4L12 3Z" /><path d="M12 9v4M12 17h.01" /></>,
    clipboard: <><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4.5V3h6v1.5M8 10h8M8 14h8M8 18h5" /></>,
    bot: <><rect x="4" y="7" width="16" height="12" rx="3" /><path d="M9 12h.01M15 12h.01M8 16h8M12 3v4" /></>,
    users: <><path d="M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 20v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
    arrow: <path d="m9 18 6-6-6-6" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    refresh: <><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 5v6h-6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    message: <path d="M4 5h16v11H8l-4 4V5Z" />,
    code: <><path d="m8 9-3 3 3 3M16 9l3 3-3 3M14 5l-4 14" /></>,
    judge: <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    external: <><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 13v6H5V6h6" /></>,
    edit: <><path d="m4 20 4.5-1 10-10-3.5-3.5-10 10L4 20Z" /><path d="m13.5 6.5 3.5 3.5" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>,
  }
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>
}

export function LoadingPanel({ text = '正在加载…' }: { text?: string }) {
  return (
    <div className="flex min-h-48 items-center justify-center text-sm text-gray-400">
      <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-blue-100 border-t-blue-600" />{text}
    </div>
  )
}

export function ErrorPanel({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center px-6 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-red-50 text-red-600"><InsightIcon name="warning" /></span>
      <p className="mt-3 max-w-xl text-sm text-red-700">{message}</p>
      {onRetry && <button onClick={onRetry} className="mt-4 rounded-lg border border-red-200 bg-white px-3 py-2 text-xs font-medium text-red-700 hover:bg-red-50">重新加载</button>}
    </div>
  )
}

export function EmptyPanel({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex min-h-52 flex-col items-center justify-center px-6 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-gray-100 text-gray-500"><InsightIcon name="clipboard" /></span>
      <p className="mt-4 text-sm font-medium text-gray-700">{title}</p>
      {description && <p className="mt-1 max-w-lg text-xs leading-5 text-gray-400">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function FailureBadge({ value }: { value: string }) {
  const tone = value === 'TOOL_FAILURE'
    ? 'bg-orange-50 text-orange-700 ring-orange-600/10'
    : value === 'COMPLETED'
      ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/10'
      : 'bg-red-50 text-red-700 ring-red-600/10'
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ring-inset ${tone}`}>{value}</span>
}
