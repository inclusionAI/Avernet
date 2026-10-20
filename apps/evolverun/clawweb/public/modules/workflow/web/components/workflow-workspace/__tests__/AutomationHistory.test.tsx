import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AutomationHistory from '../AutomationHistory'
const requests: URL[] = []
beforeEach(() => {
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = new URL(input, 'http://localhost'); requests.push(url)
    const status = url.searchParams.get('status'), period = url.searchParams.get('period'), cursor = url.searchParams.get('cursor')
    const limit = Number(url.searchParams.get('limit'))
    const count = status === 'blocked' ? 0 : period === 'today' ? 1 : limit
    return { ok: true, json: async () => ({ jobs: Array.from({length:count}, (_,i)=>({id:`${cursor?'older':'new'}-${i}`,kind:'auto_run',status:'done',attempts:1,reason:null,flowId:`${cursor?'older':'new'}-run-${i}`,complete:true})), nextCursor: !cursor && count === limit ? 'next-page' : null }) }
  }))
})
afterEach(()=>vi.unstubAllGlobals())
function mount(){const query = new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});return render(<QueryClientProvider client={query}><AutomationHistory workflowId="wf/a"/></QueryClientProvider>)}
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
