import { useEffect, useState } from 'react'
import { isDevAuthMode } from '@avernet/clawweb-shared/web/hooks/useClientUser'

const AUTH_POLL_MS = 5_000
const AUTH_TIMEOUT_MS = 3_000

/** Display identity is only an invalidation signal, never an authorization decision. */
export function useObservedAuthEpoch(): number | null {
  const [epoch, setEpoch] = useState<number | null>(null)
  useEffect(() => {
    let disposed = false
    let sequence = 0
    let current: string | null = null
    let generation = 0
    let request: AbortController | null = null

    const check = async (suspend: boolean) => {
      const serial = ++sequence
      request?.abort()
      request = new AbortController()
      const controller = request
      const timeout = window.setTimeout(() => {
        if (disposed || serial !== sequence) return
        ++sequence
        controller.abort()
        current = null
        setEpoch(null)
      }, AUTH_TIMEOUT_MS)
      if (suspend) { current = null; setEpoch(null) }
      try {
        const mode = isDevAuthMode() ? 'dev' : 'normal'
        const response = await fetch(mode === 'dev' ? '/api/auth/me?dev=1' : '/api/auth/me', {
          credentials: 'include', cache: 'no-store', signal: request.signal,
        })
        if (!response.ok) throw new Error('Identity unavailable')
        const identity = await response.json() as { userId?: unknown }
        if (typeof identity.userId !== 'string' || !identity.userId.trim()) throw new Error('Invalid identity')
        if (disposed || serial !== sequence) return
        const next = JSON.stringify([mode, identity.userId])
        if (current !== next) { current = next; setEpoch(++generation) }
      } catch {
        if (disposed || serial !== sequence) return
        current = null
        setEpoch(null)
      } finally { window.clearTimeout(timeout) }
    }
    const invalidate = () => { if (document.visibilityState === 'visible') void check(true) }
    const onVisibility = () => { if (document.visibilityState === 'visible') invalidate(); else { ++sequence; request?.abort(); current = null; setEpoch(null) } }
    if (document.visibilityState === 'visible') void check(true)
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void check(false) }, AUTH_POLL_MS)
    window.addEventListener('focus', invalidate)
    window.addEventListener('pageshow', invalidate)
    window.addEventListener('storage', invalidate)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      disposed = true; ++sequence; request?.abort(); window.clearInterval(timer)
      window.removeEventListener('focus', invalidate)
      window.removeEventListener('pageshow', invalidate)
      window.removeEventListener('storage', invalidate)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])
  return epoch
}
