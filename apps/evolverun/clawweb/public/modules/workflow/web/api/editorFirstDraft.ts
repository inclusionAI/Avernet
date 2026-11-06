import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

type FirstDraftRequest = { workflowId: string; spec: WorkflowSpec; packId: string; expectedDraftAbsent: true;
  expectedActiveDeployNumber: number; expectedActiveToken: string }

// The conflict classification is returned directly from this response, never
// inferred from a thrown exception (even one from an earlier request).
type FirstDraftResult = { kind: 'conflict'; code: 'DRAFT_ALREADY_EXISTS' | 'BASELINE_STALE' } | { kind: 'response'; body: unknown }

export async function writeEditorFirstDraft(request: FirstDraftRequest): Promise<FirstDraftResult> {
  const user = getClientUser()
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Pack-Version-CSRF': '1' }
  if (user) { headers['X-User-Id'] = user.userId; headers['X-User-Name'] = encodeURIComponent(user.nickName) }
  const response = await fetch('/api/auto-heal/editor-save-first-draft', {
    method: 'POST', headers, cache: 'no-store',
    body: new Blob([JSON.stringify(request)], { type: 'application/json' }),
  })
  if (!(response instanceof Response)) throw new Error('首次草稿响应无效')
  // Calling the native getter and body method also rejects objects which only
  // forge Response.prototype; do not trust user-overridden response methods.
  const status = Object.getOwnPropertyDescriptor(Response.prototype, 'status')!.get!.call(response) as number
  if (status < 200 || status >= 300) {
    if (status === 409) {
      try {
        const text = await Response.prototype.text.call(response)
        if (text.length <= 8192) {
          const body: unknown = JSON.parse(text)
          if (body && typeof body === 'object' && !Array.isArray(body)
            && Object.hasOwn(body, 'code') && 'code' in body && (body.code === 'DRAFT_ALREADY_EXISTS' || body.code === 'BASELINE_STALE')) return { kind: 'conflict', code: body.code }
        }
      } catch { /* A missing, unreadable or malformed body proves nothing. */ }
    }
    throw new Error('首次草稿请求失败')
  }
  return { kind: 'response', body: await Response.prototype.json.call(response) as unknown }
}
