import { screen, waitFor, within } from '@testing-library/react'
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
function mount({ readOnly = false, items = [repairItem()], tasks = [] as Array<{taskId:string;revision:number;phase:string;updatedAtMs:number;itemCount:number}> } = {}) {
  const detail = published()
  Object.assign(repairApi, { verification: vi.fn().mockRejectedValue(new Error('observation unavailable')) })
  repairApi.task.mockImplementation(id => id === 'FIX-old' ? Promise.resolve(detail) : new Promise(() => {}))
  repairApi.candidates.mockResolvedValue({ ...repairPage({ items, tasks }), canEdit: !readOnly })
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
it('keeps the chosen suggestions after a definite baseline read failure without asking to check a nonexistent task', async () => {
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: '开始下一轮优化' }))
  const checkbox = await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
  await user.click(checkbox)
  await user.type(screen.getByRole('textbox', { name: '本次修复要求' }), '保留业务约束')
  repairApi.create.mockRejectedValueOnce({ status: 503, body: JSON.stringify({ code: 'CAPABILITY_UNAVAILABLE',
    reasonCode: 'BASELINE_READ_FAILED', stage: 'baseline', outcome: 'not_created' }) })
  await user.click(screen.getByRole('button', { name: '生成 Pack 草稿' }))
  expect(await screen.findByText(/草稿未创建/)).toBeVisible()
  expect(checkbox).toBeChecked()
  expect(screen.getByRole('textbox', { name: '本次修复要求' })).toHaveValue('保留业务约束')
  expect(screen.queryByRole('button', { name: '核对任务状态' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeEnabled()
  expect(repairApi.create).toHaveBeenCalledTimes(1)
})
it('keeps the actual baseline rejection and reviewed selection without refreshing or resending', async () => {
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: '开始下一轮优化' }))
  const checkbox = await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
  await user.click(checkbox)
  await user.type(screen.getByRole('textbox', { name: '本次修复要求' }), '保留业务约束')
  repairApi.create.mockRejectedValueOnce({ status: 409, body: JSON.stringify({ error: '原建议基线已变化，请重新评估', code: 'SOURCE_CHANGED', requestId: '20023274-6e68-4535-a90a-e36a7c10df49' }) })
  await user.click(screen.getByRole('button', { name: '生成 Pack 草稿' }))
  expect(await screen.findByText(/原建议基线已变化，请核对配置差异/)).toBeVisible()
  expect(checkbox).toBeChecked()
  expect(screen.getByRole('textbox', { name: '本次修复要求' })).toHaveValue('保留业务约束')
  expect(repairApi.create).toHaveBeenCalledTimes(1)
  expect(repairApi.selection).not.toHaveBeenCalled()
})
async function createChild(tasks: Array<{taskId:string;revision:number;phase:string;updatedAtMs:number;itemCount:number}> = []) {
  const view = mount({ tasks })
  const entry = await screen.findByRole('button', { name: '开始下一轮优化' })
  await waitFor(() => expect(entry).toBeEnabled());await userEvent.click(entry)
  await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
  await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿' }))
  await screen.findByRole('button', { name: '返回上一轮观察', exact: true })
  return view
}
it('restores the exact parent/child observation link after reloading the child task', async () => {
  const first = await createChild()
  first.unmount()
  render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: '返回上一轮观察', exact: true }))
  expect(await screen.findByText(/已发布部署 #160/)).toBeVisible()
  expect(screen.getByRole('combobox', { name: '查看修复版本' })).toHaveValue('1')
})
it('clears the lineage when returning to the issue list before opening an unrelated task', async () => {
  await createChild([{ taskId: 'FIX-unrelated', revision: 1, phase: 'review', updatedAtMs: 1, itemCount: 1 }])
  await userEvent.click(screen.getByRole('button', { name: /返回问题与优化/ }))
  await userEvent.click(within(await screen.findByRole('region', { name: '问题与优化任务入口' })).getByRole('button', { name: '查看本轮任务' }))
  await waitFor(() => expect(repairApi.task).toHaveBeenCalledWith('FIX-unrelated'))
  expect(screen.queryByRole('button', { name: '返回上一轮观察', exact: true })).not.toBeInTheDocument()
  expect(JSON.parse(sessionStorage.getItem('workflow-repair:wf-1')!)).not.toHaveProperty('lineage')
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
it('keeps the affected-item reason visible after the automatic refresh rechecks the remaining valid selection', async () => {
  const a = repairItem('a', '目标 A'), b = repairItem('b', '目标 B'), revised = repairItem('a', '目标 A 已变化')
  mount({ items: [a, b] })
  let changed = false
  repairApi.candidates.mockImplementation(() => Promise.resolve({ ...repairPage({ items: changed ? [revised, b] : [a, b] }), inputDigest: (changed ? 'f' : 'c').repeat(64) }))
  repairApi.selection.mockImplementation((_wf, ids: string[]) => Promise.resolve({ inputDigest: 'f'.repeat(64), items: [revised, b].filter(item => ids.includes(item.itemId)), missing: [], canEdit: true }))
  repairApi.create.mockImplementation(() => { changed = true; return Promise.reject({ status: 409, body: JSON.stringify({ code: 'SOURCE_CHANGED', reasonCode: 'SELECTED_ITEMS_CHANGED' }) }) })
  const entry = await screen.findByRole('button', { name: '开始下一轮优化' })
  await waitFor(() => expect(entry).toBeEnabled());await userEvent.click(entry)
  await userEvent.click(await screen.findByRole('checkbox', { name: '选择 目标 A' }))
  await userEvent.click(screen.getByRole('checkbox', { name: '选择 目标 B' }))
  await userEvent.type(screen.getByRole('textbox', { name: '本次修复要求' }), '保留要求')
  await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿' }))
  await waitFor(() => expect(repairApi.selection.mock.calls.length).toBeGreaterThanOrEqual(2))
  expect(await screen.findByText(/目标 A：建议内容已变化，请审阅后重新勾选/)).toBeVisible()
  expect(screen.getByRole('checkbox', { name: '选择 目标 B' })).toBeChecked()
  expect(screen.getByRole('textbox', { name: '本次修复要求' })).toHaveValue('保留要求')
  expect(repairApi.create).toHaveBeenCalledTimes(1)
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
