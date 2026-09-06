import { useState, useEffect } from 'react'
import type { ClientUser } from '../types'

const TERN_AUTH_POLL_INTERVAL_MS = 100
const INTERNAL_DEV_LOGIN_HOST_PATTERN = /(^|\.)alipay\.net$/
const DEV_AUTH_MODE_KEY = 'clawweb_auth_mode'
let cachedServerUser: ClientUser | null = null
let globalAuthPromise: Promise<ClientUser | null> | null = null
let globalAuthPromiseCreatedAt = 0
const AUTH_PROMISE_REUSE_MS = 5_000; // 5 seconds

type RawTernUser = {
  outUserNo?: string
  nickName?: string
  userName?: string
  avatarUrl?: string
  displayName?: string
  clientUser?: Partial<ClientUser>
}

function normalizeTernUser(raw: RawTernUser | null | undefined): ClientUser | null {
  if (!raw) return null
  const clientUser = raw.clientUser ?? {}
  const userId = clientUser.userId || raw.outUserNo
  if (!userId) return null
  const nickName = clientUser.nickName || raw.nickName || clientUser.userName || raw.userName || userId
  return {
    userId,
    nickName,
    userName: clientUser.userName || raw.userName || userId,
    avatarUrl: clientUser.avatarUrl || raw.avatarUrl || '',
    displayName: clientUser.displayName || raw.displayName || nickName,
  }
}

function normalizeClientUser(raw: Partial<ClientUser> | null | undefined): ClientUser | null {
  if (!raw?.userId) return null
  const nickName = raw.nickName || raw.displayName || raw.userName || raw.userId
  return {
    userId: raw.userId,
    nickName,
    userName: raw.userName || raw.userId,
    avatarUrl: raw.avatarUrl || '',
    displayName: raw.displayName || nickName,
    isAdmin: raw.isAdmin ?? false,
    isLogAdmin: raw.isLogAdmin ?? raw.isAdmin ?? false,
    isBenchAdmin: raw.isBenchAdmin ?? raw.isAdmin ?? false,
    isClawEvolveAdmin: raw.isClawEvolveAdmin ?? raw.isAdmin ?? false,
    isSuperAdmin: raw.isSuperAdmin ?? false,
  }
}

function readCookieValue(name: string): string | null {
  const cookies = document.cookie ? document.cookie.split(';') : []
  for (const cookie of cookies) {
    const [rawKey, ...rawValueParts] = cookie.trim().split('=')
    if (rawKey !== name) continue
    const rawValue = rawValueParts.join('=')
    try {
      return decodeURIComponent(rawValue)
    } catch {
      return rawValue
    }
  }
  return null
}

function isInternalDevLoginHost(hostname = window.location.hostname): boolean {
  return INTERNAL_DEV_LOGIN_HOST_PATTERN.test(hostname)
}

function isLocalDevelopmentHost(hostname = window.location.hostname): boolean {
  return hostname === 'localhost' || hostname === '127.0.0.1'
}

export function isDevAuthMode(): boolean {
  return isLocalDevelopmentHost() && sessionStorage.getItem(DEV_AUTH_MODE_KEY) === 'dev'
}

export function enterDevAuthMode(): void {
  if (!isLocalDevelopmentHost()) return
  sessionStorage.setItem(DEV_AUTH_MODE_KEY, 'dev')
  clearCachedClientUserForTests()
}

export function leaveDevAuthMode(): void {
  sessionStorage.removeItem(DEV_AUTH_MODE_KEY)
  // Remove the key written by the short-lived localStorage implementation so
  // an existing browser is immediately restored to real-login-first behavior.
  localStorage.removeItem(DEV_AUTH_MODE_KEY)
  clearCachedClientUserForTests()
}

export function getUserFromInternalLoginCookie(hostname = window.location.hostname): ClientUser | null {
  if (!isInternalDevLoginHost(hostname)) return null

  const userId =
    readCookieValue('userId') ||
    readCookieValue('antcode_user_extern_no') ||
    readCookieValue('staff_id')

  if (!userId) return null

  const nickName = readCookieValue('nick_name') || readCookieValue('x-user-name') || `用户-${userId}`
  return {
    userId,
    nickName,
    userName: userId,
    avatarUrl: '',
    displayName: nickName,
  }
}

/**
 * Read user identity from TERN platform (embedded iframe mode).
 * Priority: window.__TERN__ → window.parent.__TERN__ → null
 */
