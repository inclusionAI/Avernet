import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import { readEditorDraftCas } from './editorDraftCas'
import { validateSnapshot } from '../editor/packVersionSession'
import { assertLosslessNumbers } from '../editor/losslessNumbers'
import { assertEditorWorkflowReadable, editorWorkflowReadIsCurrent } from '../editor/workflowWriteGuard'
import { serializeUnifiedSpec } from './workflowUnifiedSave'

export type AutoHealUnifiedBaseline = Readonly<{
  workflowId: string; packId: string; sourceVersion: 'draft' | 'current'; sourceCommit: string
  expectedSavedHead: string | null; expectedDraftToken: string; originalSpecJson: string
}>
const reads = new WeakMap<AutoHealUnifiedBaseline, { workflowId: string; epoch: number; serialized: string; reviewedSpec?: string }>()
const READ_TIMEOUT_MS = 10_000
async function boundedRead<T>(operation: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try { return await Promise.race([operation, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('读取超时；未发送保存。')), READ_TIMEOUT_MS) })]) }
  finally { clearTimeout(timer) }
}

export class AutoHealScopedCapabilityError extends Error {}

export class AutoHealBaselineUnavailableError extends AutoHealScopedCapabilityError {
  constructor() { super('无法同步数据库与 Git：缺少数据库草稿或草稿(wf)或生效 tag 基线。请先恢复工作流资源，再保存；不会创建空 Git 树。') }
}

/** Capability is an explicitly versioned host declaration, never a client grant. */
export async function readAutoHealUnifiedCapability(workflowId?: string): Promise<boolean> {
  let capability: unknown
  try { capability = await boundedRead(fetchJson('/api/auto-heal/capabilities', { cache: 'no-store' })) }
  catch { throw new Error('无法确认宿主保存能力。') }
  if (!capability || typeof capability !== 'object' || Array.isArray(capability)) throw new Error('宿主保存能力响应无效。')
  const version = (capability as { autoHealUnifiedSave?: unknown }).autoHealUnifiedSave
  if (version === 'v1') return true
  if (version === 'v1-workflow') {
    if (!workflowId) throw new AutoHealScopedCapabilityError('宿主需要工作流身份以确认保存能力；未发送保存。')
    let specific: unknown
    try { specific = await boundedRead(fetchJson(`/api/auto-heal/capabilities?workflowId=${encodeURIComponent(workflowId)}`, { cache: 'no-store' })) }
    catch { throw new AutoHealScopedCapabilityError('已识别统一保存宿主，但无法确认此工作流的保存能力；未发送保存，请重试或联系维护人员。') }
    if (!specific || typeof specific !== 'object' || Array.isArray(specific)) throw new AutoHealScopedCapabilityError('工作流保存能力响应无效；未发送保存，不会降级。')
    const mode = (specific as { autoHealUnifiedSave?: unknown }).autoHealUnifiedSave
    if (mode === 'v1') return true
    if (mode === 'baseline-unavailable') throw new AutoHealBaselineUnavailableError()
    if (mode === false) return false
    throw new AutoHealScopedCapabilityError('工作流保存能力响应无法识别；未发送保存，不会降级。')
  }
  // Older public hosts explicitly returned null when the capability was disabled.
  if (version === false || version === null) return false
  throw new Error('宿主保存能力响应无法识别。')
}

