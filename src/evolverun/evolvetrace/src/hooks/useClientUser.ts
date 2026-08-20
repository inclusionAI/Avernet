// 简化版用户身份桩：直接返回 req.userId / req.isAdmin（由服务端模板注入）

import type { ClientUser } from '../types'

export interface UserReq {
  userId?: string
  isAdmin?: boolean
}

function getInjectedReq(): UserReq {
  if (typeof window === 'undefined') return {}
  return ((window as unknown as { __REQ__?: UserReq }).__REQ__) ?? {}
}

export function getClientUser(): ClientUser | null {
  const req = getInjectedReq()
  if (!req.userId) return null
  return {
    userId: req.userId,
    isAdmin: req.isAdmin === true,
    isLogAdmin: req.isAdmin === true,
  }
}

export function useClientUser() {
  return { user: getClientUser(), authState: 'ready' as const }
}
