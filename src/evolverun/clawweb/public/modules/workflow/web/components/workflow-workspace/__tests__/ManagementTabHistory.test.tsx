import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const { historyQuery } = vi.hoisted(() => ({
  historyQuery: vi.fn(),
}))

vi.mock('../../../api/hooks', () => ({
  useWorkflowHistory: (workflowId: string) => historyQuery(workflowId),
  useWorkflowAccess: () => ({ data: { canEdit: true }, isLoading: false }),
  useFacadeBindings: () => ({ data: [], refetch: vi.fn() }),
  useDeleteWorkflow: () => ({ isPending: false, mutate: vi.fn() }),
  useRestoreWorkflowVersion: () => ({ isPending: false, mutate: vi.fn() }),
}))

vi.mock('../../PermissionPanel', () => ({ default: () => <div>权限设置</div> }))
vi.mock('../../FacadePanel', () => ({ default: () => <div>命令设置</div> }))
vi.mock('../../NotificationPanel', () => ({ default: () => <div>通知设置</div> }))
vi.mock('../../HttpCallbackPanel', () => ({ default: () => <div>回调设置</div> }))
vi.mock('../AutoAnalysisPanel', () => ({ default: () => <div>自动分析设置</div> }))

import ManagementTab from '../ManagementTab'

const history = Array.from({ length: 8 }, (_, index) => ({
  deployNumber: 8 - index,
  version: 8 - index,
  tagName: null,
  action: 'deploy',
  fromDeployNumber: null,
  note: null,
  botId: null,
  ownerId: 'owner',
  isActive: index === 0,
  gmtCreate: 1_700_000_000 - index,
}))

describe('workflow management version history', () => {
  it('exposes automatic analysis in workflow settings', async () => {
    historyQuery.mockReturnValue({
      data: { history: [] }, isLoading: false, isError: false, error: null, refetch: vi.fn(),
    })
    render(<ManagementTab workflowId="wf-1" workflowTitle="Workflow 1" onDeleted={vi.fn()} />)

    await userEvent.click(screen.getByRole('button', { name: '自动分析' }))

    expect(screen.getByText('自动分析设置')).toBeInTheDocument()
  })

  it('shows recent versions first and lets users expand and collapse older versions', async () => {
    historyQuery.mockReturnValue({
      data: { history },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })

    render(<ManagementTab workflowId="wf-1" workflowTitle="Workflow 1" onDeleted={vi.fn()} />)

    expect(screen.getByText('v8 · deploy #8')).toBeInTheDocument()
    expect(screen.getByText('v4 · deploy #4')).toBeInTheDocument()
    expect(screen.queryByText('v3 · deploy #3')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开其余 3 个版本' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '展开其余 3 个版本' }))

    expect(screen.getByText('v1 · deploy #1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '收起历史版本' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '收起历史版本' }))

    expect(screen.queryByText('v3 · deploy #3')).not.toBeInTheDocument()
  })
})
