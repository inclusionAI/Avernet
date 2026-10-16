import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SuccessTrendCard } from '../SuccessTrendCard'
const baseline = { deployNumber:10, fromMs:2000000000000, toMs:2000086400000, terminalRuns:100, succeededRuns:90, failedRuns:10, successRate:90, excludedUnfinished:1 }
const current = { ...baseline, deployNumber:12, succeededRuns:96, failedRuns:4, successRate:96 }
const data = { baseline, current, versions:[{deployNumber:10,version:1,active:false,source:null},{deployNumber:12,version:1,active:true,source:{kind:'auto_optimization'}}], differencePercentagePoints:6, excludedUnknownDeployment:2, windowMs:86400000, status:'ready' }
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
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
