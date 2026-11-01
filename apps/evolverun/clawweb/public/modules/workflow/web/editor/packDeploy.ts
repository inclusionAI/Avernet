import type { AutoHealDeployRequest, AutoHealDeployResult } from '@avernet/clawweb-shared/web/types'
import { publish, type Session, type DeployAttempt } from './packVersionSession'
import { readPackOperation } from './packOperation'

export const PACK_DEPLOY_TIMEOUT_MS = 30_000
function verified(value: unknown, attempt: DeployAttempt): AutoHealDeployResult {
  const result = value as AutoHealDeployResult & { requestId?: string }
  if (!result || result.ok !== true || result.deployed !== true || result.workflowId !== attempt.input.workflowId
    || result.packId !== attempt.input.packId || (result.requestId !== undefined && result.requestId !== attempt.input.requestId)
    || !Number.isSafeInteger(result.deployNumber) || result.deployNumber < 1
    || !Number.isSafeInteger(result.version) || result.version < 1 || typeof result.tagName !== 'string' || !result.tagName) throw new Error('部署响应无法验证')
  return result
}
async function run(session: Session, attempt: DeployAttempt, action: () => Promise<unknown>): Promise<AutoHealDeployResult | null> {
  if (session.state.deployment?.pending) return null
  const token = {}
  session.deployRequest = token
  publish(session, { ...session.state, deployment: { ...attempt, pending: true, error: null } })
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const raw = await Promise.race([action(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('未收到部署响应')), PACK_DEPLOY_TIMEOUT_MS)
    })])
    if (session.deployRequest !== token) return null
    const result = verified(raw, attempt)
    publish(session, { ...session.state, deployment: null })
    return result
  } catch {
    if (session.deployRequest === token) publish(session, { ...session.state, deployment: { ...attempt, pending: false,
      error: '部署结果尚未确认；保留原 candidateCommit 和 requestId，请查询或恢复操作后再部署。' } })
    return null
  } finally {
    clearTimeout(timer)
    if (session.deployRequest === token) session.deployRequest = undefined
  }
}
export function deployPackCandidate(session: Session, id: string, packId: string, commit: string,
  transport: (input: AutoHealDeployRequest) => Promise<AutoHealDeployResult>) {
  const existing = session.state.deployment
  if (existing && (existing.input.candidateCommit !== commit || existing.input.packId !== packId || existing.input.workflowId !== id)) {
    throw new Error('上次部署未完成，不能分配新 requestId 或替换候选。')
  }
  const attempt: DeployAttempt = existing ?? { input: { workflowId: id, packId, candidateCommit: commit,
    requestId: crypto.randomUUID(), note: 'editor deploy', autoRun: false }, pending: false, error: null }
  return run(session, attempt, () => transport(attempt.input))
}
export function reconcilePackDeploy(session: Session, recover = false) {
  const attempt = session.state.deployment
  if (!attempt) return Promise.resolve(null)
  return run(session, attempt, async () => {
    const operation = await readPackOperation(attempt.input.workflowId, attempt.input.requestId, 'deploy', recover)
    if (operation.state !== 'completed') throw new Error('部署操作尚未完成')
    return operation.result
  })
}
