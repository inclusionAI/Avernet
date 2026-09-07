import type { ReactNode } from 'react'

export type WorkspaceView = 'dashboard' | 'overview' | 'diagnosis' | 'remedies' | 'editor' | 'management'

type WorkspaceIcon = 'dashboard' | 'overview' | 'diagnosis' | 'remedies' | 'editor' | 'management'
type NavItem = { key: WorkspaceView; label: string; icon: WorkspaceIcon; count?: number }

interface SidebarProps {
  activeView: WorkspaceView
  onViewChange: (view: WorkspaceView) => void
  isAdmin: boolean
  hasWorkflow: boolean
  counts?: { diagnosis?: number; remedies?: number }
}

function NavIcon({ name }: { name: WorkspaceIcon }) {
  const paths: Record<WorkspaceIcon, ReactNode> = {
    dashboard: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    overview: <><path d="M4 17V7" /><path d="M4 17h16" /><path d="m7 13 3-3 3 2 5-6" /><path d="M15 6h3v3" /></>,
    diagnosis: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6" /><path d="M12 17h.01" /></>,
    remedies: <><path d="m12 3 8 4.5-8 4.5-8-4.5L12 3Z" /><path d="m4 12 8 4.5 8-4.5" /><path d="m4 16.5 8 4.5 8-4.5" /></>,
    editor: <><path d="m8 9-3 3 3 3" /><path d="m16 9 3 3-3 3" /><path d="m14 5-4 14" /></>,
    management: <><path d="M4 7h10" /><path d="M18 7h2" /><circle cx="16" cy="7" r="2" /><path d="M4 17h2" /><path d="M10 17h10" /><circle cx="8" cy="17" r="2" /></>,
  }
  return <svg aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">{paths[name]}</svg>
}

function NavButton({ item, active, onClick }: { item: NavItem; active: boolean; onClick: () => void }) {
  const accessibleName = item.count == null ? item.label : `${item.label} ${item.count}`
  return <button type="button" onClick={onClick} aria-label={accessibleName} aria-current={active ? 'page' : undefined} className={`group relative flex w-full items-center gap-3 overflow-hidden rounded-lg px-3 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/30 ${active ? 'bg-blue-50 font-semibold text-blue-700' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-950'}`}>
    {active && <span aria-hidden="true" className="absolute inset-y-2 left-0 w-0.5 rounded-r-full bg-blue-600" />}
    <span className={`flex h-7 w-7 items-center justify-center rounded-lg transition-colors ${active ? 'bg-blue-100 text-blue-700' : 'text-slate-400 group-hover:bg-white group-hover:text-slate-600'}`}><NavIcon name={item.icon} /></span>
    <span className="min-w-0 flex-1 truncate">{item.label}</span>
    {item.count != null && <span className={`min-w-5 rounded-full px-1.5 py-0.5 text-center text-[10px] font-semibold tabular-nums ${active ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-500'}`}>{item.count}</span>}
  </button>
}

export default function Sidebar({ activeView, onViewChange, isAdmin, hasWorkflow, counts }: SidebarProps) {
  const analysisItems: NavItem[] = [
    { key: 'overview', label: '运行概览', icon: 'overview' },
    { key: 'diagnosis', label: '问题与优化', icon: 'diagnosis', count: counts?.diagnosis },
    { key: 'remedies', label: '可复用经验', icon: 'remedies', count: counts?.remedies },
  ]
  const configItems: NavItem[] = [
    { key: 'editor', label: '编辑器', icon: 'editor' },
    { key: 'management', label: '管理设置', icon: 'management' },
  ]

  return <aside className="flex h-full w-[264px] shrink-0 flex-col border-r border-slate-200 bg-white" aria-label="任务护航导航">
    <div className="border-b border-slate-200 px-4 py-4">
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-950 text-base text-white shadow-sm">✦</span>
        <div><h2 className="text-sm font-semibold tracking-tight text-slate-950">任务护航</h2><p className="mt-0.5 text-[10px] uppercase tracking-[0.16em] text-slate-400">TASK GUARD</p></div>
      </div>
    </div>
    <div className="relative min-h-0 flex-1 overflow-y-auto px-3 py-4">
      {isAdmin && <div className="mb-5"><p className="mb-1.5 px-3 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400">全局</p><NavButton item={{ key: 'dashboard', label: '数据大盘', icon: 'dashboard' }} active={activeView === 'dashboard'} onClick={() => onViewChange('dashboard')} /></div>}

      {hasWorkflow && <><div className="mb-5"><p className="mb-1.5 px-3 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400">分析与改进</p><div className="space-y-0.5">{analysisItems.map((item) => <NavButton key={item.key} item={item} active={activeView === item.key} onClick={() => onViewChange(item.key)} />)}</div></div><div><p className="mb-1.5 px-3 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400">工作流配置</p><div className="space-y-0.5">{configItems.map((item) => <NavButton key={item.key} item={item} active={activeView === item.key} onClick={() => onViewChange(item.key)} />)}</div></div></>}
    </div>
  </aside>
}
