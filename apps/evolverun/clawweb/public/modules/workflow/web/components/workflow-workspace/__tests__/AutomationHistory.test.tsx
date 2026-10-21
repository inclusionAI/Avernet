import { act, render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AutomationHistory, { NotificationAttention } from '../AutomationHistory'
const requests: URL[] = []
beforeEach(() => {
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = new URL(input, 'http://localhost'); requests.push(url)
    const status = url.searchParams.get('status'), period = url.searchParams.get('period'), cursor = url.searchParams.get('cursor')
    const limit = Number(url.searchParams.get('limit'))
    const kind = url.searchParams.get('kind') === 'optimization' ? 'auto_draft' : 'auto_run'
    const count = status === 'blocked' ? 0 : period === 'today' ? 1 : limit
    return { ok: true, json: async () => ({ jobs: Array.from({length:count}, (_,i)=>({id:`${cursor?'older':'new'}-${i}`,kind,status:'done',attempts:1,reason:null,...(kind === 'auto_run' ? {flowId:`${cursor?'older':'new'}-run-${i}`} : {taskId:`repair-${i}`}),complete:true})), nextCursor: !cursor && count === limit ? 'next-page' : null }) }
  }))
})
afterEach(()=>{focusManager.setFocused(undefined);vi.useRealTimers();vi.unstubAllGlobals()})
function mount(){const query = new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});return render(<QueryClientProvider client={query}><AutomationHistory workflowId="wf/a"/></QueryClientProvider>)}
it('separates analysis from optimization and resets old page cursors when switching types', async () => {
  mount(); await screen.findByText(/运行 new-run-0/)
  expect(requests[0].searchParams.get('kind')).toBe('analysis')
  expect(screen.getByRole('tab', { name: '自动分析' })).toHaveAttribute('aria-selected', 'true')
  await userEvent.click(screen.getByRole('button', { name: '查看全部记录' })); await screen.findByText(/运行 new-run-19/)
  await userEvent.click(screen.getByRole('button', { name: '下一页' })); await screen.findByText(/运行 older-run-0/)
  await userEvent.click(screen.getByRole('tab', { name: '自动优化' }))
  await waitFor(() => expect(requests.at(-1)!.searchParams.get('kind')).toBe('optimization'))
  expect(requests.at(-1)!.searchParams.has('cursor')).toBe(false)
  expect(await screen.findAllByRole('link', { name: '查看原优化任务' })).toHaveLength(20)
  expect(screen.queryByRole('link', { name: '查看分析' })).not.toBeInTheDocument()
  expect(screen.getByText('第 1 页 · 每页最多 20 条')).toBeVisible()
});

