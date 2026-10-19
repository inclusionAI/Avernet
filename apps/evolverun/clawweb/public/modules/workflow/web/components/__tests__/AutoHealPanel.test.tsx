import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AutoHealDiagnosisResult, FlowRun } from '@avernet/clawweb-shared/web/types'
import AutoHealPanel from '../AutoHealPanel'

const run: FlowRun = {
  flow_id: 'source-cron-flow', workflow_id: 'cron-workflow', workflow_title: 'Cron workflow',
  status: 'failed', triggered_by: 'cron', node_count: 1, succeeded_count: 0, failed_count: 1,
  total_duration_ms: 100, total_token_usage: null, started_at: '2026-10-19T00:00:00Z',
  completed_at: null, user_id: null, origin_bot_id: 'untrusted-client-bot', plugin_version: null,
  engine: null, workflow_version: 1, workflow_deploy_number: 1,
}
const diagnosis: AutoHealDiagnosisResult = {
  ok: true, diagnosisId: 'diagnosis-1', workflowId: run.workflow_id, summary: '修复 cron 运行的参数错误',
  errorChain: [], fixSuggestions: [], fixedYaml: 'id: cron-workflow',
  fixedSpec: { id: run.workflow_id, version: '2', title: 'Cron workflow', nodes: [] },
  diff: [], rawResponse: '',
}
const deployed = { ok: true, deployed: true, workflowId: run.workflow_id, packId: 'pack-1', deployNumber: 2, version: 2, tagName: 'v2' }
const runResult = { ok: true, workflowId: run.workflow_id, flowId: 'new-flow' }
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const fetchMock = vi.fn<typeof fetch>()
let queryClient: QueryClient
let deployResponse: () => Promise<Response>
let runResponse: () => Promise<Response>

function deferredResponse() {
  let resolve!: (value: Response) => void
  const promise = new Promise<Response>((done) => { resolve = done })
  return { promise, resolve }
}

function requests(path: string) {
  return fetchMock.mock.calls.filter(([url]) => url === `/api/auto-heal/${path}`)
}

function expectSourceRun(count = 1) {
  expect(requests('run')).toHaveLength(count)
  for (const [, init] of requests('run')) {
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ workflowId: run.workflow_id, sourceFlowId: run.flow_id })
  }
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/rerun'))).toBe(false)
}

function renderPanel() {
  const onRerunComplete = vi.fn()
  const onClose = vi.fn()
  const element = (source = run) => (
    <QueryClientProvider client={queryClient}>
      <AutoHealPanel run={source} onClose={onClose} onRerunComplete={onRerunComplete} />
    </QueryClientProvider>
  )
  const view = render(element())
  return { onRerunComplete, rerenderRun: (source: FlowRun) => view.rerender(element(source)) }
}

