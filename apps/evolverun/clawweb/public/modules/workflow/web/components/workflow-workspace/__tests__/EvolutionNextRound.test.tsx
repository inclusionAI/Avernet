import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import { render, repairApi, repairItem, repairPage, lifecycle } from './evolution-issue-fixture'
import EvolutionTab from '../EvolutionTab'

function published(): RepairTaskDetail {
  const revision = { workflowId: 'wf-1', taskId: 'FIX-old', revision: 1, phase: 'published',
    input: { items: [repairItem('adopted')], instructions: '上一轮要求', baseline: { activeDeployNumber: 159 } }, draft: null, checks: null, error: null }
  return { workflowId: 'wf-1', taskId: 'FIX-old', latestAttempt: { ...revision, revision: 2, phase: 'no_change' },
    latestSuccessful: revision, revisions: [revision], capabilities: { generation: true },
    release: { deployments: [{ id: 'dep-old', revision: 1, status: 'succeeded', receipt: { deployNumber: 160 } }] },
  } as unknown as RepairTaskDetail
}
function mount({ readOnly = false, items = [repairItem()] } = {}) {
  const detail = published()
  Object.assign(repairApi, { verification: vi.fn().mockRejectedValue(new Error('observation unavailable')) })
  repairApi.task.mockImplementation(id => id === 'FIX-old' ? Promise.resolve(detail) : new Promise(() => {}))
  repairApi.candidates.mockResolvedValue({ ...repairPage({ items }), canEdit: !readOnly })
  sessionStorage.setItem('workflow-repair:wf-1', JSON.stringify({ selectedTaskId: 'FIX-old' }))
  return render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
}

it('opens a fresh new-task selection from published v1/latest no-change v2, preserves observation and only creates on submit', async () => {
  mount({ items: [repairItem(), { ...repairItem('adopted', '上一轮已采纳建议'), state: 'awaiting_verification' }] })
  const user = userEvent.setup()
  await screen.findByText(/已发布部署 #160 · 观察读取失败/)
  await user.selectOptions(screen.getByRole('combobox', { name: '查看修复版本' }), '2')
  await user.click(await screen.findByRole('button', { name: '开始下一轮优化' }))
  expect(await screen.findByText(/将创建独立新任务/)).toBeVisible()
  expect(screen.getByRole('textbox', { name: '本次修复要求' })).toHaveValue('')
  const checkbox = await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
  expect(checkbox).not.toBeChecked()
  expect(screen.getByRole('checkbox', { name: '选择 上一轮已采纳建议' })).toBeDisabled()
  expect(repairApi.create).not.toHaveBeenCalled()
  expect(repairApi.revise).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: /返回上一轮观察/ }))
  await screen.findByText(/已发布部署 #160 · 观察读取失败/)
  expect(screen.getByRole('tab', { name: '效果观察' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('combobox', { name: '查看修复版本' })).toHaveValue('1')
  await user.click(screen.getByRole('button', { name: '开始下一轮优化' }))
  await user.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeEnabled())
  await user.dblClick(screen.getByRole('button', { name: '生成 Pack 草稿' }))
  await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(1))
  expect(repairApi.create.mock.calls[0][0]).toMatchObject({ workflowId: 'wf-1', itemIds: ['item-1'], instructions: '' })
  expect(repairApi.create.mock.calls[0][0]).not.toHaveProperty('expectedAttemptRevision')
  expect(repairApi.revise).not.toHaveBeenCalled()
  await waitFor(() => expect(repairApi.task).toHaveBeenCalledWith('FIX-2'))
})

it('disables the new round entry for a read-only viewer', async () => {
  mount({ readOnly: true })
  expect(await screen.findByRole('button', { name: '开始下一轮优化' })).toBeDisabled()
})

it('blocks selection and submission when edit permission is revoked after entering', async () => {
  const view = mount()
  const entry = await screen.findByRole('button', { name: '开始下一轮优化' })
  await waitFor(() => expect(entry).toBeEnabled())
  await userEvent.click(entry)
  const item = await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
  await userEvent.click(item)
  lifecycle.canEdit = false
  view.rerender(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
  expect(item).toBeDisabled()
  expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeDisabled()
  expect(repairApi.create).not.toHaveBeenCalled()
})

it.each([false, true])('keeps empty/error selection blocked and retains the return path (error=%s)', async readError => {
  mount({ items: [] })
  const entry = await screen.findByRole('button', { name: '开始下一轮优化' })
  await waitFor(() => expect(entry).toBeEnabled())
  if (readError) repairApi.candidates.mockRejectedValue(new Error('selection unavailable'))
  await userEvent.click(entry)
  await waitFor(() => expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeDisabled())
  expect(screen.getByRole('button', { name: /返回上一轮观察/ })).toBeEnabled()
  if (readError) {
    const retry = await screen.findByRole('button', { name: '重试读取', exact: true })
    repairApi.candidates.mockResolvedValue(repairPage())
    await userEvent.click(retry)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeEnabled())
  }
  else expect(await screen.findByText('当前范围没有匹配建议。')).toBeVisible()
  expect(repairApi.create).not.toHaveBeenCalled()
})
