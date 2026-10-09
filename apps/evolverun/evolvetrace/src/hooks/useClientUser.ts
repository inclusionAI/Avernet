// 简化版用户身份桩：直接返回 req.userId / req.isAdmin（由服务端模板注入）

import type { ClientUser } from '../types'

export interface UserReq {
  userId?: string
  isAdmin?: boolean
  isLogAdmin?: boolean
}

function getInjectedReq(): UserReq {
  if (typeof window === 'undefined') return {}
  return ((window as unknown as { __REQ__?: UserReq }).__REQ__) ?? {}
}

export function getClientUser(): ClientUser | null {
  const req = getInjectedReq()
  // Dev fallback: treat anonymous local requests as the default dev admin.
  // In production the server must inject a real user via window.__REQ__.
  if (import.meta.env.DEV && !req.userId) {
    return {
      userId: 'dev_local',
      isAdmin: true,
      isLogAdmin: true,
    }
  }
  if (!req.userId) {
    return null
  }
  return {
    userId: req.userId,
    isAdmin: req.isAdmin === true,
    isLogAdmin: req.isLogAdmin === true,
  }
}

export function useClientUser() {
  return { user: getClientUser(), authState: 'ready' as const }
}
