import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { render, repairApi, repairPage, openIssueRepairs, closeIssue } from './evolution-issue-fixture'
import EvolutionTab from '../EvolutionTab'

describe('repair selection workspace', () => {
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

  it('shows disposition confirmation inside the selection workspace', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
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
    await userEvent.click(within(dialog).getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(2))
    expect(repairApi.create.mock.calls[1][0].requestId).toBe(firstId)
    await userEvent.type(instructions, '，并增加超时')
    await userEvent.click(within(dialog).getByRole('button', { name: '生成 Pack 草稿' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(3))
    expect(repairApi.create.mock.calls[2][0].requestId).not.toBe(firstId)
  })

})
