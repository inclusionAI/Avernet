import { Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { lazy, Suspense, useMemo } from 'react'

const WorkflowWorkspace = lazy(() => import('./pages/WorkflowWorkspace'))
const TCLog = lazy(() => import('./pages/TCLog'))

function PageSkeleton() {
  return (
    <div className="flex h-[60vh] items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-200 border-t-blue-600" />
    </div>
  )
}

function Layout() {
  const location = useLocation()

  const navItems = useMemo(
    () => [
      { to: '/workflows/workspace', label: '任务护航' },
      { to: '/tclog', label: 'TcLog' },
    ],
    [],
  )

  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      <nav className="border-b border-gray-200 bg-white shadow-sm">
        <div className="mx-auto flex max-w-screen-2xl items-center px-4 sm:px-6 lg:px-8">
          <NavLink to="/" end className="flex shrink-0 items-center gap-2 py-3">
            <svg className="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <defs>
                <linearGradient id="logoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#6366f1" />
                  <stop offset="100%" stopColor="#06b6d4" />
                </linearGradient>
              </defs>
              <path d="M12 2L2 7l10 5 10-5-10-5z" fill="url(#logoGrad)" opacity="0.15" stroke="url(#logoGrad)" />
              <path d="M2 17l10 5 10-5" stroke="url(#logoGrad)" />
              <path d="M2 12l10 5 10-5" stroke="url(#logoGrad)" />
            </svg>
            <span className="text-lg font-bold bg-gradient-to-r from-indigo-600 to-cyan-500 bg-clip-text text-transparent">Evolvetrace</span>
          </NavLink>
          <div className="flex min-w-0 flex-1 items-center gap-x-0.5 overflow-x-auto whitespace-nowrap px-4">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `border-b-2 px-3 py-3 text-sm font-medium transition-colors ${
                    isActive
                      ? 'border-emerald-600 text-emerald-600'
                      : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </div>
          <div className="flex shrink-0 items-center pl-6 text-xs text-gray-500">
            {location.pathname}
          </div>
        </div>
      </nav>
      <main className="flex-1">
        <Routes>
          <Route path="/workflows/workspace" element={<Suspense fallback={<PageSkeleton />}><WorkflowWorkspace /></Suspense>} />
          <Route path="/tclog" element={<Suspense fallback={<PageSkeleton />}><TCLog /></Suspense>} />
          <Route path="/" element={<WorkflowWorkspace />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return <Layout />
}
