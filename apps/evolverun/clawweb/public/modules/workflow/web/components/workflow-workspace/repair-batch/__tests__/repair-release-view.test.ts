import { expect, it } from 'vitest'
import { candidateLabel, repairGitLinks } from '../repair-release-view'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'

it('uses durable deployment and approval status rather than the old review phase', () => {
  const d = { release: { approval: { id: 'approval', status: 'approved', revision: 1 }, deployments: [
    { approvalId: 'approval', revision: 1, status: 'failed' },
  ] } } as unknown as RepairTaskDetail
  expect(candidateLabel(d, { revision: 1, phase: 'review' } as never)).toBe('部署失败')
  expect(candidateLabel(d, { revision: 2, phase: 'no_change' } as never)).toBe('无需变更')
  d.release!.approval!.id = 'new-target-approval'
  expect(candidateLabel(d, { revision: 1, phase: 'review' } as never)).toBe('已确认，待部署')
  d.release!.deployments = []
  expect(candidateLabel(d, { revision: 1, phase: 'review' } as never)).toBe('已确认，待部署')
})

it('creates Git links only from a safe server-provided repository URL', () => {
  const base = 'a'.repeat(40), candidate = 'b'.repeat(40)
  expect(repairGitLinks('https://code.alipay.com/ClawMind/clawflows.git', base, candidate)).toEqual({
    baseline: `https://code.alipay.com/ClawMind/clawflows/commit/${base}`,
    candidate: `https://code.alipay.com/ClawMind/clawflows/commit/${candidate}`,
    compare: `https://code.alipay.com/ClawMind/clawflows/compare/${base}...${candidate}`,
  })
  for (const url of ['javascript:alert(1)', 'https://user:secret@host/repo', undefined]) {
    expect(repairGitLinks(url, base, candidate)).toBeNull()
  }
})
