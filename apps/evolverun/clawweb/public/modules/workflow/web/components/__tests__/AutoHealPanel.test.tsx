import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AutoHealDiagnosisResult, FlowRun } from '@avernet/clawweb-shared/web/types'
import AutoHealPanel from '../AutoHealPanel'

// Keep React Query and HTTP handling real; @avernet/clawweb-shared resolves to source through
// the vitest alias, so the re-exported hooks and fetchJson never come from dist.

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
const savedCommit = 'a'.repeat(40)
const deployed = { ok: true, deployed: true, workflowId: run.workflow_id, packId: 'pack-1', deployNumber: 2, version: 2, tagName: 'v2' }
const runResult = { ok: true, workflowId: run.workflow_id, flowId: 'new-flow' }
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const fetchMock = vi.fn<typeof fetch>()
let queryClient: QueryClient
let saveResponse: () => Promise<Response>
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

async function deployBody() {
  const init = requests('deploy')[0][1]!
  expect(new Headers(init.headers).get('X-Pack-Version-CSRF')).toBe('1')
  expect(new Headers(init.headers).has('Content-Encoding')).toBe(false)
  expect(init.body).toBeInstanceOf(Blob)
  const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init.body as Blob) })
  return JSON.parse(text)
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
  localStorage.clear()
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  saveResponse = async () => response({ ok: true, saved: true, packId: 'pack-1', commit: savedCommit })
  deployResponse = async () => response(deployed)
  runResponse = async () => response(runResult)
  fetchMock.mockReset().mockImplementation(async (input) => {
    switch (String(input)) {
      case '/api/auto-heal/diagnose': return response(diagnosis)
      case '/api/auto-heal/save': return saveResponse()
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

  it('removes legacy sensitive records without rendering them and blocks unsafe replacement', async () => {
    const prefix = 'clawweb.autoheal.operation.v1.'
    localStorage.setItem(prefix + run.workflow_id, JSON.stringify({ diagnosis: { rawResponse: 'SECRET-OTHER-USER', fixedSpec: { secret: 'placeholder' } } }))
    localStorage.setItem(prefix + 'another-workflow', JSON.stringify({ diagnosis: { summary: 'OTHER-SECRET' } }))
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('旧版恢复记录')
    expect(document.body.textContent).not.toContain('SECRET-OTHER-USER')
    expect(document.body.textContent).not.toContain('placeholder')
    expect(localStorage.getItem(prefix + run.workflow_id)).toBeNull()
    expect(localStorage.getItem(prefix + 'another-workflow')).toBeNull()
    expect(JSON.stringify(localStorage)).not.toContain('SECRET')
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    expect(await screen.findByRole('button', { name: '保存修复' })).toBeDisabled()
    expect(requests('save')).toHaveLength(0)
    cleanup(); renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('旧版恢复记录')
  })
  it('shows and deploys the exact saved commit, regardless of subsequent remote changes', async () => {
    renderPanel()
    const deployButton = await saveFix(false)
    expect(screen.getByText(savedCommit)).toBeInTheDocument()
    await userEvent.click(deployButton)
    await waitFor(() => expect(requests('deploy')).toHaveLength(1))
    expect((await deployBody()).candidateCommit).toBe(savedCommit)
  })

  it('does not offer deployment if the save response lacks a valid commit', async () => {
    saveResponse = async () => response({ ok: true, saved: true, packId: 'pack-1' })
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    await userEvent.click(await screen.findByRole('button', { name: '保存修复' }))
    expect(await screen.findByText(/保存结果缺少有效的候选 commit/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '部署修复' })).not.toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(0)
  })
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
    expect((await deployBody()).autoRun).toBe(false)
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
  })

  it('autoRun=false deploys only, then manually runs the original cron flow without input.command', async () => {
    const pendingRun = deferredResponse()
    runResponse = () => pendingRun.promise
    const { onRerunComplete } = renderPanel()
    await userEvent.click(await saveFix(false))

    expect(await screen.findByText('修复已部署')).toBeInTheDocument()
    expect((await deployBody()).autoRun).toBe(false)
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
    expect((await deployBody())).toEqual({
      workflowId: run.workflow_id, packId: 'pack-1', diagnosisId: diagnosis.diagnosisId,
      note: 'auto-heal deploy', autoRun: false, candidateCommit: savedCommit,
    })
    expect(screen.getByText('正在部署修复…')).toBeInTheDocument()
    expect(requests('run')).toHaveLength(0)
    expect(onRerunComplete).not.toHaveBeenCalled()

    await act(async () => pendingDeploy.resolve(response(deployed)))
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
    expect(requests('deploy')).toHaveLength(1)
  })

  it('shows the exact-candidate compatibility error instead of reporting a deployment or rerunning', async () => {
    const message = '候选部署暂不可用：跨入口并发保护、Git/数据库一致性及 YAML 候选同步尚未完成，未执行部署。'
    deployResponse = async () => response({ error: 'Service Unavailable', code: 'CANDIDATE_DEPLOYMENT_UNAVAILABLE', message }, 503)
    const { onRerunComplete } = renderPanel()
    await userEvent.click(await saveFix())
    expect(await screen.findByText(message)).toBeInTheDocument()
    expect((await deployBody()).candidateCommit).toBe(savedCommit)
    expect(requests('run')).toHaveLength(0)
    expect(onRerunComplete).not.toHaveBeenCalled()
    expect(screen.queryByText('修复已部署')).not.toBeInTheDocument()
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

  it.each([true, false])('does not carry diagnosis or a deploy candidate across source-run changes (autoRun=%s)', async autoRun => {
    const { rerenderRun, onRerunComplete } = renderPanel()
    await saveFix(autoRun)
    rerenderRun({ ...run, flow_id: 'different-run' })
    expect(screen.queryByText(diagnosis.summary)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '部署修复' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新运行' })).not.toBeInTheDocument()
    expect(requests('run')).toHaveLength(0)
    expect(onRerunComplete).not.toHaveBeenCalled()
  })
})
