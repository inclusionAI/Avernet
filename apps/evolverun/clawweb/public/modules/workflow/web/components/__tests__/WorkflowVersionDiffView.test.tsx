// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { VersionDiffResult } from '@avernet/clawweb-shared/web/types'
import WorkflowVersionDiff from '../WorkflowVersionDiff'

const sharedFields = Object.fromEntries(
  Array.from({ length: 12 }, (_, index) => [`setting${index + 1}`, `unchanged-${index + 1}`]),
)

const comparison: VersionDiffResult = {
  workflowId: 'support',
  from: {
    version: 23, deployNumber: 31, action: 'deploy', gmtCreate: 1720000000,
    specJson: JSON.stringify({
      id: 'support', title: '旧工单处理', ...sharedFields,
      owner: 'old-owner', nodes: [{ id: 'start', type: 'start' }],
    }),
  },
  to: {
    version: 24, deployNumber: 32, action: 'deploy', gmtCreate: 1720003600,
    specJson: JSON.stringify({
      id: 'support', title: '新工单处理', description: '新增说明', ...sharedFields,
      owner: 'new-owner', nodes: [{ id: 'start', type: 'start' }],
    }),
  },
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((onResolve, onReject) => { resolve = onResolve; reject = onReject })
  return { promise, resolve, reject }
}

function renderComparison(fromDeploy = 31, toDeploy = 32) {
  return render(<WorkflowVersionDiff workflowId="support" fromDeploy={fromDeploy} toDeploy={toDeploy} />)
}

async function panes() {
  return {
    old: await screen.findByRole('region', { name: '旧版本 v23' }),
    current: screen.getByRole('region', { name: '新版本 v24' }),
  }
}

beforeEach(() => {
  vi.spyOn(api.workflows, 'diffHistory').mockResolvedValue(comparison)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('WorkflowVersionDiff side-by-side view', () => {
  it('loads the requested deploys and displays their actual versions, YAML and line numbers on the correct sides', async () => {
    renderComparison()
    const { old, current } = await panes()
    expect(api.workflows.diffHistory).toHaveBeenCalledWith('support', 31, 32)
    expect(within(old).getByText('title: 旧工单处理')).toBeInTheDocument()
    expect(within(current).getByText('title: 新工单处理')).toBeInTheDocument()
    expect(within(old).queryByText('title: 新工单处理')).not.toBeInTheDocument()
    expect(within(current).queryByText('title: 旧工单处理')).not.toBeInTheDocument()
    expect(within(current).getByText('description: 新增说明')).toBeInTheDocument()
    expect(within(old).queryByText('description: 新增说明')).not.toBeInTheDocument()
    expect(old).toHaveTextContent(/2\s*[-+]?\s*title: 旧工单处理/)
    expect(current).toHaveTextContent(/2\s*[-+]?\s*title: 新工单处理/)
    expect(current).toHaveTextContent(/3\s*[-+]?\s*description: 新增说明/)
    expect(within(old).getByText('第 2 行，删除')).toHaveClass('sr-only')
    expect(within(current).getByText('第 3 行，新增')).toHaveClass('sr-only')
  })

  it('aligns a changed line and an unmatched insertion with a blank opposite row', async () => {
    renderComparison()
    const { old, current } = await panes()
    const oldTitle = within(old).getByText('title: 旧工单处理').parentElement!
    const newTitle = within(current).getByText('title: 新工单处理').parentElement!
    const inserted = within(current).getByText('description: 新增说明').parentElement!
    // Rows must share an index across the panes, including a placeholder for the insertion.
    const oldRows = Array.from(oldTitle.parentElement!.children)
    const newRows = Array.from(newTitle.parentElement!.children)
    const titleIndex = newRows.indexOf(newTitle)
    const insertionIndex = newRows.indexOf(inserted)
    expect(oldRows.indexOf(oldTitle)).toBe(titleIndex)
    expect(insertionIndex).toBeGreaterThan(titleIndex)
    expect(oldRows[insertionIndex]).toBeDefined()
    expect(oldRows[insertionIndex].textContent?.trim()).toBe('')
  })

  it('starts with full content and switches to changes and back in both panes', async () => {
    const user = userEvent.setup()
    renderComparison()
    const { old, current } = await panes()
    expect(within(old).getByText('setting6: unchanged-6')).toBeInTheDocument()
    expect(within(current).getByText('setting6: unchanged-6')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '仅看变更' }))
    expect(within(old).queryByText('setting6: unchanged-6')).not.toBeInTheDocument()
    expect(within(current).queryByText('setting6: unchanged-6')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '完整内容' }))
    expect(within(old).getByText('setting6: unchanged-6')).toBeInTheDocument()
    expect(within(current).getByText('setting6: unchanged-6')).toBeInTheDocument()
  })

  it('expands and collapses unchanged lines in both panes together in changes mode', async () => {
    const user = userEvent.setup()
    renderComparison()
    await screen.findByText('v23')
    await user.click(screen.getByRole('button', { name: '仅看变更' }))
    const { old, current } = await panes()
    const expand = screen.getAllByRole('button', { name: /展开.*行未变更内容/ })[0]
    expect(within(old).queryByText('setting6: unchanged-6')).not.toBeInTheDocument()
    expect(within(current).queryByText('setting6: unchanged-6')).not.toBeInTheDocument()
    await user.click(expand)
    expect(within(old).getByText('setting6: unchanged-6')).toBeInTheDocument()
    expect(within(current).getByText('setting6: unchanged-6')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: /收起.*行未变更内容/ })[0])
    expect(within(old).queryByText('setting6: unchanged-6')).not.toBeInTheDocument()
    expect(within(current).queryByText('setting6: unchanged-6')).not.toBeInTheDocument()
  })

  it('offers navigation between distinct changes', async () => {
    const user = userEvent.setup()
    renderComparison()
    await screen.findByText('owner: old-owner')
    const next = screen.getByRole('button', { name: '下一处变更' })
    const previous = screen.getByRole('button', { name: '上一处变更' })
    expect(screen.getByLabelText('变更位置')).toHaveTextContent('— / 2')
    await user.click(next)
    expect(screen.getByLabelText('变更位置')).toHaveTextContent('1 / 2')
    await user.click(next)
    expect(screen.getByLabelText('变更位置')).toHaveTextContent('2 / 2')
    await user.click(previous)
    expect(screen.getByLabelText('变更位置')).toHaveTextContent('1 / 2')
  })

  it('synchronizes vertical scrolling in both directions without coupling horizontal scrolling', async () => {
    renderComparison()
    const { old, current } = await panes()
    expect(old).toHaveClass('overflow-scroll')
    expect(current).toHaveClass('overflow-scroll')
    old.scrollTop = 120
    old.scrollLeft = 45
    fireEvent.scroll(old)
    expect(current.scrollTop).toBe(120)
    expect(current.scrollLeft).toBe(0)
    current.scrollTop = 260
    current.scrollLeft = 75
    fireEvent.scroll(current)
    expect(old.scrollTop).toBe(260)
    expect(old.scrollLeft).toBe(45)
  })
})

