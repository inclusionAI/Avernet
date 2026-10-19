import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import RepairTaskEntry from '../RepairTaskEntry'

it('makes pending tasks available at the page entry and keeps history collapsed', async () => {
  const open = vi.fn()
  render(<RepairTaskEntry loading={false} onOpen={open} tasks={[
    { taskId: 'a-uuid', title: '调整搜索节点超时', phase: 'review', revision: 2, itemCount: 3, updatedAtMs: 2 },
    { taskId: 'old-uuid', title: '上一轮脚本修复', phase: 'published', revision: 1, itemCount: 1, updatedAtMs: 1 },
  ]} />)
  expect(screen.getByText('1 个待审阅')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '查看全部任务（2）' }))
  const dialog = screen.getByRole('dialog', { name: '优化任务' })
  const current = within(dialog).getByRole('article', { name: '优化任务 a-uuid' })
  expect(within(current).getByText('调整搜索节点超时')).toBeVisible()
  expect(within(dialog).getByText('上一轮脚本修复')).not.toBeVisible()
  expect(within(current).getByText('a-uuid')).not.toBeVisible()
  await userEvent.click(within(current).getByRole('button', { name: '查看任务' }))
  expect(open).toHaveBeenCalledExactlyOnceWith('a-uuid')
})

it('separates task totals and makes review/published status explicit on every row', async () => {
  const open = vi.fn()
  render(<RepairTaskEntry loading={false} onOpen={open} tasks={[
    { taskId: 'review-1', title: '当前生效 Pack 已变化，请重新核对原修改的适用性', phase: 'review', revision: 1, itemCount: 2, updatedAtMs: 4 },
    { taskId: 'review-2', title: '调整报告输出', phase: 'review', revision: 1, itemCount: 1, updatedAtMs: 3 },
    { taskId: 'published-1', title: '修复报告模板', phase: 'published', revision: 1, itemCount: 1, updatedAtMs: 2 },
    { taskId: 'published-2', title: '调整搜索超时', phase: 'published', revision: 1, itemCount: 1, updatedAtMs: 1 },
  ]} />)
  expect(screen.getByText('2 个待审阅')).toBeVisible()
  expect(screen.getByText('2 个已发布')).toBeVisible()
  expect(screen.queryByText(/问题（/)).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '查看全部任务（4）' }))
  const dialog = screen.getByRole('dialog', { name: '优化任务' })
  expect(within(dialog).getByText('共 4 个优化任务。')).toBeVisible()
  const pending = within(dialog).getByRole('article', { name: '优化任务 review-1' })
  expect(within(pending).getByText('待审阅')).toBeVisible()
  expect(within(pending).getByText(/包含 2 条建议/)).toBeVisible()
  await userEvent.click(within(dialog).getByText('已发布与历史任务（2 个）', { selector: 'summary' }))
  const published = within(dialog).getByRole('article', { name: '优化任务 published-1' })
  expect(within(published).getByText('已发布')).toBeVisible()
  await userEvent.click(within(published).getByRole('button', { name: '查看任务' }))
  expect(open).toHaveBeenCalledExactlyOnceWith('published-1')
})

it('keeps unread task totals distinct from a confirmed empty list', () => {
  const { rerender } = render(<RepairTaskEntry loading tasks={[]} onOpen={vi.fn()} />)
  expect(screen.getByText('正在读取优化任务…')).toBeVisible()
  expect(screen.queryByText(/暂无优化任务/)).not.toBeInTheDocument()
  rerender(<RepairTaskEntry loading={false} tasks={[]} onOpen={vi.fn()} />)
  expect(screen.getByText(/暂无优化任务/)).toBeVisible()
  expect(screen.queryByRole('button', { name: /查看全部任务/ })).not.toBeInTheDocument()
  rerender(<RepairTaskEntry error loading={false} tasks={[]} onOpen={vi.fn()} />)
  expect(screen.getByText('任务数量暂不可用')).toBeVisible()
  expect(screen.queryByText(/暂无优化任务/)).not.toBeInTheDocument()
})

it('shows the real goal as the heading and separates origin from review status', async () => {
 render(<RepairTaskEntry loading={false} onOpen={vi.fn()} tasks={[
  {taskId:'auto',title:'优化 report 节点（2 条建议）',source:'automatic',phase:'review',revision:1,itemCount:2,updatedAtMs:2},
  {taskId:'manual',title:'修复搜索超时',source:'manual',phase:'review',revision:1,itemCount:1,updatedAtMs:1},
 ] as never}/>);
 expect(screen.getByRole('heading',{name:'优化 report 节点（2 条建议）'})).toBeVisible();
 expect(screen.getByText('自动任务')).toBeVisible();
 await userEvent.click(screen.getByRole('button',{name:'查看全部任务（2）'}));
 const dialog=screen.getByRole('dialog',{name:'优化任务'});
 expect(within(dialog).getByRole('heading',{name:'修复搜索超时'})).toBeVisible();
 expect(within(dialog).getByText('手动任务')).toBeVisible();
});

it('shows every review task directly without opening the task history', async () => {
 const open=vi.fn();render(<RepairTaskEntry loading={false} onOpen={open} tasks={[
 {taskId:'auto',title:'自动报告优化',source:'automatic',phase:'review',revision:1,itemCount:2,updatedAtMs:3},
 {taskId:'manual',title:'手动搜索优化',source:'manual',phase:'review',revision:2,itemCount:1,updatedAtMs:2},
 {taskId:'old',title:'已发布优化',source:'automatic',phase:'published',revision:1,itemCount:1,updatedAtMs:1},
 ]}/>);
 expect(screen.getByRole('heading',{name:'自动报告优化'})).toBeVisible();
 expect(screen.getByRole('heading',{name:'手动搜索优化'})).toBeVisible();
 expect(screen.queryByRole('heading',{name:'已发布优化'})).not.toBeInTheDocument();
 await userEvent.click(screen.getByRole('article',{name:'优化任务 manual'}).querySelector('button')!);
 expect(open).toHaveBeenCalledWith('manual');
});
