import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import RemediesPanel from '../../RemediesPanel'
const state = vi.hoisted(() => ({ isLoading: false, isError: true, data: undefined, refetch: vi.fn() }))
vi.mock('../../../../api/hooks', () => ({ useEvolveLessons: () => state }))
describe('experience read failures', () => {
  it('offers retry instead of claiming a failed read means no experience', async () => {
    render(<RemediesPanel workflowId="wf" />)
    expect(screen.queryByText('还没有可复用经验')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('经验加载失败')
    await userEvent.click(screen.getByRole('button', { name: '重试经验' }))
    expect(state.refetch).toHaveBeenCalledOnce()
  })
})
