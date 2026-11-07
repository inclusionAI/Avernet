import { fetchJson } from '@avernet/clawweb-shared/web/api/client'

export type PackOperation = { requestId: string; kind: 'save' | 'deploy'; state: string; result?: unknown }
export async function readPackOperation(id: string, requestId: string, kind: PackOperation['kind'], recover = false, signal?: AbortSignal): Promise<PackOperation> {
  const result = await fetchJson<PackOperation>(`/api/workflows/${encodeURIComponent(id)}/pack-operations/${encodeURIComponent(requestId)}${recover ? '/reconcile' : ''}`,
    recover ? { method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' }, signal } : { signal })
  if (!result || result.requestId !== requestId || result.kind !== kind || typeof result.state !== 'string' || result.state.length > 64) throw new Error('操作状态身份不匹配')
  return result
}
