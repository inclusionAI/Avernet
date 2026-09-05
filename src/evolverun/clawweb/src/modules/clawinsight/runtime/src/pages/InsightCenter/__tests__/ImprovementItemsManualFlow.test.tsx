import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

const mocks = vi.hoisted(() => ({
  improvements: vi.fn(),
  improvement: vi.fn(),
  updateImprovement: vi.fn(),
  markHandled: vi.fn(),
}))

vi.mock('../../../hooks/useClientUser', () => ({
  useClientUser: () => ({ user: { userId: '2088' }, authState: 'ready' }),
}))

vi.mock('../../../api/insight', () => ({ insightApi: mocks }))

import ImprovementItems from '../ImprovementItems'

const base = {
  improvementId: 51, ownerUserId: '2088', botOwnerUserId: '2088', botId: 'bot-manual',
  title: '补齐 Agent 权限配置', userGuidance: null, sourceType: 'ADMIN_RULE_ASSIGN_OWNER',
  sourceRuleId: 'tool.web-search.use-asap', evidenceCount: 2, sessionCount: 1,
  dataStartTime: null, dataEndTime: null, dataAsOf: '2026-08-17T00:00:00Z', batchId: 'batch-1',
  actionType: 'ASSIGN_OWNER', assignmentReason: '需要 Owner 修改权限配置', rootCauseSummary: '缺少网络权限',
  suggestedAction: '补齐权限并重新运行', adminReviewStatus: 'APPROVED', adminReviewedBy: 'admin',
  adminReviewedAt: 1, adminReviewComment: null, rejectReasonCode: null, rejectComment: null,
  rejectedAt: null, verificationLastCheckedAt: null, verificationNewSessionCount: 0,
  verificationLastRecurrenceAt: null, resolvedSource: null, latestEvolveTaskId: null,
  latestEvolveTaskStatus: null, appliedEvolveTaskId: null, appliedBy: null, appliedAt: null,
  createdBy: 'governance-agent', gmtCreate: 1, gmtModified: 1,
}

function detail(overrides: Record<string, unknown> = {}) {
  return { ...base, status: 'ACTIVE', handledAt: null, verificationStatus: 'NOT_STARTED', version: 1, evidence: [], evolveLinks: [], ...overrides }
}

describe('manual improvement flow', () => {
  function LocationProbe() {
    const location = useLocation()
    return <output data-testid="location">{location.pathname}{location.search}</output>
  }

  function renderItems() {
    return render(
      <MemoryRouter>
        <Routes>
          <Route path="*" element={<><ImprovementItems
            selectedImprovementId={51}
            botOptions={[{ botId: 'bot-manual', botName: '手动修复 Bot' }]}
            onBotChange={() => {}}
            onSelectImprovement={() => {}}
            onGoFailures={() => {}}
          /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('opens Bot Repair from a manual improvement instead of the legacy governance task', async () => {
    const active = detail()
    mocks.improvements.mockResolvedValue({ items: [active], nextCursor: null, statusCounts: { active: 1, inProgress: 0, resolved: 0, archived: 0 } })
    mocks.improvement.mockResolvedValue(active)

    const user = userEvent.setup()
    renderItems()

    await user.click(await screen.findByRole('button', { name: '进入 Bot 修复' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/evolve/new?type=repair&improvementId=51')
    expect(mocks.updateImprovement).not.toHaveBeenCalled()
  })

  it('exposes manual repair controls after automatic verification fails', async () => {
    const failed = detail({
      status: 'ACTIVE',
      sourceType: 'ADMIN_RULE_ASSIGN_OWNER',
      actionType: 'ASSIGN_OWNER',
      handledAt: '2026-08-19T08:00:00Z',
      verificationStatus: 'STILL_PRESENT',
      version: 4,
    })
    const verifying = detail({
      status: 'IN_PROGRESS',
      sourceType: 'ADMIN_RULE_ASSIGN_OWNER',
      actionType: 'ASSIGN_OWNER',
      handledAt: '2026-08-19T09:00:00Z',
      verificationStatus: 'PENDING',
      version: 5,
    })
    mocks.improvements.mockResolvedValue({ items: [failed], nextCursor: null, statusCounts: { active: 1, inProgress: 0, resolved: 0, archived: 0 } })
    mocks.improvement.mockResolvedValue(failed)
    mocks.markHandled.mockResolvedValue(verifying)

    const user = userEvent.setup()
    renderItems()

    expect((await screen.findAllByText('自动修复未生效')).length).toBeGreaterThan(0)
    expect(screen.getByText(/自动修复未生效，请手动修复/)).toBeInTheDocument()
    const startButton = screen.getByRole('button', { name: '进入 Bot 修复' })
    expect(startButton).toBeInTheDocument()
    await user.click(startButton)
    expect(screen.getByTestId('location')).toHaveTextContent('/evolve/new?type=repair&improvementId=51')
  })

})