describe('WorkflowVersionDiff requests', () => {
  it('shows loading until the requested snapshots arrive', async () => {
    const pending = deferred<VersionDiffResult>()
    vi.mocked(api.workflows.diffHistory).mockReturnValueOnce(pending.promise)
    renderComparison()
    expect(screen.getByText('加载版本对比…')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '旧版本 v23' })).not.toBeInTheDocument()
    await act(async () => pending.resolve(comparison))
    expect(screen.queryByText('加载版本对比…')).not.toBeInTheDocument()
    expect(await screen.findByText('v23')).toBeInTheDocument()
    expect(screen.getByText('v24')).toBeInTheDocument()
  })

  it('shows a request error rather than stale snapshot data', async () => {
    vi.mocked(api.workflows.diffHistory).mockRejectedValueOnce(new Error('无法加载历史版本'))
    renderComparison()
    expect(await screen.findByText('无法加载历史版本')).toBeInTheDocument()
    expect(screen.queryByText('v23')).not.toBeInTheDocument()
  })

  it('discards a late response after the selected deploys change', async () => {
    const first = deferred<VersionDiffResult>()
    const second = deferred<VersionDiffResult>()
    vi.mocked(api.workflows.diffHistory).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const view = renderComparison()
    view.rerender(<WorkflowVersionDiff workflowId="support" fromDeploy={32} toDeploy={33} />)
    expect(api.workflows.diffHistory).toHaveBeenLastCalledWith('support', 32, 33)
    const updated: VersionDiffResult = {
      ...comparison,
      from: comparison.to,
      to: { ...comparison.to, version: 25, deployNumber: 33, specJson: JSON.stringify({ id: 'support', title: '最新工单处理', nodes: [] }) },
    }
    await act(async () => second.resolve(updated))
    expect(screen.getByText('v25')).toBeInTheDocument()
    await act(async () => first.resolve(comparison))
    expect(screen.getByText('v25')).toBeInTheDocument()
    expect(screen.queryByText('v23')).not.toBeInTheDocument()
  })
})