export async function readAutoHealUnifiedBaseline(workflowId: string): Promise<AutoHealUnifiedBaseline> {
  const epoch = assertEditorWorkflowReadable(workflowId)
  if (!await readAutoHealUnifiedCapability(workflowId)) throw new Error('宿主未启用统一保存；请求未发送。')
  const draft = await boundedRead(readEditorDraftCas(workflowId))
  assertLosslessNumbers(draft.specJson)
  const original: unknown = JSON.parse(draft.specJson)
  if (!original || typeof original !== 'object' || Array.isArray(original)
    || Object.hasOwn(original, 'id') && (original as { id: unknown }).id !== workflowId) throw new Error('数据库草稿内容身份无效；请求未发送。')
  const endpoint = `/api/workflows/${encodeURIComponent(workflowId)}/pack-version`
  // Only a real, exact endpoint response is evidence for trying current. A
  // missing manifest can share this code: current must also attest a null
  // wf target head before it can be used as the first saved baseline.
  const user = getClientUser()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (user) { headers['X-User-Id'] = user.userId; headers['X-User-Name'] = encodeURIComponent(user.nickName) }
  const draftRead = await boundedRead((async () => {
    const response = await fetch(`${endpoint}?version=draft`, { cache: 'no-store', headers })
    if (!(response instanceof Response)) throw new Error('Git 基线响应无效；未发送保存。')
    const status = Object.getOwnPropertyDescriptor(Response.prototype, 'status')!.get!.call(response) as number
    const text = await Response.prototype.text.call(response)
    if (status === 404 && text.length <= 8192) {
      let body: unknown
      try { body = JSON.parse(text) } catch { /* Invalid responses never prove absence. */ }
      if (body && typeof body === 'object' && !Array.isArray(body)
        && Object.keys(body).length === 2 && (body as Record<string, unknown>).error === 'Snapshot not found'
        && (body as Record<string, unknown>).code === 'SNAPSHOT_NOT_FOUND') return { absent: true as const }
    }
    if (status < 200 || status >= 300) throw new Error('无法读取草稿(wf) Git 基线；未发送保存。')
    return { absent: false as const, value: JSON.parse(text) as unknown }
  })())
  const sourceVersion = draftRead.absent ? 'current' : 'draft'
  const raw = draftRead.absent ? await boundedRead(fetchJson<unknown>(`${endpoint}?version=current`, { cache: 'no-store' })) : draftRead.value
  const snapshot = validateSnapshot(raw, workflowId, sourceVersion)
  if (!editorWorkflowReadIsCurrent(workflowId, epoch) || snapshot.packId !== (draft.packId ?? workflowId)
    || (sourceVersion === 'current' && snapshot.draftHead !== null)) throw new Error('统一保存基线不一致或读取期间已变化；请求未发送。')
  const baseline: AutoHealUnifiedBaseline = Object.freeze({ workflowId, packId: snapshot.packId, sourceVersion,
    sourceCommit: snapshot.baseCommit, expectedSavedHead: snapshot.draftHead, expectedDraftToken: draft.draftToken, originalSpecJson: draft.specJson })
  reads.set(baseline, { workflowId, epoch, serialized: JSON.stringify(baseline) })
  return baseline
}

export function assertAutoHealUnifiedBaseline(baseline: AutoHealUnifiedBaseline, workflowId: string): void {
  const read = baseline && reads.get(baseline)
  if (!read || read.workflowId !== workflowId || read.serialized !== JSON.stringify(baseline)
    || !editorWorkflowReadIsCurrent(workflowId, read.epoch)) throw new Error('缺少可信或最新的统一保存基线；请求未发送。')
}

/** Confirmation binds exactly the displayed draft and candidate to this observed baseline. */
export function confirmAutoHealUnifiedReview(baseline: AutoHealUnifiedBaseline, candidate: unknown): string {
  assertAutoHealUnifiedBaseline(baseline, baseline.workflowId)
  const serialized = serializeUnifiedSpec(candidate)
  reads.get(baseline)!.reviewedSpec = serialized
  return serialized
}
export function assertAutoHealUnifiedReview(baseline: AutoHealUnifiedBaseline, workflowId: string, candidate: unknown): string {
  assertAutoHealUnifiedBaseline(baseline, workflowId)
  const serialized = serializeUnifiedSpec(candidate)
  if (reads.get(baseline)?.reviewedSpec !== serialized) throw new Error('修复内容尚未确认或确认后已变化；请求未发送。')
  return serialized
}
