import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { render, repairApi, repairItem, repairPage, openIssueRepairs, closeIssue } from './evolution-issue-fixture'
import EvolutionTab from '../EvolutionTab'

describe('repair selection workspace', () => {
  it('finds a committed task after losing the create response without creating another task', async () => {
    let committed = false
    repairApi.candidates.mockImplementation(() => Promise.resolve(repairPage({ tasks: committed
      ? [{ taskId: 'FIX-recovered', revision: 1, phase: 'drafting', updatedAtMs: 1, itemCount: 1 }] : [] })))
    repairApi.create.mockImplementation(() => { committed = true; return Promise.reject(new Error('response lost')) })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成修复草稿（1）' }))
    await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeDisabled())
    await userEvent.click(screen.getByRole('button', { name: '核对任务状态', exact: true }))
    await waitFor(() => expect(repairApi.task).toHaveBeenCalledWith('FIX-recovered'))
    expect(repairApi.create).toHaveBeenCalledTimes(1)
  })

  it('keeps generation blocked when checking a lost create response fails', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.create.mockRejectedValue(new Error('response lost'))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成修复草稿（1）' }))
    await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿' }))
    const recovery = await screen.findByRole('button', { name: '核对任务状态', exact: true })
    repairApi.candidates.mockRejectedValueOnce(new Error('read unavailable'))
    await userEvent.click(recovery)
    await waitFor(() => expect(recovery).toBeEnabled())
    expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeDisabled()
    expect(repairApi.create).toHaveBeenCalledTimes(1)
  })

  it('reloads changed sources and requires a new selection without losing requirements', async () => {
    let changed = false
    repairApi.candidates.mockImplementation(() => Promise.resolve({ ...repairPage(), inputDigest: (changed ? 'f' : 'c').repeat(64) }))
    repairApi.create.mockImplementationOnce(() => {
      changed = true
      return Promise.reject({ status: 409, body: JSON.stringify({ code: 'SOURCE_CHANGED' }) })
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成修复草稿（1）' }))
    await userEvent.type(screen.getByRole('textbox', { name: '本次修复要求' }), '保留原分支')
    await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })).not.toBeChecked())
    expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: '本次修复要求' })).toHaveValue('保留原分支')
    expect(repairApi.create).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })).toBeEnabled())
    await userEvent.click(screen.getByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(2))
    expect(repairApi.create.mock.calls[1][0]).toMatchObject({ inputDigest: 'f'.repeat(64), instructions: '保留原分支' })
    expect(repairApi.create.mock.calls[1][0].requestId).not.toBe(repairApi.create.mock.calls[0][0].requestId)
  })

  it('creates a Pack draft when no active repair task exists', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成修复草稿（1）' }))
    const dialog = screen.getByRole('region', { name: 'Workflow 修复工作区' })
    expect(within(dialog).getByText(/先改成草稿，不直接部署/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledWith(expect.objectContaining({
      workflowId: 'wf-1', itemIds: ['item-1'], inputDigest: 'c'.repeat(64),
    })))
    expect(await screen.findByRole('region', { name: 'Workflow 修复工作区' })).toBeVisible()
    expect(repairApi.task).toHaveBeenCalledWith('FIX-2')
  })

  it('confirms disposition and refreshes the selection row without allowing stale reselection', async () => {
    let disposed = false
    repairApi.candidates.mockImplementation(() => Promise.resolve(repairPage({
      items: [{ ...repairItem(), state: disposed ? 'no_action' : 'pending' }],
    })))
    repairApi.disposition.mockImplementation(() => { disposed = true; return Promise.resolve({}) })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成修复草稿（1）' }))
    await userEvent.click(await screen.findByRole('button', { name: '查看问题与证据 ↗' }))
    await userEvent.click(await screen.findByRole('button', { name: '暂不处理 将超时阈值调整为 90 秒' }))
    const confirm = screen.getByRole('dialog', { name: '暂不处理' })
    await userEvent.type(within(confirm).getByRole('textbox', { name: '处置原因' }), '保留现有规则')
    await userEvent.click(within(confirm).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(repairApi.disposition).toHaveBeenCalledWith('item-1', expect.objectContaining({ action: 'no_action' })))
    expect(await screen.findByText('已暂不处理，可恢复后重新选择')).toBeVisible()
    const checkbox = screen.getByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
    expect(checkbox).not.toBeChecked()
    expect(checkbox).toBeDisabled()
    expect(screen.getByRole('button', { name: '生成 Pack 草稿' })).toBeDisabled()
  })

  it('rejects an oversized Pack draft before posting it', async () => {
    repairApi.candidates.mockResolvedValue({ ...repairPage(), limits: { maxItems: 100, maxRequestBytes: 300 } })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())
    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成修复草稿（1）' }))
    const dialog = screen.getByRole('region', { name: 'Workflow 修复工作区' })
    await userEvent.type(within(dialog).getByRole('textbox', { name: '本次修复要求' }), '修'.repeat(100))
    await userEvent.click(within(dialog).getByRole('button', { name: '生成 Pack 草稿' }))

    expect(within(dialog).getByRole('alert')).toHaveTextContent('请求内容过大，请缩短说明或减少选择。')
    expect(repairApi.create).not.toHaveBeenCalled()
  })

  it('reuses a draft request ID only for an exact retry', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.create.mockRejectedValueOnce(new Error('response lost'))
      .mockRejectedValueOnce(new Error('response lost again'))
      .mockResolvedValueOnce({ taskId: 'FIX-2', revision: 1 })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalled())

    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成修复草稿（1）' }))
    const dialog = screen.getByRole('region', { name: 'Workflow 修复工作区' })
    const instructions = within(dialog).getByRole('textbox', { name: '本次修复要求' })
    await userEvent.type(instructions, '保留现有分支')
    await userEvent.click(within(dialog).getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(1))
    const firstId = repairApi.create.mock.calls[0][0].requestId
    await userEvent.click(within(dialog).getByRole('button', { name: '核对任务状态', exact: true }))
    await waitFor(() => expect(within(dialog).getByRole('button', { name: '生成 Pack 草稿' })).toBeEnabled())
    await userEvent.click(within(dialog).getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(2))
    expect(repairApi.create.mock.calls[1][0].requestId).toBe(firstId)
    await userEvent.click(within(dialog).getByRole('button', { name: '核对任务状态', exact: true }))
    await waitFor(() => expect(within(dialog).getByRole('button', { name: '生成 Pack 草稿' })).toBeEnabled())
    await userEvent.type(instructions, '，并增加超时')
    await userEvent.click(within(dialog).getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(3))
    expect(repairApi.create.mock.calls[2][0].requestId).not.toBe(firstId)
  })

})
