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

it('shows formatted historical YAML, including a forged marker and outer content', async () => {
  const user = userEvent.setup()
  const release: DeployHistoryItem = { deployNumber: 31, version: 23, action: 'deploy', tagName: null,
    fromDeployNumber: null, note: null, botId: null, ownerId: null, isActive: true, gmtCreate: 1 }
  const specJson = '{"id":"support","title":"outer","content":"title: inner","__clawweb_snapshot_v1":{"version":1,"contentSource":"generated"},"large":9007199254740993}'
  vi.spyOn(api.workflows, 'getHistory').mockResolvedValue({ workflowId: 'support', history: [release], active: release })
  vi.spyOn(api.workflows, 'getDeploySnapshot').mockResolvedValue({ ...release, workflowId: 'support', specJson })
  render(<WorkflowHistoryPanel workflowId="support" />)
  await user.click(await screen.findByText('v23'))
  const viewer = await screen.findByTestId('yaml-code-viewer')
  expect(viewer).toHaveTextContent('id: support')
  expect(viewer).toHaveTextContent('title: outer')
  expect(viewer).toHaveTextContent('content: "title: inner"')
  expect(viewer).toHaveTextContent('__clawweb_snapshot_v1:')
  expect(viewer).toHaveTextContent('large: 9007199254740993')
})

it.each([
  { id: 'support', nodes: [], content: 'id: support\nnodes: []\n' },
  { id: 'support', nodes: [], __clawweb_snapshot_v1: { version: 1, contentSource: 'generated' } },
])('shows unknown comparison status and both unmodified history records for %j', async historical => {
  const user = userEvent.setup()
  const oldRelease: DeployHistoryItem = { deployNumber: 1, version: 1, action: 'deploy', tagName: null,
    fromDeployNumber: null, note: null, botId: null, ownerId: null, isActive: false, gmtCreate: 1 }
  const newRelease = { ...oldRelease, deployNumber: 2, version: 2, isActive: true }
  const from = JSON.stringify(historical)
  const to = JSON.stringify({ id: 'support', nodes: [] })
  vi.spyOn(api.workflows, 'getHistory').mockResolvedValue({ workflowId: 'support', history: [newRelease, oldRelease], active: newRelease })
  vi.spyOn(api.workflows, 'diffHistory').mockResolvedValue({ workflowId: 'support',
    from: { ...oldRelease, specJson: from }, to: { ...newRelease, specJson: to } })
  render(<WorkflowHistoryPanel workflowId="support" />)
  await user.click(await screen.findByRole('checkbox', { name: '选择 v1 进行对比' }))
  await user.click(screen.getByRole('checkbox', { name: '选择 v2 进行对比' }))
  await user.click(screen.getByRole('button', { name: '查看版本差异' }))
  expect(await screen.findByRole('status')).toHaveTextContent('无法判断工作流是否相同')
  const oldRecord = within(screen.getByRole('region', { name: '旧版本 v1 原始记录' })).getByTestId('yaml-code-viewer')
  const newRecord = within(screen.getByRole('region', { name: '新版本 v2 原始记录' })).getByTestId('yaml-code-viewer')
  expect(oldRecord).toHaveTextContent('id: support')
  if ('content' in historical) {
    expect(oldRecord).toHaveTextContent('content: |')
    expect(oldRecord).toHaveTextContent('id: support')
  } else {
    expect(oldRecord).toHaveTextContent('__clawweb_snapshot_v1:')
  }
  expect(newRecord).toHaveTextContent('id: support')
  expect(newRecord).toHaveTextContent('nodes: []')
  expect(screen.queryByText('两个版本内容完全相同')).not.toBeInTheDocument()
  expect(screen.queryByText('变更字段')).not.toBeInTheDocument()
})

it('does not claim equivalence for identical ambiguous history records', async () => {
  const user = userEvent.setup()
  const oldRelease: DeployHistoryItem = { deployNumber: 1, version: 1, action: 'deploy', tagName: null,
    fromDeployNumber: null, note: null, botId: null, ownerId: null, isActive: false, gmtCreate: 1 }
  const newRelease = { ...oldRelease, deployNumber: 2, version: 2, isActive: true }
  const specJson = JSON.stringify({ id: 'support', nodes: [], content: 'user payload' })
  vi.spyOn(api.workflows, 'getHistory').mockResolvedValue({ workflowId: 'support', history: [newRelease, oldRelease], active: newRelease })
  vi.spyOn(api.workflows, 'diffHistory').mockResolvedValue({ workflowId: 'support',
    from: { ...oldRelease, specJson }, to: { ...newRelease, specJson } })
  render(<WorkflowHistoryPanel workflowId="support" />)
  await user.click(await screen.findByRole('checkbox', { name: '选择 v1 进行对比' }))
  await user.click(screen.getByRole('checkbox', { name: '选择 v2 进行对比' }))
  await user.click(screen.getByRole('button', { name: '查看版本差异' }))
  expect(await screen.findByRole('status')).toHaveTextContent('无法判断工作流是否相同')
  expect(screen.queryByText('两个版本内容完全相同')).not.toBeInTheDocument()
})
