import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'

const mocks = vi.hoisted(() => ({
  user: { userId: 'dev_local', isAdmin: false },
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

import InsightCenter from '../index'

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
