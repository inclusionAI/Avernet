/** Preview-only snapshot of the existing OCB Layout navigation (2026-09-21).
 * Production owns its header; do not import this fixture into product code.
 * Non-monitoring destinations intentionally lead to the preview-only notice.
 */
import { NavLink } from 'react-router-dom';
import UserMenu from './UserMenu';
export function HostHeader() {
  return (<nav className="border-b border-gray-200 bg-white shadow-sm">
        <div className="mx-auto flex max-w-screen-2xl items-center px-4 sm:px-6 lg:px-8">
          {/* Left: brand logo */}
          <NavLink to="/" end className="flex shrink-0 items-center gap-2 border-b-2 border-transparent py-3">
            <svg className="h-8 w-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <defs>
                <linearGradient id="logoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#6366f1" />
                  <stop offset="100%" stopColor="#06b6d4" />
                </linearGradient>
              </defs>
              <path d="M12 2L2 7l10 5 10-5-10-5z" fill="url(#logoGrad)" opacity="0.15" stroke="url(#logoGrad)" />
              <path d="M2 17l10 5 10-5" stroke="url(#logoGrad)" />
              <path d="M2 12l10 5 10-5" stroke="url(#logoGrad)" />
              <circle cx="12" cy="2" r="1.5" fill="#6366f1" />
              <path d="M12 7v5l3 2" stroke="#6366f1" strokeWidth="2" />
            </svg>
            <span className="text-lg font-bold bg-gradient-to-r from-indigo-600 to-cyan-500 bg-clip-text text-transparent">AgentEvolve</span>
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">(内部版)</span>
          </NavLink>
          {/* Center: nav links */}
          <div className="no-scrollbar flex min-w-0 flex-1 items-center gap-x-0.5 overflow-x-auto whitespace-nowrap">
          <NavLink
            to="/workflows/workspace"
            className={({ isActive }) =>
              `border-b-2 px-2 py-3 text-sm font-medium transition-colors ${
                isActive
                  ? 'border-emerald-600 text-emerald-600'
                  : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
              }`
            }
          >
            任务护航
          </NavLink>
          <NavLink
            to="/insight"
            className={({ isActive }) =>
              `border-b-2 px-2 py-3 text-sm font-medium transition-colors ${
                isActive
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
              }`
            }
          >
            效果中心
          </NavLink>
          <NavLink
            to="/evolve"
            className={({ isActive }) =>
              `border-b-2 px-2 py-3 text-sm font-medium transition-colors ${
                isActive
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
              }`
            }
          >
            Claw进化
          </NavLink>
          <NavLink
            to="/other"
            className={({ isActive }) =>
              `border-b-2 px-2 py-3 text-sm font-medium transition-colors ${
                isActive
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
              }`
            }
          >
            其他功能
          </NavLink>
          </div>
          {/* Right: admin-only links + user info */}
          <div className="flex shrink-0 items-center ml-auto pl-6">
            <UserMenu />
          </div>
        </div>
      </nav>);
}
