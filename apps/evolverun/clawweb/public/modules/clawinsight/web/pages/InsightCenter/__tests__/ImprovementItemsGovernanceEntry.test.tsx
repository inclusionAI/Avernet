import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'

const mocks = vi.hoisted(() => ({
  improvements: vi.fn(),
  improvement: vi.fn(),
  bots: vi.fn(),
  repairCreate: vi.fn(),
  markHandled: vi.fn(),
}))

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({
  useClientUser: () => ({ user: { userId: '2088' }, authState: 'ready' }),
}))

vi.mock('../../../api/insight', () => ({ insightApi: mocks }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({
  api: {
    tclog: { bots: mocks.bots },
    repair: { create: mocks.repairCreate },
  },
}))

import ImprovementItems from '../ImprovementItems'

const improvement = {
  improvementId: 35, ownerUserId: '2088', botOwnerUserId: '2088', botId: 'bot-source',
  title: '工具调用可靠性治理', userGuidance: null,
  sourceType: 'ADMIN_RULE_DIRECT_EVOLUTION', sourceRuleId: 'tool.utoo-proxy.unsupported',
  evidenceCount: 3, sessionCount: 2, dataStartTime: null, dataEndTime: null,
  dataAsOf: '2026-08-17T00:00:00Z', batchId: 'batch-1', status: 'ACTIVE',
  actionType: 'DIRECT_EVOLUTION', assignmentReason: '授权后可自动修复', rootCauseSummary: '工具选择错误',
  suggestedAction: '更新 tools.md', adminReviewStatus: 'APPROVED', adminReviewedBy: 'admin',
  adminReviewedAt: 1, adminReviewComment: null, rejectReasonCode: null, rejectComment: null,
  rejectedAt: null, handledAt: null, verificationStatus: 'NOT_STARTED', verificationLastCheckedAt: null,
  verificationNewSessionCount: 0, verificationLastRecurrenceAt: null, resolvedSource: null,
  latestEvolveTaskId: null, latestEvolveTaskStatus: null, appliedEvolveTaskId: null,
  appliedBy: null, appliedAt: null, version: 1, createdBy: 'governance-agent', gmtCreate: 1, gmtModified: 1,
}

function LocationProbe() {
  const location = useLocation()
  return <span data-testid="location">{location.pathname}{location.search}</span>
}

describe('Insight improvement governance entry', () => {
  it('opens a local auto-repair authorization dialog and creates the task', async () => {
    mocks.improvements.mockResolvedValue({
      items: [improvement], nextCursor: null,
      statusCounts: { active: 1, inProgress: 0, resolved: 0, archived: 0 },
    })
    mocks.improvement.mockResolvedValue({ ...improvement, evidence: [], evolveLinks: [] })
    mocks.bots.mockResolvedValue({
      bots: [
        { botId: 'service-bot', displayBotId: 'service-bot', botName: '线上服务 Bot', status: 'active', source: 'test', activeEngine: 'openclaw', botType: 'service', deviceProvider: 'baas' },
        { botId: 'test-bot', displayBotId: 'test-bot', botName: '自动修复测试 Bot', status: 'active', source: 'test', activeEngine: 'openclaw', botType: 'personal', deviceProvider: 'baas' },
      ],
    })
    mocks.repairCreate.mockResolvedValue({ taskId: 'REPAIR-AUTO-1', status: 'pending' })

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/insight?tab=todo&improvementId=35']}>
        <ImprovementItems
          selectedImprovementId={35}
          botOptions={[{ botId: 'bot-source', botName: '来源 Bot' }]}
          onBotChange={() => {}}
          onSelectImprovement={() => {}}
          onGoFailures={() => {}}
        />
        <LocationProbe />
      </MemoryRouter>,
    )

    const authorization = await screen.findByRole('button', { name: '自动修复' })
    expect(screen.queryByRole('button', { name: /复制给 Agent/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '我已手动修复，开始验收' })).toBeInTheDocument()
    expect(screen.getByText(/如果你已经自行修复，也可以直接标记并进入 Agent 验收/)).toBeInTheDocument()
    expect(screen.getByText('处理原因')).toBeInTheDocument()
    expect(screen.getByText('授权后可自动修复')).toBeInTheDocument()
    expect(screen.queryByText('为什么找你')).not.toBeInTheDocument()
    expect(screen.queryByText('该问题需要当前处理人完成权限、配置或业务逻辑操作。')).not.toBeInTheDocument()

    await user.click(authorization)
    expect(await screen.findByRole('dialog', { name: '自动修复' })).toBeInTheDocument()
    expect(screen.getByText(/服务 Bot 不会出现在列表中，也不会被修改/)).toBeInTheDocument()
    const botSelect = screen.getByRole('combobox', { name: '测试 Bot' })
    expect(botSelect).toHaveTextContent('自动修复测试 Bot')
    expect(botSelect).not.toHaveTextContent('线上服务 Bot')
    await user.selectOptions(botSelect, 'test-bot')
    expect(screen.getByRole('radio', { name: /仅本次授权/ })).toBeChecked()
    // The improvement.source bot ('bot-source') is not in the available test-bot list, so the
    // dialog opens with an empty selection — the user must explicitly pick a test bot here.
    // Using selectOptions (HTML <select>) rather than click lets the change event fire
    // even when the source bot could not be auto-selected.
    await user.selectOptions(botSelect, 'test-bot')
    await user.click(screen.getByRole('radio', { name: /持续授权同类问题/ }))
    await user.click(screen.getByRole('button', { name: /持续授权并创建自动修复任务/ }))

    expect(mocks.repairCreate).toHaveBeenCalledWith(expect.objectContaining({
      botId: 'test-bot',
      insightImprovementId: 35,
      crossBotConfirmed: true,
      persistAutoRepairGrant: true,
    }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/evolve/repair-runs/REPAIR-AUTO-1'))
  })

  it('lets the owner skip Evolve after manually fixing an automatic improvement', async () => {
    const active = { ...improvement };
    const verifying = { ...improvement, status: 'IN_PROGRESS', handledAt: '2026-08-27T10:00:00Z', verificationStatus: 'PENDING', version: 2 };
    mocks.improvements.mockResolvedValue({
      items: [active], nextCursor: null,
      statusCounts: { active: 1, inProgress: 0, resolved: 0, archived: 0 },
    });
    mocks.improvement.mockResolvedValue({ ...active, evidence: [], evolveLinks: [] });
    mocks.markHandled.mockResolvedValue(verifying);

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/insight?tab=todo&improvementId=35']}>
        <ImprovementItems
          selectedImprovementId={35}
          botOptions={[{ botId: 'bot-source', botName: '来源 Bot' }]}
          onBotChange={() => {}}
          onSelectImprovement={() => {}}
          onGoFailures={() => {}}
        />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: '我已手动修复，开始验收' }));
    await waitFor(() => expect(mocks.markHandled).toHaveBeenCalledWith(35, 1));
    expect((await screen.findAllByText('自动验收中')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '自动修复' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '我已手动修复，开始验收' })).not.toBeInTheDocument();
  });

  it('hides the processing-reason card when Governance Agent did not provide a specific reason', async () => {
    const withoutReason = { ...improvement, assignmentReason: null }
    mocks.improvements.mockResolvedValue({
      items: [withoutReason], nextCursor: null,
      statusCounts: { active: 1, inProgress: 0, resolved: 0, archived: 0 },
    })
    mocks.improvement.mockResolvedValue({ ...withoutReason, evidence: [], evolveLinks: [] })

    render(
      <MemoryRouter>
        <ImprovementItems
          selectedImprovementId={35}
          botOptions={[{ botId: 'bot-source', botName: '来源 Bot' }]}
          onBotChange={() => {}}
          onSelectImprovement={() => {}}
          onGoFailures={() => {}}
        />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('button', { name: '自动修复' })).toBeInTheDocument()
    expect(screen.queryByText('处理原因')).not.toBeInTheDocument()
    expect(screen.queryByText('为什么找你')).not.toBeInTheDocument()
    expect(screen.queryByText('该问题需要当前处理人完成权限、配置或业务逻辑操作。')).not.toBeInTheDocument()
    expect(screen.getByText('更新 tools.md')).toBeInTheDocument()
  })

  it('shows an automatically continued item as repair in progress', async () => {
    const inProgress = {
      ...improvement,
      status: 'IN_PROGRESS',
      latestEvolveTaskId: 'EV-AUTO-PERSISTED-1',
      version: 2,
    }
    mocks.improvements.mockResolvedValue({
      items: [inProgress], nextCursor: null,
      statusCounts: { active: 0, inProgress: 1, resolved: 0, archived: 0 },
    })
    mocks.improvement.mockResolvedValue({ ...inProgress, evidence: [], evolveLinks: [] })

    render(
      <MemoryRouter initialEntries={['/insight?tab=todo&improvementId=35']}>
        <ImprovementItems
          selectedImprovementId={35}
          botOptions={[{ botId: 'bot-source', botName: '来源 Bot' }]}
          onBotChange={() => {}}
          onSelectImprovement={() => {}}
          onGoFailures={() => {}}
        />
      </MemoryRouter>,
    )

    expect((await screen.findAllByText('修复中')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '查看修复进度' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '自动修复' })).not.toBeInTheDocument()
  })
})
