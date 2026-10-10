import { fetchJson } from '@avernet/clawweb-shared/web/api/client'

/** Bound read-only UI requests; never apply this retry affordance to task mutations. */
export async function readOnlyJson<T>(url: string, signal?: AbortSignal, queryBody?: unknown): Promise<T> {
  const controller = new AbortController()
  const cancel = () => controller.abort(signal?.reason)
  if (signal?.aborted) cancel()
  else signal?.addEventListener('abort', cancel, { once: true })
  const timer = setTimeout(() => controller.abort(new Error('读取超时，请重试；其他内容仍可查看。')), 30_000)
  try { return await fetchJson<T>(url, { signal: controller.signal,
    ...(queryBody === undefined ? {} : { method: 'POST', body: JSON.stringify(queryBody) }) }) }
  finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel) }
}