async function saveFix(autoRun = true) {
  await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  await userEvent.click(await screen.findByRole('button', { name: '保存修复' }))
  const deployButton = await screen.findByRole('button', { name: '部署修复' })
  if (!autoRun) await userEvent.click(screen.getByRole('checkbox', { name: '部署后自动重新运行' }))
  return deployButton
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  deployResponse = async () => response(deployed)
  runResponse = async () => response(runResult)
  fetchMock.mockReset().mockImplementation(async (input) => {
    switch (String(input)) {
      case '/api/auto-heal/diagnose': return response(diagnosis)
      case '/api/auto-heal/save': return response({ ok: true, saved: true, packId: 'pack-1' })
      case '/api/auto-heal/deploy': return deployResponse()
      case '/api/auto-heal/run': return runResponse()
      default: throw new Error(`Unexpected request: ${String(input)}`)
    }
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  queryClient.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('AutoHealPanel source-aware reruns', () => {
  it('sends run.flow_id, not client bot/stage metadata, for a single manual rerun', async () => {
    const { onRerunComplete, rerenderRun } = renderPanel()
    const clientRun = { ...run, botId: 'retired-diagnostic-bot', stage: 'untrusted-stage' }
    rerenderRun(clientRun)
    await userEvent.click(await saveFix(false))
    await userEvent.click(await screen.findByRole('button', { name: '重新运行' }))
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
  })

  it('never delegates automatic running to the old deploy callback', async () => {
    const { onRerunComplete } = renderPanel()
    await userEvent.click(await saveFix())
    await waitFor(() => expect(requests('deploy')).toHaveLength(1))
    expect(JSON.parse(String(requests('deploy')[0][1]?.body)).autoRun).toBe(false)
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
  })

  it('autoRun=false deploys only, then manually runs the original cron flow without input.command', async () => {
    const pendingRun = deferredResponse()
    runResponse = () => pendingRun.promise
    const { onRerunComplete } = renderPanel()
    await userEvent.click(await saveFix(false))

    expect(await screen.findByText('修复已部署')).toBeInTheDocument()
    expect(JSON.parse(String(requests('deploy')[0][1]?.body)).autoRun).toBe(false)
    expect(requests('run')).toHaveLength(0)
    const rerun = screen.getByRole('button', { name: '重新运行' })
    // Same-tick repeat events must not enqueue duplicate BaaS tasks.
    act(() => { fireEvent.click(rerun); fireEvent.click(rerun) })
    await waitFor(() => expectSourceRun())
    expect(screen.getByText('正在重新运行工作流…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新运行' })).not.toBeInTheDocument()
    await act(async () => pendingRun.resolve(response(runResult)))
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
    expect(requests('save')).toHaveLength(1)
    expect(requests('deploy')).toHaveLength(1)
  })

  it('autoRun=true disables the server callback and runs once only after deployment succeeds', async () => {
    const pendingDeploy = deferredResponse()
    deployResponse = () => pendingDeploy.promise
    const { onRerunComplete } = renderPanel()
    const deployButton = await saveFix()
    act(() => { fireEvent.click(deployButton); fireEvent.click(deployButton) })
    await waitFor(() => expect(requests('deploy')).toHaveLength(1))
    expect(JSON.parse(String(requests('deploy')[0][1]?.body))).toEqual({
      workflowId: run.workflow_id, packId: 'pack-1', diagnosisId: diagnosis.diagnosisId,
      note: 'auto-heal deploy', autoRun: false,
    })
    expect(screen.getByText('正在部署修复…')).toBeInTheDocument()
    expect(requests('run')).toHaveLength(0)
    expect(onRerunComplete).not.toHaveBeenCalled()

    await act(async () => pendingDeploy.resolve(response(deployed)))
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
    expect(requests('deploy')).toHaveLength(1)
  })

  it('never runs when deployment fails', async () => {
    deployResponse = async () => response({ message: '部署失败，未发布' }, 500)
    const { onRerunComplete } = renderPanel()
    await userEvent.click(await saveFix())
    expect(await screen.findByText('部署失败，未发布')).toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(1)
    expect(requests('run')).toHaveLength(0)
    expect(screen.queryByText('修复已部署')).not.toBeInTheDocument()
    expect(onRerunComplete).not.toHaveBeenCalled()
  })

  it.each([true, false])('preserves the deployed fix and diagnosis after run failure (autoRun=%s); retries only run', async (autoRun) => {
    runResponse = async () => response({ message: 'BaaS 400: 原 Bot 暂不可用' }, 400)
    const { onRerunComplete } = renderPanel()
    await userEvent.click(await saveFix(autoRun))
    if (!autoRun) await userEvent.click(await screen.findByRole('button', { name: '重新运行' }))

    expect(await screen.findByText('BaaS 400: 原 Bot 暂不可用')).toBeInTheDocument()
    expect(screen.getByText('修复已部署')).toBeInTheDocument()
    expect(screen.getByText(diagnosis.summary)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存修复' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '部署修复' })).not.toBeInTheDocument()
    expect(onRerunComplete).not.toHaveBeenCalled()
    expectSourceRun()

    runResponse = async () => response(runResult)
    const retry = screen.getByRole('button', { name: '重新运行' })
    act(() => { fireEvent.click(retry); fireEvent.click(retry) })
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun(2)
    expect(requests('save')).toHaveLength(1)
    expect(requests('deploy')).toHaveLength(1)
    expect(screen.queryByText('BaaS 400: 原 Bot 暂不可用')).not.toBeInTheDocument()
  })

  it('explains a missing flow_id before diagnosis without any request', async () => {
    const { rerenderRun } = renderPanel()
    rerenderRun({ ...run, flow_id: '' })
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    expect(screen.getByText(/缺少 flow_id/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([true, false])('does not run without a source flow_id after deployment (autoRun=%s)', async (autoRun) => {
    const { rerenderRun, onRerunComplete } = renderPanel()
    await saveFix(autoRun)
    rerenderRun({ ...run, flow_id: '' })
    await userEvent.click(screen.getByRole('button', { name: '部署修复' }))
    if (!autoRun) await userEvent.click(await screen.findByRole('button', { name: '重新运行' }))
    expect(await screen.findByText(/缺少 flow_id.*无法重新运行/)).toBeInTheDocument()
    expect(screen.getByText('修复已部署')).toBeInTheDocument()
    expect(screen.getByText(diagnosis.summary)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新运行' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存修复' })).not.toBeInTheDocument()
    expect(requests('run')).toHaveLength(0)
    expect(onRerunComplete).not.toHaveBeenCalled()
  })
})
