import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const { update } = vi.hoisted(() => ({ update: vi.fn() }))

vi.mock('../../../api/workflow-automation', () => ({
  useAutomationPolicy: () => ({
    data: { workflowId: 'wf-1', enabled: false, autoDraft: false, dailyTime: '09:00', version: 0, dailyConfigured: false, source: 'default' },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useSaveAutomationPolicy: () => ({ mutate: update, isPending: false }),
  useAutomationStatus: () => ({ data: { jobs: [] }, isError: false, refetch: vi.fn() }),
  retryAutomationFailures: vi.fn(),
}))

import AutoAnalysisPanel from '../AutoAnalysisPanel'

describe('automatic failed-run analysis settings', () => {
  it('lets an editor enable automatic analysis for the current workflow', async () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)

    expect(screen.getByText('自动分析')).toBeInTheDocument()
    expect(screen.getByText(/不会自动部署/)).toBeInTheDocument()
    const toggle = screen.getByRole('switch', { name: '自动分析' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')

    await userEvent.click(toggle)

    expect(update).toHaveBeenCalledWith(
      { enabled: true, autoDraft: false, dailyTime: '09:00', expectedVersion: 0 },
      expect.objectContaining({ onError: expect.any(Function) }),
    )
    expect(screen.getByLabelText('每日执行时间')).toBeDisabled()
    expect(screen.getByLabelText('自动生成优化草稿')).toBeDisabled()
  })
})
