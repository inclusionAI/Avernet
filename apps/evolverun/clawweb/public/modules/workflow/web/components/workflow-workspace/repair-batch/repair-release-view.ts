import type { RepairRevision } from '../../../../server/contracts/repair-batch'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import { phases } from './repair-view'

export function candidateLabel(detail: RepairTaskDetail, revision: RepairRevision): string {
  const approval = detail.release?.approval
  const deployment = detail.release?.deployments.find(item => item.revision === revision.revision
    && (item.status !== 'failed' || approval?.status !== 'approved' || approval.revision !== revision.revision || item.approvalId === approval.id))
  if (deployment) return ({ succeeded: '已生效，自动观察', failed: '部署失败', uncertain: '部署结果待核对',
    created: '等待部署', dispatching: '正在部署', running: '正在部署' })[deployment.status]
  if (approval?.status === 'approved' && approval.revision === revision.revision) return '已确认，待部署'
  if(detail.automaticProgress?.publishable&&detail.latestSuccessful?.revision===revision.revision&&revision.input.automation?.roundId)return '待确认发布'
  return revision.phase === 'review' ? '待确认发布' : phases[revision.phase] ?? revision.phase
}

export function repairGitLinks(repository: string | undefined, baseline: string, candidate: string | null) {
  try {
    const url = new URL(repository ?? '')
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash
      || !/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(baseline)
      || candidate && !/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(candidate)) return null
    const repo = url.href.replace(/\/$/, '').replace(/\.git$/, '')
    return { baseline: `${repo}/commit/${baseline}`, candidate: candidate ? `${repo}/commit/${candidate}` : null,
      compare: candidate ? `${repo}/compare/${baseline}...${candidate}` : null }
  } catch { return null }
}
