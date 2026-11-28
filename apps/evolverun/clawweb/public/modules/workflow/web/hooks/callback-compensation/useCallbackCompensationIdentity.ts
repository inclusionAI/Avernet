import { useEffect, useState } from 'react'
import { isDevAuthMode } from '@avernet/clawweb-shared/web/hooks/useClientUser'

export interface CallbackCompensationIdentity { actorId: string; scopeKey: string; generation: number }

/** Native /auth/me observation fences UI only; capabilities still prove exact administrator access.
 * Mirrors useObservedAuthEpoch's focus/visibility/poll invalidation, retaining the server actor for partitioning. */
export function useCallbackCompensationIdentity(): CallbackCompensationIdentity | null {
  const [identity, setIdentity] = useState<CallbackCompensationIdentity | null>(null)
  useEffect(() => {
    let disposed = false, sequence = 0, generation = 0, fingerprint: string | null = null
    let request: AbortController | null = null
    const check = async (suspend: boolean) => {
      const serial = ++sequence
      request?.abort()
      const controller = new AbortController()
      request = controller
      if (suspend) { fingerprint = null; setIdentity(null) }
      const timer = window.setTimeout(() => {
        if (disposed || serial !== sequence) return
        ++sequence; controller.abort(); fingerprint = null; setIdentity(null)
      }, 3000)
      try {
        const mode = isDevAuthMode() ? 'dev' : 'normal'
        const response = await fetch(mode === 'dev' ? '/api/auth/me?dev=1' : '/api/auth/me', {
          credentials: 'include', cache: 'no-store', signal: controller.signal,
        })
        if (!response.ok) throw new Error('Identity unavailable')
        const value = await response.json() as { userId?: unknown; isAdmin?: unknown }
        if (typeof value?.userId !== 'string' || !value.userId.trim()) throw new Error('Identity unavailable')
        if (disposed || controller.signal.aborted || serial !== sequence) return
        const scopeKey = JSON.stringify([window.location.origin, mode])
        const next = JSON.stringify([scopeKey, value.userId, value.isAdmin === true])
        if (next !== fingerprint) {
          fingerprint = next
          setIdentity({ actorId: value.userId, scopeKey, generation: ++generation })
        }
      } catch {
        if (disposed || serial !== sequence) return
        fingerprint = null; setIdentity(null)
      } finally { window.clearTimeout(timer) }
    }
    const invalidate = () => { if (document.visibilityState === 'visible') void check(true) }
    const visibility = () => {
      if (document.visibilityState === 'visible') invalidate()
      else { ++sequence; request?.abort(); fingerprint = null; setIdentity(null) }
    }
    invalidate()
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void check(false) }, 5000)
    window.addEventListener('focus', invalidate)
    window.addEventListener('pageshow', invalidate)
    window.addEventListener('storage', invalidate)
    document.addEventListener('visibilitychange', visibility)
    return () => {
      disposed = true; ++sequence; request?.abort(); window.clearInterval(timer)
      window.removeEventListener('focus', invalidate)
      window.removeEventListener('pageshow', invalidate)
      window.removeEventListener('storage', invalidate)
      document.removeEventListener('visibilitychange', visibility)
    }
  }, [])
  return identity
}
