import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import RepairTaskEntry from '../RepairTaskEntry'

it('makes pending tasks available at the page entry and keeps history collapsed', async () => {
  const open = vi.fn()
  render(<RepairTaskEntry issueCount={53} loading={false} onOpen={open} tasks={[
    { taskId: 'a-uuid', title: '调整搜索节点超时', phase: 'review', revision: 2, itemCount: 3, updatedAtMs: 2 },
    { taskId: 'old-uuid', title: '上一轮脚本修复', phase: 'published', revision: 1, itemCount: 1, updatedAtMs: 1 },
  ]} />)
  expect(screen.getByText('问题（53）')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '优化任务（1 待处理）' }))
  expect(screen.getByRole('button', { name: '调整搜索节点超时' })).toBeVisible()
  expect(screen.getByRole('button', { name: '上一轮脚本修复', hidden: true })).not.toBeVisible()
  expect(screen.getByText('a-uuid')).not.toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '调整搜索节点超时' }))
  expect(open).toHaveBeenCalledExactlyOnceWith('a-uuid')
})
