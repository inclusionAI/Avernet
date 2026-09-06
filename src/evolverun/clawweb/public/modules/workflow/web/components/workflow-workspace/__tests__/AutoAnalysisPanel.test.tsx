import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const { update } = vi.hoisted(() => ({ update: vi.fn() }))

vi.mock('../../../api/hooks', () => ({
  useWorkflowAutoAnalysis: () => ({
    data: { workflowId: 'wf-1', enabled: false, source: 'default' },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateWorkflowAutoAnalysis: () => ({ mutate: update, isPending: false }),
}))

import AutoAnalysisPanel from '../AutoAnalysisPanel'

describe('automatic failed-run analysis settings', () => {
  it('lets an editor enable automatic analysis for the current workflow', async () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)

    expect(screen.getByText('失败后自动分析')).toBeInTheDocument()
    expect(screen.getByText(/只创建分析任务，不会自动应用建议或部署/)).toBeInTheDocument()
    const toggle = screen.getByRole('switch', { name: '失败后自动分析' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')

    await userEvent.click(toggle)

    expect(update).toHaveBeenCalledWith(
      { workflowId: 'wf-1', enabled: true },
      expect.objectContaining({ onError: expect.any(Function) }),
    )
  })
})
