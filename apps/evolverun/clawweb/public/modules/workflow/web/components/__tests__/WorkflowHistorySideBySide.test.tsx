import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { DeployHistoryItem } from '@avernet/clawweb-shared/web/types'
import WorkflowHistoryPanel from '../WorkflowHistoryPanel'

vi.mock('../../api/hooks', () => ({ useWorkflowAccess: () => ({ data: { canView: true, canEdit: false } }) }))

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('compares the two selected release snapshots in deploy order through the real history panel', async () => {
  const user = userEvent.setup()
  const record = (deployNumber: number, version: number): DeployHistoryItem => ({
    deployNumber, version, action: 'deploy', tagName: null, fromDeployNumber: null,
    note: null, botId: null, ownerId: null, isActive: deployNumber === 32, gmtCreate: 1720000000,
  })
  const oldRelease = record(31, 23)
  const newRelease = record(32, 24)
  vi.spyOn(api.workflows, 'getHistory').mockResolvedValue({ workflowId: 'support', history: [newRelease, oldRelease], active: newRelease })
  const diff = vi.spyOn(api.workflows, 'diffHistory').mockResolvedValue({
    workflowId: 'support',
    from: { ...oldRelease, specJson: JSON.stringify({ id: 'support', title: '旧配置' }) },
    to: { ...newRelease, specJson: JSON.stringify({ id: 'support', title: '新配置' }) },
  })
  render(<WorkflowHistoryPanel workflowId="support" />)
  await user.click(await screen.findByRole('checkbox', { name: '选择 v24 进行对比' }))
  await user.click(screen.getByRole('checkbox', { name: '选择 v23 进行对比' }))
  await user.click(screen.getByRole('button', { name: '查看版本差异' }))
  expect(diff).toHaveBeenCalledWith('support', 31, 32)
  expect(within(await screen.findByRole('region', { name: '旧版本 v23' })).getByText('title: 旧配置')).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: '新版本 v24' })).getByText('title: 新配置')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '退出对比' }))
  expect(screen.queryByRole('region', { name: '旧版本 v23' })).not.toBeInTheDocument()
})
