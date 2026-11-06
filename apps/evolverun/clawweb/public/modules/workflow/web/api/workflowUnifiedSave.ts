import type { Change, Version } from '../editor/packVersionSession'

export type UnifiedSaveInput = {
  requestId: string; sourceVersion: Version; sourceCommit: string; expectedSavedHead: string | null
  expectedDraftToken: string; specJson: string; changes: Change[]
}
export type UnifiedSaveReceipt = { commit: string; draftToken: string }

const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const oid = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/
const hashPattern = /^[a-f0-9]{64}$/
export const UNIFIED_SAVE_TIMEOUT_MS = 30_000

/** This is observation only, never permission for the later POST. */
export async function readUnifiedSaveCapability(id: string): Promise<'available' | 'unavailable'> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([(async () => {
      const response = await fetch(`/api/workflows/${encodeURIComponent(id)}/unified-save`, {
        method: 'GET', headers: { 'X-Pack-Version-CSRF': '1' }, cache: 'no-store', signal: controller.signal,
      })
      // These statuses only choose an explicitly confirmed DB-only path. They
      // are never proof about a POST, and no POST has yet been dispatched.
      if (response.status === 401 || response.status === 404) return 'unavailable' as const
      if (response.status !== 200) throw new Error(`统一保存能力读取失败（HTTP ${response.status}），未发送写入。`)
      let value: unknown
      try { value = await response.json() } catch { throw new Error('统一保存能力响应无效；未发送写入。') }
      if (!record(value) || Object.keys(value).length !== 1 || value.protocol !== 'workflow-unified-save-v1') throw new Error('统一保存能力响应无效；未发送写入。')
      return 'available' as const
    })(), new Promise<never>((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('统一保存能力读取超时；未发送写入。')) }, UNIFIED_SAVE_TIMEOUT_MS) })])
  } finally { clearTimeout(timer) }
}

/** No retry: requestId is only correlation, not an idempotency key. */
export async function workflowUnifiedSave(id: string, input: UnifiedSaveInput): Promise<UnifiedSaveReceipt> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([(async () => {
      const response = await fetch(`/api/workflows/${encodeURIComponent(id)}/unified-save`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Pack-Version-CSRF': '1' },
        cache: 'no-store', signal: controller.signal,
        body: new Blob([JSON.stringify(input)], { type: 'application/json' }),
      })
      let body: unknown
      try { body = await response.json() } catch { throw new Error(`统一保存响应不可用（HTTP ${response.status}）`) }
      if (record(body) && body.requestId === input.requestId && record(body.evidence)) {
        const evidence = body.evidence
        // A proxy can manufacture even a correlated rejection after forwarding
        // the POST. No verifiable origin-bound pre-write receipt exists yet.
        if (response.status === 200 && body.outcome === 'confirmed' && body.reconciliationRequired === false
          && evidence.db === 'confirmed' && evidence.git === 'confirmed'
          && typeof evidence.inputHash === 'string' && hashPattern.test(evidence.inputHash)
          && typeof evidence.commit === 'string' && oid.test(evidence.commit)
          && typeof evidence.draftToken === 'string' && hashPattern.test(evidence.draftToken)) {
          return { commit: evidence.commit, draftToken: evidence.draftToken }
        }
        throw new Error(`统一保存结果为 ${String(body.outcome)}；需要人工核对数据库与 Git。`)
      }
      throw new Error(`统一保存未授权、不可用或响应无效（HTTP ${response.status}）；需要人工核对。`)
    })(), new Promise<never>((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('统一保存超时，结果未知。')) }, UNIFIED_SAVE_TIMEOUT_MS) })])
  } finally { clearTimeout(timer) }
}

/** Refuse JSON.stringify's silent omission/coercion before freezing a candidate. */
export function serializeUnifiedSpec(value: unknown): string {
  const ancestors = new Set<object>()
  const visit = (item: unknown, depth: number): void => {
    if (depth > 100) throw new Error('工作流文档过深，未发送保存。')
    if (item === null || typeof item === 'string' || typeof item === 'boolean') return
    if (typeof item === 'number' && Number.isFinite(item) && !Object.is(item, -0)
      && (!Number.isInteger(item) || Number.isSafeInteger(item))) return
    if (typeof item !== 'object') throw new Error('工作流包含无法无损保存的值；未发送保存。')
    const object = item as object, array = Array.isArray(item), proto = Object.getPrototypeOf(item)
    if (ancestors.has(object) || (array ? proto !== Array.prototype : proto !== Object.prototype && proto !== null)) throw new Error('工作流包含无法无损保存的对象；未发送保存。')
    ancestors.add(object)
    const keys = Reflect.ownKeys(object)
    if (keys.some(key => typeof key !== 'string') || array && keys.length !== (item as unknown[]).length + 1) throw new Error('工作流包含无法无损保存的字段；未发送保存。')
    if (array) for (let i = 0; i < (item as unknown[]).length; i++) {
      if (!Object.hasOwn(object, String(i))) throw new Error('工作流数组存在空项；未发送保存。')
    }
    for (const key of keys) {
      if (array && key === 'length') continue
      const descriptor = Object.getOwnPropertyDescriptor(object, key)
      if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) throw new Error('工作流包含无法无损保存的字段；未发送保存。')
      visit(descriptor.value, depth + 1)
    }
    ancestors.delete(object)
  }
  visit(value, 0)
  return JSON.stringify(value)
}
