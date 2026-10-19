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

it('does not imply a separate missing review action for a publishable candidate', () => {
  expect(candidateLabel({ release: { approval: null, deployments: [] } } as unknown as RepairTaskDetail,
    { revision: 1, phase: 'review' } as never)).toBe('待确认发布')
})

it('creates Git links only from a safe server-provided repository URL', () => {
  const base = 'a'.repeat(40), candidate = 'b'.repeat(40)
  expect(repairGitLinks('https://example.com/workflows/packs.git', base, candidate)).toEqual({
    baseline: `https://example.com/workflows/packs/commit/${base}`,
    candidate: `https://example.com/workflows/packs/commit/${candidate}`,
    compare: `https://example.com/workflows/packs/compare/${base}...${candidate}`,
  })
  for (const url of ['javascript:alert(1)', 'https://user:secret@host/repo', undefined]) {
    expect(repairGitLinks(url, base, candidate)).toBeNull()
  }
})