function getUserFromTern(): ClientUser | null {
  try {
    const user = normalizeTernUser(window.__TERN__?.user)
    if (user) return user
  } catch {
    // Cross-origin access to window.__TERN__ may throw
  }

  try {
    const user = normalizeTernUser(window.parent?.__TERN__?.user)
    if (user) return user
  } catch {
    // Cross-origin iframe access may throw
  }

  return null
}

/**
 * Check if running inside TERN platform (not local dev).
 */
function isTernEmbedded(): boolean {
  return !!getUserFromTern()
}

export { getUserFromTern, isTernEmbedded }

/**
 * Synchronous helper to get the current user outside of React hooks.
 * Reads only from the internal TERN login context.
 * Used by non-React code (e.g. API client) that cannot use hooks.
 */
export function getClientUser(): ClientUser | null {
  return getUserFromTern() || cachedServerUser || getUserFromInternalLoginCookie()
}

function fetchClientUserFromServerOnce(): Promise<ClientUser | null> {
  const now = Date.now();
  if (globalAuthPromise && (now - globalAuthPromiseCreatedAt) < AUTH_PROMISE_REUSE_MS) {
    return globalAuthPromise;
  }
  globalAuthPromise = fetchClientUserFromServer();
  globalAuthPromiseCreatedAt = now;
  return globalAuthPromise;
}

async function fetchClientUserFromServer(): Promise<ClientUser | null> {
  if (typeof fetch !== 'function') return null

  try {
    const res = await fetch(isDevAuthMode() ? '/api/auth/me?dev=1' : '/api/auth/me', {
      method: 'GET',
      credentials: 'include',
    })
    if (!res.ok) return null
    const user = normalizeClientUser(await res.json())
    cachedServerUser = user
    return user
  } catch {
    return null
  }
}

export function clearCachedClientUserForTests() {
  cachedServerUser = null
  globalAuthPromise = null
  globalAuthPromiseCreatedAt = 0
}

/**
 * Authentication states for the useClientUser hook.
 */
export type AuthState = 'loading' | 'ready' | 'login_required'

/**
 * Hook that provides the current user identity with full auth flow:
 *
 * 1. TERN-embedded: reads from window.__TERN__ (real SSO identity)
 * 2. Server auth: asks /api/auth/me to resolve Buservice cookies
 * 3. Internal local dev: reads direct user cookies on *.alipay.net if present
 * 4. No trusted identity: enters 'login_required' state
 */
export function useClientUser() {
  const [user, setUser] = useState<ClientUser | null>(null)
  const [authState, setAuthState] = useState<AuthState>('loading')

  useEffect(() => {
    let disposed = false
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    let settled = false

    const finish = (nextUser: ClientUser | null) => {
      if (disposed || settled) return
      settled = true
      if (pollTimer) clearTimeout(pollTimer)
      setUser(nextUser)
      setAuthState(nextUser ? 'ready' : 'login_required')
    }

    const pollClientUser = () => {
      if (disposed || settled) return
      const clientUser = getClientUser()
      if (clientUser) {
        finish(clientUser)
        return
      }

      pollTimer = setTimeout(pollClientUser, TERN_AUTH_POLL_INTERVAL_MS)
    }

    const initialUser = getClientUser()
    if (initialUser) {
      finish(initialUser)
      // TERN/cookie user lacks server-side role flags — always enrich from server
      void fetchClientUserFromServerOnce().then((serverUser) => {
        if (disposed || !serverUser) return
        // Merge server-side role flags into the existing user
        setUser((prev) =>
          prev && prev.userId === serverUser.userId
            ? { ...prev, isAdmin: serverUser.isAdmin, isLogAdmin: serverUser.isLogAdmin, isBenchAdmin: serverUser.isBenchAdmin, isClawEvolveAdmin: serverUser.isClawEvolveAdmin, isSuperAdmin: serverUser.isSuperAdmin }
            : prev,
        )
      })
      return
    }

    void fetchClientUserFromServerOnce().then((serverUser) => {
      finish(serverUser)
    })
    pollTimer = setTimeout(pollClientUser, TERN_AUTH_POLL_INTERVAL_MS)

    return () => {
      disposed = true
      if (pollTimer) clearTimeout(pollTimer)
    }
  }, [])

  return { user, authState }
}
