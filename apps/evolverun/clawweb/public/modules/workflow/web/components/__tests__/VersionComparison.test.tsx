import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SuccessTrendCard } from '../SuccessTrendCard'
const baseline = { deployNumber:10, fromMs:2000000000000, toMs:2000086400000, terminalRuns:100, succeededRuns:90, failedRuns:10, successRate:90, excludedUnfinished:1 }
const current = { ...baseline, deployNumber:12, succeededRuns:96, failedRuns:4, successRate:96 }
const data = { baseline, current, versions:[{deployNumber:10,version:1,active:false,source:null},{deployNumber:12,version:1,active:true,source:{kind:'auto_optimization'}}], differencePercentagePoints:6, excludedUnknownDeployment:2, windowMs:86400000, status:'ready' }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/') })
it('opens version comparison inside the existing card with precise samples and percentage points', async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok:true, json:async () => ({data:url.includes('version-comparison') ? data : []}) }))
  vi.stubGlobal('fetch',fetchMock)
  render(<SuccessTrendCard workflowId="workflow" currentSuccessRate="96%" currentDetail="样本" />)
  fireEvent.click(screen.getByRole('button',{name:'版本对比'}))
  await screen.findByText('成功率提高 6.0 个百分点')
  expect(screen.getByText('成功 96/100 · 失败 4')).toBeInTheDocument()
  expect(screen.getByRole('option',{name:'#12 · 自动优化 · 人工确认发布 · 当前生效'})).toBeInTheDocument()
  expect(screen.getByText(/指标变化不代表全部由优化造成/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'成功率趋势'}))
  await waitFor(() => expect(screen.getByText('暂无趋势数据')).toBeInTheDocument())
})
it('renders null samples honestly without an improvement claim', async () => {
  vi.stubGlobal('fetch',vi.fn(async (url: string) => ({ok:true,json:async () => ({data:url.includes('version-comparison') ? {...data,current:{...current,terminalRuns:0,succeededRuns:0,failedRuns:0,successRate:null},differencePercentagePoints:null,status:'no_samples'}:[]})})))
  render(<SuccessTrendCard workflowId="workflow" currentSuccessRate="—" currentDetail="" />)
  fireEvent.click(screen.getByRole('button',{name:'版本对比'}))
  await screen.findByText('暂无终态样本')
  expect(screen.queryByText(/成功率提高/)).not.toBeInTheDocument()
})

it('does not send a previous workflow deep-link selection after the card switches workflows', async () => {
  window.history.replaceState({}, '', '/workflows/workspace?workflowId=tech-research-v2&view=version-comparison&baseline=159&current=160')
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ data }) }))
  vi.stubGlobal('fetch', fetchMock)
  const view = render(<SuccessTrendCard workflowId="tech-research-v2" currentSuccessRate="96%" currentDetail="" />)
  await screen.findByText('成功率提高 6.0 个百分点')
  expect(fetchMock).toHaveBeenCalledWith('/api/workflows/tech-research-v2/version-comparison?baseline=159&current=160')
  view.rerender(<SuccessTrendCard workflowId="risk-processor" currentSuccessRate="96%" currentDetail="" />)
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/workflows/risk-processor/version-comparison?'))
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/risk-processor/') && String(url).includes('159'))).toBe(false)
})
it('distinguishes an unavailable comparison service from a retryable generic failure', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503 })))
  render(<SuccessTrendCard workflowId="workflow" currentSuccessRate="—" currentDetail="" />)
  fireEvent.click(screen.getByRole('button', { name: '版本对比' }))
  await screen.findByText(/版本对比服务未就绪/)
})

it('drops dropdown selections when switching workflows even without a URL selection', async () => {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ data }) }))
  vi.stubGlobal('fetch', fetchMock)
  const view = render(<SuccessTrendCard workflowId="first" currentSuccessRate="96%" currentDetail="" />)
  fireEvent.click(screen.getByRole('button', { name: '版本对比' }))
  await screen.findByText('成功率提高 6.0 个百分点')
  fireEvent.change(screen.getByRole('combobox', { name: '基线版本' }), { target: { value: '10' } })
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/workflows/first/version-comparison?baseline=10&current=12'))
  view.rerender(<SuccessTrendCard workflowId="second" currentSuccessRate="96%" currentDetail="" />)
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/workflows/second/version-comparison?'))
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/second/') && String(url).includes('baseline='))).toBe(false)
})

it.each(['network', 'json'])('keeps unexpected %s failures behind the generic message', async failure => {
  vi.stubGlobal('fetch', vi.fn(async () => {
    if (failure === 'network') throw new Error('private proxy diagnostic')
    return { ok: true, json: async () => { throw new SyntaxError('unexpected private HTML fragment') } }
  }))
  render(<SuccessTrendCard workflowId="workflow" currentSuccessRate="—" currentDetail="" />)
  fireEvent.click(screen.getByRole('button', { name: '版本对比' }))
  await screen.findByText('版本对比加载失败')
  expect(screen.queryByText(/private/)).not.toBeInTheDocument()
})
