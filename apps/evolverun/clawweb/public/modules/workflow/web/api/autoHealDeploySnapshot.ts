import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { AutoHealSaveResult } from '@avernet/clawweb-shared/web/types'
import { autoHealSaveIsCurrent, autoHealUnifiedSaveIdentity } from './autoHealWrites'
import { readEditorDraftCas } from './editorDraftCas'
import { compatiblePackScope, diff, validateSnapshot } from '../editor/packVersionSession'
import { packWorkflow } from '../editor/packCanvas'
import type { DeployReview } from '../components/DeploySnapshotReview'

export type AutoHealDeploySnapshot = { commit: string; draftToken: string; expectedActiveCommit: string; activeRef: string | null; changes: DeployReview[] }

/** Read the same complete saved/active file snapshots and authorized state as Editor. */
export async function readAutoHealDeploySnapshot(receipt: AutoHealSaveResult): Promise<AutoHealDeploySnapshot> {
  const identity = autoHealUnifiedSaveIdentity(receipt)
  if (!identity || !autoHealSaveIsCurrent(receipt)) throw new Error('保存回执无效或已过期；部署未发送。')
  const id = receipt.workflowId
  const endpoint = `/api/workflows/${encodeURIComponent(id)}/pack-version`
  const [raw, current, draft, proof] = await Promise.all([
    fetchJson<unknown>(`${endpoint}?version=draft`, { cache: 'no-store' }),
    fetchJson<unknown>(`${endpoint}?version=current`, { cache: 'no-store' }).then(value => ({ ok: true as const, value }), () => ({ ok: false as const })),
    readEditorDraftCas(id),
    fetchJson<unknown>(`/api/auto-heal/editor-deploy/snapshot-v2/state?workflowId=${encodeURIComponent(id)}`, { cache: 'no-store' }),
  ])
  const saved = validateSnapshot(raw, id, 'draft')
  const packPath = saved.packPath
  if (!proof || typeof proof !== 'object' || Array.isArray(proof)) throw new Error('无法验证部署能力；部署未发送。')
  const state = proof as Record<string, unknown>
  if (state.protocol !== 'editor-deployment-state-v1' || state.workflowId !== id || state.packId !== receipt.packId
    || state.canDeploy !== true || state.savedCommit !== identity.commit || saved.baseCommit !== identity.commit
    || saved.baseRef !== `origin/wf/${id}` || saved.draftHead !== identity.commit || saved.packId !== receipt.packId || typeof packPath !== 'string'
    || (draft.packId ?? id) !== receipt.packId || draft.draftToken !== identity.draftToken) throw new Error('保存后的 Git 或数据库草稿基线已变化，无法部署；请重新核对。')
  let active: ReturnType<typeof validateSnapshot> | null = null
  if (current.ok) {
    active = validateSnapshot(current.value, id, 'current')
    if (state.activeCommit !== active.baseCommit || !compatiblePackScope(saved, active)) throw new Error('生效版基线或 Pack 范围不一致，无法审阅。')
  } else if (state.activeCommit !== null) throw new Error('无法读取生效版或证明首次部署。')
  const priorFiles = active?.versionFiles ?? []
  const prior = new Map(priorFiles.map(file => [file.path, file]))
  const changes: DeployReview[] = diff(priorFiles, saved.versionFiles).map(change => ({
    path: change.path, operation: change.operation === 'delete' ? '删除' : change.operation === 'create' ? '新增' : '更新',
    before: prior.get(change.path) ?? null, after: change.operation === 'delete' ? null : change,
  }))
  if (changes.reduce((n, change) => n + (change.before?.contentBase64.length ?? 0) + (change.after?.contentBase64.length ?? 0), 0) > 512 * 1024) throw new Error('差异内容过大，无法完整审阅；部署未发送。')
  packWorkflow(saved.versionFiles, id, saved.packId)
  if (!autoHealSaveIsCurrent(receipt)) throw new Error('读取期间数据库状态已变化；部署未发送。')
  return { ...identity, expectedActiveCommit: active?.baseCommit ?? '', activeRef: active?.baseRef ?? null, changes }
}
