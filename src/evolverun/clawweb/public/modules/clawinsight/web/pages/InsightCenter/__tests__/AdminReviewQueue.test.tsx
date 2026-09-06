import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  adminImprovements: vi.fn(),
  adminImprovement: vi.fn(),
  reviewAdminImprovement: vi.fn(),
  adminExecuteOnce: vi.fn(),
}))

vi.mock('../../../api/insight', () => ({ insightApi: mocks }))
vi.mock('../FailureTaskDrawer', () => ({
  default: ({ task }: { task: { sessionId: string; taskIndex: number } }) => <div>BAD_CASE:{task.sessionId}:{task.taskIndex}</div>,
}))

import AdminReviewQueue from '../AdminReviewQueue'

const baseImprovement = {
  ownerUserId: 'dev_local', botOwnerUserId: 'dev_local', botId: 'bot-1',
  userGuidance: null, sourceType: 'ADMIN_RULE_DIRECT_EVOLUTION',
  sourceRuleId: 'tool.utoo-proxy.unsupported', evidenceCount: 1, sessionCount: 1,
  dataStartTime: null, dataEndTime: null, dataAsOf: '2026-08-17T00:00:00Z',
  batchId: 'batch-1', status: 'PENDING_ADMIN', actionType: 'DIRECT_EVOLUTION' as const,
  assignmentReason: '规则明确', rootCauseSummary: '工具选择错误', suggestedAction: '更新 tools.md',
  adminReviewStatus: 'PENDING' as const, adminReviewedBy: null, adminReviewedAt: null,
  adminReviewComment: null, rejectReasonCode: null, rejectComment: null, rejectedAt: null,
  handledAt: null, verificationStatus: 'NOT_STARTED' as const, verificationLastCheckedAt: null,
  verificationNewSessionCount: 0, verificationLastRecurrenceAt: null, resolvedSource: null,
  latestEvolveTaskId: null, latestEvolveTaskStatus: null, appliedEvolveTaskId: null,
  appliedBy: null, appliedAt: null, version: 1, createdBy: 'governance-agent',
  gmtCreate: 1, gmtModified: 1,
}

const items = [
  { ...baseImprovement, improvementId: 3, title: '自动优化候选项' },
  {
    ...baseImprovement,
    improvementId: 4,
    title: '手动优化候选项',
    sourceType: 'ADMIN_RULE_ASSIGN_OWNER',
    sourceRuleId: 'tool.web-search.use-asap',
    actionType: 'ASSIGN_OWNER' as const,
    rootCauseSummary: '权限配置缺失',
  },
]

function setup() {
  mocks.adminImprovements.mockClear()
  mocks.adminImprovement.mockClear()
  mocks.reviewAdminImprovement.mockClear()
  mocks.adminExecuteOnce.mockClear()
  mocks.adminImprovements.mockResolvedValue({
    items,
    nextCursor: null,
    reviewCounts: { pending: 2, approved: 0, rejected: 0 },
  })
  mocks.reviewAdminImprovement.mockResolvedValue({})
  return render(<AdminReviewQueue botOptions={[{ botId: 'bot-1', botName: '测试 Bot' }]} onSelectImprovement={() => {}} />)
}

