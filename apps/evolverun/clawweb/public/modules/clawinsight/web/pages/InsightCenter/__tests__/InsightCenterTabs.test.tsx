import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import userEvent from '@testing-library/user-event'

const mocks = vi.hoisted(() => ({
  user: { userId: 'dev_local', isAdmin: false, isClawInsightAdmin: false as boolean | undefined },
  overview: vi.fn().mockResolvedValue({ dataAsOf: '2026-08-17T00:00:00Z', botComparison: [] }),
}))

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({
  useClientUser: () => ({ user: mocks.user, authState: 'ready' }),
}))

vi.mock('../../../api/insight', () => ({
  insightApi: {
    overview: mocks.overview,
  },
}))

vi.mock('../InsightOverview', () => ({ default: ({ scope }: { scope: { ownerUserId?: string } }) => <div>OVERVIEW_CONTENT:{scope.ownerUserId ?? 'mine'}</div> }))
vi.mock('../FailureTasks', () => ({ default: () => <div>EVIDENCE_CONTENT</div> }))
vi.mock('../ImprovementItems', () => ({ default: ({ ownerUserId, readOnly }: { ownerUserId?: string; readOnly?: boolean }) => <div>TODO_CONTENT:{ownerUserId ?? 'mine'}:{String(readOnly)}</div> }))
vi.mock('../AdminReviewQueue', () => ({
  default: ({ selectedImprovementId, onSelectImprovement }: { selectedImprovementId?: number; onSelectImprovement: (id: number) => void }) => <div>
    ADMIN_CONTENT:{selectedImprovementId ?? 'none'}
    <button onClick={() => onSelectImprovement(88)}>OPEN_ADMIN_IMPROVEMENT</button>
  </div>,
}))

vi.mock('../monitoring/MonitoringPanel', () => ({ default: () => <div>MONITORING_CONTENT</div> }))
vi.mock('../monitoring/session-recovery/SessionRecoveryList', () => ({ SessionRecoveryList: () => <div>RECOVERY_LIST</div> }))
vi.mock('../monitoring/MonitoringSettings', () => ({ MonitoringSettings: ({ onClose }: { onClose: () => void }) =>
  <div role="dialog" aria-label="诊断监控设置"><button onClick={onClose}>关闭设置</button></div> }))

import InsightCenter from '../index'

beforeEach(() => {
  mocks.user.userId = 'dev_local'
  mocks.user.isAdmin = false
  mocks.user.isClawInsightAdmin = false
  mocks.overview.mockClear()
})

describe('Insight Center tabs', () => {
  it('opens the overview by default and orders overview, evidence, then todo', async () => {
    mocks.user.isAdmin = false
    render(<MemoryRouter initialEntries={['/insight']}><InsightCenter /></MemoryRouter>)

    expect(screen.getByText('OVERVIEW_CONTENT:mine')).toBeInTheDocument()
    const labels = ['效果概览', '问题证据', '我的待办'].map((label) => screen.getByText(label).closest('button'))
    expect(labels.every(Boolean)).toBe(true)
    expect(labels[0]!.compareDocumentPosition(labels[1]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(labels[1]!.compareDocumentPosition(labels[2]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shares the Admin owner scope across overview, evidence, and todo', async () => {
    mocks.user.isAdmin = true
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/insight?ownerUserId=*']}><InsightCenter /></MemoryRouter>)

    expect(await screen.findByText('OVERVIEW_CONTENT:*')).toBeInTheDocument()
    expect(screen.getByText('统一数据视角')).toBeInTheDocument()
    expect(screen.getByText('当前查看：').parentElement).toHaveTextContent('全部用户')

    await user.click(screen.getByText('我的待办'))
    expect(screen.getByText('TODO_CONTENT:*:true')).toBeInTheDocument()
  })

  it('keeps the selected Admin improvement in the URL-backed tab state', async () => {
    mocks.user.isAdmin = true
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/insight?tab=admin']}><InsightCenter /></MemoryRouter>)

    expect(screen.getByText('ADMIN_CONTENT:none')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'OPEN_ADMIN_IMPROVEMENT' }))
    expect(screen.getByText('ADMIN_CONTENT:88')).toBeInTheDocument()
  })
})


describe('Insight monitoring module navigation', () => {
  it('opens settings from the invitation URL and clears the flag when settings close', async () => {
    function Location() { return <output>{useLocation().search}</output> }
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/insight?module=monitoring&openMonitoringSettings=1']}><InsightCenter /><Location /></MemoryRouter>)
    expect(screen.getByRole('dialog', { name: '诊断监控设置' })).toBeInTheDocument()
    expect(screen.getByText('MONITORING_CONTENT')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '关闭设置' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/^\?module=monitoring$/)
  })
  it('isolates monitoring from governance loading and preserves the governance deep link', async () => {
    mocks.overview.mockClear()
    mocks.user.isAdmin = true
    mocks.user.isClawInsightAdmin = true
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/insight?module=monitoring&tab=admin&improvementId=88&ownerUserId=*']}><InsightCenter /></MemoryRouter>)
    expect(screen.getByText('MONITORING_CONTENT')).toBeInTheDocument()
    expect(mocks.overview).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Agent 治理' }))
    expect(screen.getByText('ADMIN_CONTENT:88')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '诊断自愈' }))
    expect(screen.getByText('MONITORING_CONTENT')).toBeInTheDocument()
    expect(screen.queryByText('ADMIN_CONTENT:88')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '诊断', exact: true })).toHaveAttribute('aria-current', 'page')
    await user.click(screen.getByRole('button', { name: '自愈', exact: true }))
    expect(screen.getByRole('button', { name: '自愈', exact: true })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('RECOVERY_LIST')).toBeInTheDocument()
    expect(screen.queryByText('MONITORING_CONTENT')).not.toBeInTheDocument()
  })
})

describe('Monitoring member navigation boundary', () => {
  it.each([false, undefined])('allows authenticated members with admin flag %s', (flag) => {
    mocks.user.isAdmin = true
    mocks.user.isClawInsightAdmin = flag
    render(<MemoryRouter initialEntries={['/insight?module=monitoring']}><InsightCenter /></MemoryRouter>)
    expect(screen.getByRole('button', { name: '诊断自愈' })).toBeInTheDocument()
    expect(screen.getByText('MONITORING_CONTENT')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Agent 治理' })).toBeInTheDocument()
    expect(mocks.overview).not.toHaveBeenCalled()
  })
  it('permits a monitoring-only member without generic admin privileges', () => {
    mocks.user.isClawInsightAdmin = true
    render(<MemoryRouter initialEntries={['/insight?module=monitoring']}><InsightCenter /></MemoryRouter>)
    expect(screen.getByText('MONITORING_CONTENT')).toBeInTheDocument()
    expect(mocks.overview).not.toHaveBeenCalled()
  })
  it('does not expose the module to an unidentified browser user', () => {
    mocks.user.userId = ''
    render(<MemoryRouter initialEntries={['/insight?module=monitoring']}><InsightCenter /></MemoryRouter>)
    expect(screen.queryByText('MONITORING_CONTENT')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '诊断自愈' })).not.toBeInTheDocument()
  })

})