it('folds notification exceptions, pages their own read-only query, and keeps full identifiers in details', async () => {
  const taskId = '2d675412-fa44-414a-ab56-8b1abc672833'
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = new URL(input, 'http://localhost'); requests.push(url)
    const older = url.searchParams.has('cursor')
    return { ok: true, json: async () => ({ jobs: [{id: older ? 'older-notice' : 'recent-notice', kind:'auto_notice', status:'uncertain', attempts:2,reason:'NOTIFICATION_OUTCOME_UNKNOWN',taskId}], nextCursor:older ? null : 'next-notice' }) }
  }))
  const query = new QueryClient({defaultOptions:{queries:{retry:false}}})
  render(<QueryClientProvider client={query}><NotificationAttention workflowId="wf/a" /></QueryClientProvider>)
  const summary = await screen.findByText('通知发送异常，需处理')
  expect(screen.getByText('任务通知 · 待核对')).not.toBeVisible()
  expect(requests[0].searchParams.get('kind')).toBe('notification')
  expect(requests[0].searchParams.get('status')).toBe('attention')
  expect(requests[0].searchParams.get('limit')).toBe('8')
  await userEvent.click(summary)
  expect(screen.getByText(/优化任务 2d675412…/)).toBeVisible()
  expect(screen.getByText(`优化任务编号：${taskId}`)).not.toBeVisible()
  await userEvent.click(screen.getByText('查看详情'))
  expect(screen.getByText(`优化任务编号：${taskId}`)).toBeVisible()
  expect(screen.getByRole('link', {name:'查看原优化任务'})).toHaveAttribute('href', `/workflows/workspace?workflowId=wf%2Fa&tab=evolution&repairTaskId=${taskId}`)
  await userEvent.click(screen.getByRole('button', {name:'下一页'}))
  await waitFor(() => expect(requests.at(-1)!.searchParams.get('cursor')).toBe('next-notice'))
  expect(await screen.findByText('第 2 页 · 每页最多 8 条')).toBeVisible()
  expect(screen.getByRole('button', {name:'下一页'})).toBeDisabled()
  await userEvent.click(screen.getByRole('button', {name:'上一页'}))
  expect(screen.getByText('第 1 页 · 每页最多 8 条')).toBeVisible()
  await userEvent.click(screen.getByRole('button', {name:'刷新通知异常'}))
  await waitFor(() => expect(requests.filter(url=>!url.searchParams.has('cursor'))).toHaveLength(2))
})
it('loads only eight recent records initially, fetching older pages only on demand', async()=>{
  mount()
  await screen.findByText(/运行 new-run-7/)
  expect(screen.getAllByRole('link',{name:'查看分析'})).toHaveLength(8)
  expect(requests).toHaveLength(1);expect(requests[0].pathname).toContain('wf%2Fa/automation-records')
  expect(requests[0].searchParams.get('limit')).toBe('8')
  expect(screen.queryByLabelText('执行记录时间范围')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'查看全部记录'}))
  await screen.findByText(/运行 new-run-19/)
  expect(screen.getByRole('button',{name:'上一页'})).toBeDisabled()
  expect(requests.at(-1)!.searchParams.get('limit')).toBe('20')
  await userEvent.click(screen.getByRole('button',{name:'下一页'}))
  await screen.findByText(/运行 older-run-0/)
  expect(requests.at(-1)!.searchParams.get('cursor')).toBe('next-page')
  expect(screen.getByText('第 2 页 · 每页最多 20 条')).toBeVisible()
  expect(screen.getByRole('button',{name:'下一页'})).toBeDisabled()
  await userEvent.click(screen.getByRole('button',{name:'上一页'}))
  await screen.findByText(/运行 new-run-0/)
  expect(screen.getByText('第 1 页 · 每页最多 20 条')).toBeVisible()
})
it('resets pagination on date or status changes, and returns to unfiltered recent records when collapsed', async()=>{
  mount();await screen.findByText(/运行 new-run-0/)
  await userEvent.click(screen.getByRole('button',{name:'查看全部记录'}));await screen.findByText(/运行 new-run-19/)
  await userEvent.click(screen.getByRole('button',{name:'下一页'}));await screen.findByText(/运行 older-run-0/)
  await userEvent.selectOptions(screen.getByLabelText('执行记录时间范围'),'today')
  await waitFor(()=>expect(requests.at(-1)!.searchParams.get('period')).toBe('today'))
  expect(requests.at(-1)!.searchParams.has('cursor')).toBe(false)
  expect(screen.getByText('第 1 页 · 每页最多 20 条')).toBeVisible()
  await userEvent.selectOptions(screen.getByLabelText('执行记录时间范围'),'7d')
  await waitFor(()=>expect(requests.at(-1)!.searchParams.get('period')).toBe('7d'))
  await userEvent.selectOptions(screen.getByLabelText('执行记录状态'),'blocked')
  expect(await screen.findByText('没有符合筛选条件的记录。')).toBeVisible()
  expect(requests.at(-1)!.searchParams.get('status')).toBe('blocked')
  expect(requests.at(-1)!.searchParams.has('cursor')).toBe(false)
  await userEvent.click(screen.getByRole('button',{name:'收起全部记录'}))
  const history=screen.getByRole('region',{name:'自动任务历史记录'})
  expect(within(history).getAllByRole('link',{name:'查看分析'})).toHaveLength(8)
  expect(screen.queryByLabelText('执行记录状态')).not.toBeInTheDocument()
})
it('exposes query errors and lets the user retry without business writes', async()=>{
  vi.mocked(fetch).mockRejectedValueOnce(new Error('controlled history read failure'))
  mount();expect(await screen.findByRole('alert')).toHaveTextContent('执行记录读取失败')
  await userEvent.click(screen.getByRole('button',{name:'重试读取记录'}))
  await screen.findByText(/运行 new-run-7/)
  expect(requests).toHaveLength(1)
})

it('does not poll history and refreshes explicitly on request', async () => {
  vi.useFakeTimers();
  focusManager.setFocused(true);
  mount();
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(screen.getByText(/运行 new-run-7/)).toBeVisible();
  await act(async () => { await vi.advanceTimersByTimeAsync(45000); });
  expect(requests).toHaveLength(1);
  vi.useRealTimers();
  await userEvent.click(screen.getByRole('button', { name: '刷新记录' }));
  await waitFor(() => expect(requests).toHaveLength(2));
});
