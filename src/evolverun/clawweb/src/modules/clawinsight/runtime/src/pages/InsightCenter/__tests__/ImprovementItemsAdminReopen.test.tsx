import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  adminImprovements: vi.fn(),
  adminImprovement: vi.fn(),
  adminReopenImprovement: vi.fn(),
}))

vi.mock('../../../hooks/useClientUser', () => ({
  useClientUser: () => ({ user: { userId: '205357', isAdmin: true }, authState: 'ready' }),
}))
vi.mock('../../../api/insight', () => ({ insightApi: mocks }))
vi.mock('../../../api/client', () => ({ api: {} }))

import ImprovementItems from '../ImprovementItems'

const rejected = {
  improvementId: 99,
  ownerUserId: '481227',
  botOwnerUserId: '481227',
  botId: 'default',
  title: '钉钉消息推送目标无法解析导致 AI 日报未送达',
  userGuidance: '[Admin驳回]\n说明：暂不处理',
  sourceType: 'REJECTED_RULE_ASSIGN_OWNER',
  sourceRuleId: 'dingtalk.daily-report.target',
  evidenceCount: 2,
  sessionCount: 1,
  dataStartTime: null,
  dataEndTime: null,
  dataAsOf: '2026-09-03T00:00:00Z',
  batchId: 'reopen-test',
  status: 'ARCHIVED',
  actionType: 'ASSIGN_OWNER' as const,
  assignmentReason: '需要 Owner 检查推送目标配置',
  rootCauseSummary: '推送目标无法解析',
  suggestedAction: '核对钉钉机器人目标配置并重新验证。',
  adminReviewStatus: 'REJECTED' as const,
  adminReviewedBy: 'admin-1',
  adminReviewedAt: 1,
  adminReviewComment: '暂不处理',
  rejectReasonCode: 'ADMIN_REJECTED',
  rejectComment: '暂不处理',
  rejectedAt: 1,
  handledAt: null,
  verificationStatus: 'NOT_STARTED' as const,
  verificationLastCheckedAt: null,
  verificationNewSessionCount: 0,
  verificationLastRecurrenceAt: null,
  resolvedSource: null,
  latestEvolveTaskId: null,
  latestEvolveTaskStatus: null,
  appliedEvolveTaskId: null,
  appliedBy: null,
  appliedAt: null,
  version: 1,
  createdBy: 'governance-agent',
  gmtCreate: 1,
  gmtModified: 1,
}

describe('Insight admin historical improvement reopen', () => {
  it('uses the admin list and reopens a rejected governance item without the owner PATCH', async () => {
    mocks.adminImprovements.mockResolvedValue({
      items: [rejected],
      nextCursor: null,
      statusCounts: { active: 0, inProgress: 0, resolved: 0, archived: 1 },
      reviewCounts: { pending: 0, approved: 0, rejected: 1 },
    })
    mocks.adminImprovement.mockResolvedValue({ ...rejected, evidence: [], evolveLinks: [] })
    mocks.adminReopenImprovement.mockResolvedValue({
      ...rejected,
      status: 'ACTIVE',
      sourceType: 'ADMIN_RULE_ASSIGN_OWNER',
      adminReviewStatus: 'APPROVED',
      version: 2,
    })

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/insight?tab=todo&ownerUserId=481227&improvementId=99']}>
        <ImprovementItems
          ownerUserId="481227"
          readOnly
          selectedImprovementId={99}
          botOptions={[{ botId: 'default', botName: 'default' }]}
          onBotChange={() => undefined}
          onSelectImprovement={() => undefined}
          onGoFailures={() => undefined}
        />
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('button', { name: /已驳回/ }))
    await waitFor(() => expect(mocks.adminImprovements).toHaveBeenCalledWith(expect.objectContaining({
      ownerUserId: '481227',
      status: 'ARCHIVED',
      includeAll: true,
    })))
    expect((await screen.findAllByText(rejected.title)).length).toBeGreaterThanOrEqual(1)
    expect(mocks.adminImprovement).toHaveBeenCalledWith(99)

    await user.click((await screen.findAllByRole('button', { name: '恢复处理' }))[0])
    await waitFor(() => expect(mocks.adminReopenImprovement).toHaveBeenCalledWith(99, {
      version: 1,
      reason: '管理员重新评估后决定继续处理该改进项。',
    }))
  })
})