describe('Admin review queue', () => {
  it('only exposes approve/reject and supports select-all batch rejection with a required reason', async () => {
    setup()
    const user = userEvent.setup()

    expect(await screen.findByText('自动优化候选项')).toBeInTheDocument()
    expect(screen.queryByText(/批准并信任/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: '全选当前页' }))
    expect(screen.getByText('已选择 2 项')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '批量驳回' }))

    const confirm = screen.getByRole('button', { name: '确认' })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('驳回理由（必填）'), '属于业务预期，Gov Agent 应降低该信号权重')
    await user.click(confirm)

    await waitFor(() => expect(mocks.reviewAdminImprovement).toHaveBeenCalledTimes(2))
    expect(mocks.reviewAdminImprovement).toHaveBeenCalledWith(3, {
      decision: 'REJECT',
      comment: '属于业务预期，Gov Agent 应降低该信号权重',
      version: 1,
    })
    expect(mocks.reviewAdminImprovement).toHaveBeenCalledWith(4, {
      decision: 'REJECT',
      comment: '属于业务预期，Gov Agent 应降低该信号权重',
      version: 1,
    })
  })

  it('supports approving every pending improvement on the current page', async () => {
    setup()
    const user = userEvent.setup()

    await screen.findByText('手动优化候选项')
    await user.click(screen.getByRole('button', { name: '全部批准' }))
    await user.click(screen.getByRole('button', { name: '确认' }))

    await waitFor(() => expect(mocks.reviewAdminImprovement).toHaveBeenCalledTimes(2))
    expect(mocks.reviewAdminImprovement).toHaveBeenCalledWith(3, {
      decision: 'APPROVE', comment: undefined, version: 1,
    })
    expect(mocks.reviewAdminImprovement).toHaveBeenCalledWith(4, {
      decision: 'APPROVE', comment: undefined, version: 1,
    })
  })

  it('opens the complete Bad Case from an improvement evidence snapshot', async () => {
    setup()
    mocks.adminImprovement.mockResolvedValue({
      ...items[0],
      evidence: [{
        sessionId: 'admin-gate-auto-20260817', taskIndex: 0, ordinal: 0,
        taskDescription: '抓取公开状态页并生成服务异常摘要', failureClass: 'TOOL_FAILURE',
        reasoningSummary: '错误选择 UTOO_PROXY', payloadRef: 'oss://fixture', payloadEtag: 'etag', payloadVersionId: null,
      }],
      evolveLinks: [],
    })
    const user = userEvent.setup()
    render(<AdminReviewQueue
      botOptions={[{ botId: 'bot-1', botName: '测试 Bot' }]}
      selectedImprovementId={3}
      onSelectImprovement={() => {}}
    />)

    const open = await screen.findByRole('button', { name: '查看完整 Bad Case' })
    await user.click(open)
    expect(screen.getByText('BAD_CASE:admin-gate-auto-20260817:0')).toBeInTheDocument()
  })

  it('allows an approved improvement to start one admin-only repair', async () => {
    const approved = {
      ...items[0],
      status: 'ACTIVE',
      adminReviewStatus: 'APPROVED' as const,
      evidence: [],
      evolveLinks: [],
    }
    mocks.adminImprovements.mockClear()
    mocks.adminImprovement.mockClear()
    mocks.adminExecuteOnce.mockClear()
    mocks.adminImprovements.mockResolvedValue({
      items: [approved],
      nextCursor: null,
      reviewCounts: { pending: 0, approved: 1, rejected: 0 },
    })
    mocks.adminImprovement.mockResolvedValue(approved)
    mocks.adminExecuteOnce.mockResolvedValue({
      taskId: 'EV-ADMIN-ONCE-1',
      taskName: '管理员代处理',
      status: 'running',
      improvementId: 3,
      executionMode: 'ADMIN_ONCE',
      operatorUserId: 'admin-1',
      targetUserId: 'dev_local',
      targetBotId: 'bot-1',
      persistentAuthorization: false,
      source: null,
      steps: [],
    })
    const user = userEvent.setup()
    render(<AdminReviewQueue
      botOptions={[{ botId: 'bot-1', botName: '测试 Bot' }]}
      selectedImprovementId={3}
      onSelectImprovement={() => {}}
    />)

    await user.click(await screen.findByRole('button', { name: '代用户发起一次自动修复' }))
    await user.type(screen.getByLabelText('管理员代处理原因 *'), '用户长期未处理，问题持续影响任务完成率')
    await user.click(screen.getByRole('button', { name: '确认并进入进化室' }))

    await waitFor(() => expect(mocks.adminExecuteOnce).toHaveBeenCalledWith(
      3,
      {
        reason: '用户长期未处理，问题持续影响任务完成率',
        repairDirection: '更新 tools.md',
      },
      expect.any(String),
    ))
    expect(await screen.findByText(/已创建一次性管理员代处理任务 EV-ADMIN-ONCE-1/)).toBeInTheDocument()
  })
})
