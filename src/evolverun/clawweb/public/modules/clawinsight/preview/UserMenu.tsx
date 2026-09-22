/** Preview-only snapshot of the existing OCB user menu. Uses synthetic login data. */
import { useState, useRef, useEffect } from 'react'
import { leaveDevAuthMode, useClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'

export default function UserMenu() {
  const { user, authState } = useClientUser()
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open])

  if (authState === 'loading') {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5">
        <div className="h-8 w-8 animate-pulse rounded-full bg-gray-200" />
      </div>
    )
  }

  if (!user) {
    return (
      <div className="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm text-gray-400">
        <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" />
        </svg>
        <span>未登录</span>
      </div>
    )
  }

  const leaveDev = () => {
    leaveDevAuthMode()
    window.location.reload()
  }

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-md px-2 py-1 text-sm transition-colors hover:bg-gray-100"
      >
        {user.avatarUrl ? (
          <img
            src={user.avatarUrl}
            alt={user.nickName}
            className="h-8 w-8 rounded-full border border-gray-200 object-cover"
          />
        ) : (
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-600 text-xs font-medium text-white">
            {user.nickName.slice(0, 1)}
          </div>
        )}
        <span className="max-w-[120px] truncate font-medium text-gray-700">
          {user.nickName}
        </span>
        <svg
          className={`h-4 w-4 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-56 rounded-lg border border-gray-200 bg-white py-1 shadow-lg">
          <div className="border-b border-gray-100 px-4 py-2">
            <p className="text-xs font-medium text-gray-400">当前登录用户</p>
            <p className="mt-0.5 text-sm font-semibold text-gray-900">{user.nickName}</p>
          </div>
          <div className="px-4 py-2">
            <p className="text-xs text-gray-500">
              <span className="font-bold text-gray-400 uppercase tracking-wider">工号: </span>
              <span className="font-mono text-gray-900">{user.userId}</span>
            </p>
            <p className="mt-1 text-xs text-gray-500">
              <span className="font-bold text-gray-400 uppercase tracking-wider">账号: </span>
              <span className="font-mono text-gray-900">{user.userName}</span>
            </p>
          </div>
          {user.userId === 'dev_local' && (
            <div className="border-t border-gray-100 p-2">
              <button
                type="button"
                onClick={leaveDev}
                className="w-full rounded-md px-2 py-2 text-left text-xs font-medium text-blue-600 hover:bg-blue-50"
              >
                退出 Dev 模式，使用真实登录
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
