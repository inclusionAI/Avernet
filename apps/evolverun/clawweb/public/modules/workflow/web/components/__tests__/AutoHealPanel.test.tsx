import { MutationCache, onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AutoHealDiagnosisResult, FlowRun } from '@avernet/clawweb-shared/web/types'
import AutoHealPanel from '../AutoHealPanel'
import { beginEditorWorkflowWrite, finishEditorWorkflowWrite } from '../../editor/workflowWriteGuard'
import { isWorkflowDeploymentPending } from '../../editor/deploymentGuard'
import { AUTO_HEAL_LEGACY_TIMEOUT_MS } from '../../api/autoHealLegacyTransport'

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
  return { onRerunComplete, unmount: view.unmount, rerenderRun: (source: FlowRun) => view.rerender(element(source)) }
}

async function saveFix(autoRun = true) {
  await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  await screen.findByText(/兼容保存的数据库和 Git 写入范围/); await userEvent.click(await screen.findByRole('button', { name: '保存修复' }))
  const deployButton = await screen.findByRole('button', { name: '部署修复' })
  if (!autoRun) await userEvent.click(screen.getByRole('checkbox', { name: '部署后自动重新运行' }))
  return deployButton
}

async function confirmDeploy(button: HTMLElement) {
  await userEvent.click(button)
  await userEvent.click(screen.getByRole('button', { name: '了解风险，确认部署' }))
}

let testSequence = 0
beforeEach(() => {
  run.workflow_id = `cron-workflow-${++testSequence}`
  diagnosis.workflowId = run.workflow_id
  diagnosis.fixedSpec!.id = run.workflow_id
  deployed.workflowId = run.workflow_id
  runResult.workflowId = run.workflow_id
  localStorage.clear()
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  saveResponse = async () => response({ ok: true, saved: true, workflowId: run.workflow_id, packId: 'pack-1', branch: `wf/${run.workflow_id}`, commit: 'a'.repeat(40) })
  deployResponse = async () => response(deployed)
  runResponse = async () => response(runResult)
  fetchMock.mockReset().mockImplementation(async (input) => {
    switch (String(input)) {
      case '/api/auto-heal/capabilities': return response({ autoHealUnifiedSave: false })
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

  it('settles the shared pending registration after unmount with the real deploy mutation', async () => {
    const pending = deferredResponse()
    deployResponse = () => pending.promise
    const { unmount } = renderPanel()
    await confirmDeploy(await saveFix(false))
    await waitFor(() => expect(requests('deploy')).toHaveLength(1))
    expect(isWorkflowDeploymentPending(run.workflow_id)).toBe(true)
    unmount()
    await act(async () => { pending.resolve(response(deployed)); await pending.promise })
    await waitFor(() => expect(isWorkflowDeploymentPending(run.workflow_id)).toBe(false))
    expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${run.workflow_id}`)).toBeNull()
    expect(requests('run')).toHaveLength(0)
  })

  it('handles a simulated tree rejection response without an uncertainty warning', async () => {
    deployResponse = async () => response({ code: 'DEPLOY_TREE_CONFLICT', requestId: (await deployBody()).requestId }, 409)
    renderPanel()
    await confirmDeploy(await saveFix(false))
    expect(await screen.findByText(/当前生效 tag 缺失.*部署已阻止/)).toBeInTheDocument()
    expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(run.workflow_id)}`)).toBeNull()
    expect(screen.queryByText(/结果可能未知/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '部署修复' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存修复' })).not.toBeInTheDocument()
    deployResponse = async () => response(deployed)
    await userEvent.click(screen.getByRole('button', { name: '重新诊断' }))
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    await screen.findByText(/兼容保存的数据库和 Git 写入范围/); await userEvent.click(await screen.findByRole('button', { name: '保存修复' }))
    await confirmDeploy(await screen.findByRole('button', { name: '部署修复' }))
    expect(await screen.findByText('部署已完成')).toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(2)
  })

  it.each([undefined, 'other-request-id'])('keeps AutoHeal blocked when a conflict response has requestId %s', async requestId => {
    deployResponse = async () => response({ code: 'DEPLOY_TREE_CONFLICT', ...(requestId ? { requestId } : {}) }, 409)
    renderPanel()
    await confirmDeploy(await saveFix(false))
    expect(await screen.findByText(/结果可能未知/)).toBeInTheDocument()
    expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(run.workflow_id)}`)).not.toBeNull()
    expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
    const body = await deployBody()
    expect(body.requestId).toMatch(/^[a-f0-9-]{36}$/)
    expect(requests('deploy')).toHaveLength(1)
  })

  it('can deploy on HTTP origins where crypto.randomUUID is unavailable', async () => {
    renderPanel()
    const button = await saveFix(false)
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    await confirmDeploy(button)
    expect(await screen.findByText('部署已完成')).toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(1)
    expect((await deployBody()).requestId).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/)
  })

  it('clears the deployment marker when a deferred mutation is rejected by the Editor guard before fetch', async () => {
    renderPanel()
    const button = await saveFix(false)
    onlineManager.setOnline(false)
    let owner: string | undefined
    try {
      await confirmDeploy(button)
      await waitFor(() => expect(isWorkflowDeploymentPending(run.workflow_id)).toBe(true))
      act(() => { owner = beginEditorWorkflowWrite(run.workflow_id) })
      act(() => onlineManager.setOnline(true))
      await waitFor(() => expect(isWorkflowDeploymentPending(run.workflow_id)).toBe(false))
      expect(await screen.findByText(/数据库写入正在进行；请等待结果，原草稿仍保留/)).toBeInTheDocument()
      expect(requests('deploy')).toHaveLength(0)
      expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(run.workflow_id)}`)).toBeNull()
      expect(screen.queryByText(/结果可能未知/)).not.toBeInTheDocument()
    } finally {
      onlineManager.setOnline(true)
      if (owner) act(() => finishEditorWorkflowWrite(run.workflow_id, owner!))
    }
  })

  it('disables deployment if Editor finishes a write after the AutoHeal save', async () => {
    renderPanel()
    await saveFix(false)
    act(() => {
      const owner = beginEditorWorkflowWrite(run.workflow_id)
      finishEditorWorkflowWrite(run.workflow_id, owner)
    })
    expect(await screen.findByText(/保存后数据库写入状态已变化/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '部署修复' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新诊断' })).toBeEnabled()
    expect(requests('deploy')).toHaveLength(0)
  })

  it('does not invalidate a saved deployment for an Editor write to a different workflow', async () => {
    renderPanel()
    const button = await saveFix(false)
    act(() => { const owner = beginEditorWorkflowWrite('unrelated-workflow'); finishEditorWorkflowWrite('unrelated-workflow', owner) })
    expect(button).toBeEnabled()
    await confirmDeploy(button)
    expect(await screen.findByText('部署已完成')).toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(1)
  })

  it('does not adopt a later epoch when a write completes before the save success callback', async () => {
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    const afterSave = vi.fn(() => gate)
    queryClient = new QueryClient({ mutationCache: new MutationCache({ onSuccess: (_data, variables) => {
      if (variables && typeof variables === 'object' && 'spec' in variables) return afterSave()
    } }), defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    try {
      renderPanel()
      await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
      await screen.findByText(/兼容保存的数据库和 Git 写入范围/); await userEvent.click(await screen.findByRole('button', { name: '保存修复' }))
      await waitFor(() => expect(afterSave).toHaveBeenCalledOnce())
      act(() => { const owner = beginEditorWorkflowWrite(run.workflow_id); finishEditorWorkflowWrite(run.workflow_id, owner) })
      await act(async () => { release(); await gate })
      expect(await screen.findByText(/保存后数据库写入状态已变化/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '部署修复' })).toBeDisabled()
      expect(requests('deploy')).toHaveLength(0)
    } finally { release() }
  })

  it('rejects a queued deploy if an intervening Editor write completes before dispatch', async () => {
    renderPanel()
    const button = await saveFix(false)
    onlineManager.setOnline(false)
    try {
      await confirmDeploy(button)
      await waitFor(() => expect(isWorkflowDeploymentPending(run.workflow_id)).toBe(true))
      act(() => { const owner = beginEditorWorkflowWrite(run.workflow_id); finishEditorWorkflowWrite(run.workflow_id, owner) })
      act(() => onlineManager.setOnline(true))
      await waitFor(() => expect(isWorkflowDeploymentPending(run.workflow_id)).toBe(false))
      expect(await screen.findByText(/保存后数据库写入状态已变化/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '部署修复' })).toBeDisabled()
      expect(requests('deploy')).toHaveLength(0)
      expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(run.workflow_id)}`)).toBeNull()
    } finally { onlineManager.setOnline(true) }
  })

  it('blocks a repeat after a lost deploy response, including after remount', async () => {
    deployResponse = async () => response({ message: 'response lost' }, 503)
    renderPanel()
    await confirmDeploy(await saveFix(false))
    expect(await screen.findByText(/结果可能未知/)).toBeInTheDocument()
    expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(run.workflow_id)}`)).not.toBeNull()
    expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
    cleanup()
    renderPanel()
    await screen.findByText(/存在未确认的操作/)
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    expect(await screen.findByRole('button', { name: '保存修复' })).toBeDisabled()
    expect(requests('deploy')).toHaveLength(1)
  })

  it('keeps other workflow markers when the user explicitly confirms reconciliation for this workflow', async () => {
    const markerPrefix = 'clawweb.autoheal.review-blocked.v1.'
    localStorage.setItem(markerPrefix + 'other-workflow', '1')
    deployResponse = async () => response({ message: 'response lost' }, 503)
    renderPanel()
    await confirmDeploy(await saveFix(false))
    expect(await screen.findByText(/结果可能未知/)).toBeInTheDocument()
    const currentKey = markerPrefix + encodeURIComponent(run.workflow_id)
    const otherKey = markerPrefix + 'other-workflow'
    expect(localStorage.getItem(currentKey)).not.toBeNull()
    expect(localStorage.getItem(otherKey)).toBe('1')

    await userEvent.click(screen.getByRole('button', { name: '我已人工核对，清除当前工作流标记…' }))
    const confirm = screen.getByRole('button', { name: '清除当前工作流标记' })
    expect(confirm).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: /我已人工核对 Git 和数据库/ }))
    await userEvent.click(confirm)

    expect(localStorage.getItem(currentKey)).toBeNull()
    expect(localStorage.getItem(otherKey)).toBe('1')
    expect(screen.queryByText(/存在未确认的操作/)).not.toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(1)
  })

  it('removes legacy sensitive records without rendering them and blocks unsafe replacement', async () => {
    const prefix = 'clawweb.autoheal.operation.v1.'
    localStorage.setItem(prefix + run.workflow_id, JSON.stringify({ diagnosis: { rawResponse: 'SECRET-OTHER-USER', fixedSpec: { secret: 'placeholder' } } }))
    localStorage.setItem(prefix + 'another-workflow', JSON.stringify({ diagnosis: { summary: 'OTHER-SECRET' } }))
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('存在未确认的操作')
    expect(document.body.textContent).not.toContain('SECRET-OTHER-USER')
    expect(document.body.textContent).not.toContain('placeholder')
    expect(localStorage.getItem(prefix + run.workflow_id)).toBeNull()
    expect(localStorage.getItem(prefix + 'another-workflow')).toBeNull()
    expect(JSON.stringify(localStorage)).not.toContain('SECRET')
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    expect(await screen.findByRole('button', { name: '保存修复' })).toBeDisabled()
    expect(requests('save')).toHaveLength(0)
    cleanup(); renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('存在未确认的操作')
  })
  it('explains DB-only saving and deploys using the saved workflow and pack identifiers', async () => {
    renderPanel()
    const deployButton = await saveFix(false)
    expect(screen.getByText(/实际保存范围由宿主决定/)).toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(0)
    await confirmDeploy(deployButton)
    await waitFor(() => expect(requests('deploy')).toHaveLength(1))
    expect(await deployBody()).toEqual({
      workflowId: run.workflow_id, packId: 'pack-1', diagnosisId: diagnosis.diagnosisId,
      requestId: expect.any(String), note: 'auto-heal deploy', autoRun: false,
    })
    await screen.findByText('部署已完成')
    expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(run.workflow_id)}`)).toBeNull()
  })

  it.each([
    null,
    { ok: false, saved: true, workflowId: run.workflow_id, packId: 'pack-1' },
    { ok: true, saved: false, workflowId: run.workflow_id, packId: 'pack-1' },
    { ok: true, saved: true, workflowId: 'another-workflow', packId: 'pack-1' },
    { ok: true, saved: true, workflowId: run.workflow_id, packId: '' },
    { ok: true, saved: true, workflowId: run.workflow_id },
  ])('does not offer deployment for an unsuccessful or invalid save response: %o', async (result) => {
    saveResponse = async () => response(result && result.workflowId === 'cron-workflow' ? { ...result, workflowId: run.workflow_id } : result)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    await screen.findByText(/兼容保存的数据库和 Git 写入范围/); await userEvent.click(await screen.findByRole('button', { name: '保存修复' }))
    expect(await screen.findByText(/保存结果无效或工作流不匹配/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '部署修复' })).not.toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(0)
    expect(requests('run')).toHaveLength(0)
  })

  it('allows another compatibility save after the enterprise pre-write permission denial', async () => {
    saveResponse = async () => response({ error: 'Forbidden', message: 'No edit permission for this workflow' }, 403)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    await screen.findByText(/兼容保存的数据库和 Git 写入范围/)
    await userEvent.click(screen.getByRole('button', { name: '保存修复' }))
    expect(await screen.findByText('No edit permission for this workflow')).toBeInTheDocument()
    expect(screen.queryByText(/数据库写入结果未知/)).not.toBeInTheDocument()
    saveResponse = async () => response({ ok: true, saved: true, workflowId: run.workflow_id, packId: 'pack-1', branch: `wf/${run.workflow_id}`, commit: 'a'.repeat(40) })
    expect(screen.getByRole('button', { name: '保存修复' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: '保存修复' }))
    expect(await screen.findByRole('button', { name: '部署修复' })).toBeEnabled()
    expect(requests('save')).toHaveLength(2)
  })

  it('does not deploy when saving fails authorization', async () => {
    saveResponse = async () => response({ message: '无权保存' }, 403)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
    await screen.findByText(/兼容保存的数据库和 Git 写入范围/); await userEvent.click(await screen.findByRole('button', { name: '保存修复' }))
    expect(await screen.findByText('无权保存')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '部署修复' })).not.toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(0)
    expect(requests('run')).toHaveLength(0)
  })
  it('sends run.flow_id, not client bot/stage metadata, for a single manual rerun', async () => {
    const { onRerunComplete, rerenderRun } = renderPanel()
    const clientRun = { ...run, botId: 'retired-diagnostic-bot', stage: 'untrusted-stage' }
    rerenderRun(clientRun)
    await confirmDeploy(await saveFix(false))
    await userEvent.click(await screen.findByRole('button', { name: '重新运行' }))
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
  })

  it('never delegates automatic running to the old deploy callback', async () => {
    const { onRerunComplete } = renderPanel()
    await confirmDeploy(await saveFix())
    await waitFor(() => expect(requests('deploy')).toHaveLength(1))
    expect((await deployBody()).autoRun).toBe(false)
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
  })

  it('autoRun=false deploys only, then manually runs the original cron flow without input.command', async () => {
    const pendingRun = deferredResponse()
    runResponse = () => pendingRun.promise
    const { onRerunComplete } = renderPanel()
    await confirmDeploy(await saveFix(false))

    expect(await screen.findByText('部署已完成')).toBeInTheDocument()
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
    await userEvent.click(deployButton)
    const confirm = screen.getByRole('button', { name: '了解风险，确认部署' })
    act(() => { fireEvent.click(confirm); fireEvent.click(confirm) })
    await waitFor(() => expect(requests('deploy')).toHaveLength(1))
    expect((await deployBody())).toEqual({
      workflowId: run.workflow_id, packId: 'pack-1', diagnosisId: diagnosis.diagnosisId,
      requestId: expect.any(String), note: 'auto-heal deploy', autoRun: false,
    })
    expect(screen.getByText('正在部署修复…')).toBeInTheDocument()
    expect(requests('run')).toHaveLength(0)
    expect(onRerunComplete).not.toHaveBeenCalled()

    await act(async () => pendingDeploy.resolve(response(deployed)))
    await waitFor(() => expect(onRerunComplete).toHaveBeenCalledTimes(1))
    expectSourceRun()
    expect(requests('deploy')).toHaveLength(1)
  })

  it('shows a deployment compatibility error instead of reporting a deployment or rerunning', async () => {
    const message = '候选部署暂不可用：跨入口并发保护、Git/数据库一致性及 YAML 候选同步尚未完成，未执行部署。'
    deployResponse = async () => response({ error: 'Service Unavailable', code: 'CANDIDATE_DEPLOYMENT_UNAVAILABLE', message }, 503)
    const { onRerunComplete } = renderPanel()
    await confirmDeploy(await saveFix())
    expect(await screen.findByText((text) => text.includes(message))).toBeInTheDocument()
    expect(await deployBody()).not.toHaveProperty('candidateCommit')
    expect(requests('run')).toHaveLength(0)
    expect(onRerunComplete).not.toHaveBeenCalled()
    expect(screen.queryByText('部署已完成')).not.toBeInTheDocument()
  })

  it('does not rerun or report success when deploy returns an unsuccessful result', async () => {
    deployResponse = async () => response({ ...deployed, deployed: false })
    const { onRerunComplete } = renderPanel()
    await confirmDeploy(await saveFix())
    expect(await screen.findByText(/部署结果无法确认/)).toBeInTheDocument()
    expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(run.workflow_id)}`)).not.toBeNull()
    expect(requests('run')).toHaveLength(0)
    expect(screen.queryByText('部署已完成')).not.toBeInTheDocument()
    expect(onRerunComplete).not.toHaveBeenCalled()
  })

  it('never runs when deployment fails', async () => {
    deployResponse = async () => response({ message: '部署失败，未发布' }, 500)
    const { onRerunComplete } = renderPanel()
    await confirmDeploy(await saveFix())
    expect(await screen.findByText(/部署失败，未发布/)).toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(1)
    expect(requests('run')).toHaveLength(0)
    expect(screen.queryByText('部署已完成')).not.toBeInTheDocument()
    expect(onRerunComplete).not.toHaveBeenCalled()
  })

  it.each([true, false])('preserves the deployed fix and diagnosis after run failure (autoRun=%s); retries only run', async (autoRun) => {
    runResponse = async () => response({ message: 'BaaS 400: 原 Bot 暂不可用' }, 400)
    const { onRerunComplete } = renderPanel()
    await confirmDeploy(await saveFix(autoRun))
    if (!autoRun) await userEvent.click(await screen.findByRole('button', { name: '重新运行' }))

    expect(await screen.findByText('BaaS 400: 原 Bot 暂不可用')).toBeInTheDocument()
    expect(screen.getByText('部署已完成')).toBeInTheDocument()
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

it.each([
  ['missing capability endpoint', () => response({ code: 'NOT_FOUND' }, 404)],
  ['service failure', () => response({ code: 'CAPABILITIES_UNAVAILABLE' }, 503)],
  ['unknown capability', () => response({ autoHealUnifiedSave: 'unknown' })],
  ['invalid body', () => new Response('{', { status: 200 })],
  ['network failure', () => { throw new Error('offline') }],
])('blocks all saving when initial host capability is unknown: %s', async (_name, failure) => {
  const prior = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => url === '/api/auto-heal/capabilities' ? failure() : prior(url, init))
  renderPanel()
  await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('未发送保存')
  expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
  expect(screen.queryByRole('button', { name: '我已了解风险，继续兼容保存' })).not.toBeInTheDocument()
  expect(requests('save')).toHaveLength(0); expect(requests('unified-save')).toHaveLength(0)
})

it('blocks saving when initial host capability times out', async () => {
  const pending = deferredResponse(), prior = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => url === '/api/auto-heal/capabilities' ? pending.promise : prior(url, init))
  renderPanel(); vi.useFakeTimers()
  try {
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始诊断' })); await Promise.resolve(); await Promise.resolve() })
    await act(async () => { await vi.advanceTimersByTimeAsync(10_001) })
    expect(screen.getByRole('alert')).toHaveTextContent('未发送保存')
    expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '我已了解风险，继续兼容保存' })).not.toBeInTheDocument()
    expect(requests('save')).toHaveLength(0); expect(requests('unified-save')).toHaveLength(0)
  } finally { vi.useRealTimers() }
})

it.each([false, null])('keeps the explicitly disabled legacy capability %s compatible', async flag => {
  const prior = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => url === '/api/auto-heal/capabilities' ? response({ autoHealUnifiedSave: flag }) : prior(url, init))
  renderPanel()
  await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  await screen.findByText(/兼容保存的数据库和 Git 写入范围/)
  await userEvent.click(screen.getByRole('button', { name: '保存修复' }))
  expect(await screen.findByText(/实际保存范围由宿主决定/)).toBeInTheDocument()
  expect(requests('save')).toHaveLength(1); expect(requests('unified-save')).toHaveLength(0)
})

function mockCapabilityAndBaselines(options: { unavailable: boolean }) {
  const prior = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/capabilities') return response({ autoHealUnifiedSave: 'v1' })
    if (String(url).endsWith('/editor-draft')) return response({ workflowId: run.workflow_id, packId: 'pack-1', specJson: JSON.stringify({ id: run.workflow_id, title: 'original after diagnosis', nodes: [] }), draftToken: 'a'.repeat(64) })
    if (String(url).endsWith('/pack-version?version=draft')) return response({ workflowId: run.workflow_id, packId: 'pack-1', baseRef: `origin/wf/${run.workflow_id}`, baseCommit: 'b'.repeat(40), draftHead: 'b'.repeat(40), versionFiles: [] })
    if (url === '/api/auto-heal/unified-save' && options.unavailable) return response({ code: 'UNIFIED_SAVE_UNAVAILABLE' }, 503)
    return prior(url, init)
  })
}

async function prepareUnifiedReview() {
  await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  await screen.findByText(/保存前需要核对当前草稿/)
  expect(screen.getByRole('button', { name: '保存修复' }).closest('.max-w-2xl')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '保存修复' }))
  expect(await screen.findByRole('region', { name: '保存前核对' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: '当前数据库原始草稿' })).toHaveTextContent('original after diagnosis')
  expect(screen.getByText(/拟保存的完整修复内容/)).toBeInTheDocument()
  expect(requests('unified-save')).toHaveLength(0)
  expect(requests('save')).toHaveLength(0)
  const review = screen.getByRole('region', { name: '保存前核对' })
  expect(review.closest('.max-w-6xl')).toBeInTheDocument()
  expect(screen.getByRole('region', { name: '当前数据库原始草稿' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: '拟保存的完整修复内容' })).toBeInTheDocument()
}

it('releases the write guard on a pre-write unified authorization rejection without fallback', async () => {
  mockCapabilityAndBaselines({ unavailable: true }); renderPanel(); await prepareUnifiedReview()
  await userEvent.click(screen.getByRole('button', { name: '确认差异并保存' }))
  expect(await screen.findByText(/宿主未授权统一保存/)).toBeInTheDocument()
  expect(screen.queryByText(/数据库写入结果未知/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '保存修复' })).toBeEnabled()
  expect(requests('unified-save')).toHaveLength(1); expect(requests('save')).toHaveLength(0)
  await userEvent.click(screen.getByRole('button', { name: '保存修复' }))
  expect(await screen.findByRole('region', { name: '保存前核对' })).toBeInTheDocument()
})

it.each(['ok', 'first-saved', 'stale-token', 'stale-active', 'bad-ack', 'wf-production', 'legacy-draft'])('reviews and binds unified snapshot deployment (%s)', async scenario => {
  mockCapabilityAndBaselines({ unavailable: false })
  const prior = fetchMock.getMockImplementation()!
  const saved = 'd'.repeat(40), active = 'b'.repeat(40), token = 'e'.repeat(64)
  let savedDone = false
  const files = (title: string) => [
    { path: 'workflow.pack.yaml', mode: '100644', contentBase64: btoa(`id: pack-1\nworkflows:\n  - ${run.workflow_id}\n`) },
    { path: `workflows/${run.workflow_id}.yaml`, mode: '100644', contentBase64: btoa(`id: ${run.workflow_id}\ntitle: ${title}\nversion: '2'\nnodes: []\n`) },
    { path: 'scripts/test.sh', mode: '100644', contentBase64: btoa(title) },
  ]
  fetchMock.mockImplementation(async (url, init) => {
    if (!savedDone && scenario === 'first-saved' && String(url).endsWith('/pack-version?version=draft')) return response({ error: 'Snapshot not found', code: 'SNAPSHOT_NOT_FOUND' }, 404)
    if (!savedDone && scenario === 'first-saved' && String(url).endsWith('/pack-version?version=current')) return response({ workflowId: run.workflow_id, packId: 'pack-1', packPath: 'pack', baseRef: `refs/tags/deploy/${run.workflow_id}/#1`, baseCommit: active, draftHead: null, versionFiles: files('before') })
    if (url === '/api/auto-heal/unified-save') {
      const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init!.body as Blob) })
      expect(JSON.parse(text)).toMatchObject({
        specJson: JSON.stringify(diagnosis.fixedSpec), expectedDraftToken: 'a'.repeat(64),
        sourceVersion: scenario === 'first-saved' ? 'current' : 'draft', sourceCommit: active,
        expectedSavedHead: scenario === 'first-saved' ? null : active,
      })
      savedDone = true
      return response({ requestId: JSON.parse(text).requestId, outcome: 'confirmed', reconciliationRequired: false,
        evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'c'.repeat(64), commit: saved, draftToken: token } })
    }
    if (savedDone && String(url).endsWith('/editor-draft')) return response({ workflowId: run.workflow_id, packId: 'pack-1', specJson: JSON.stringify(diagnosis.fixedSpec), draftToken: scenario === 'stale-token' ? 'f'.repeat(64) : token })
    if (savedDone && String(url).includes('/pack-version?version=')) {
      const isDraft = String(url).endsWith('draft')
      return response({ workflowId: run.workflow_id, packId: 'pack-1', packPath: 'pack', baseRef: isDraft ? `${scenario === 'legacy-draft' ? 'origin/saved' : 'origin/wf'}/${run.workflow_id}` : scenario === 'wf-production' ? `origin/wf/${run.workflow_id}` : `refs/tags/deploy/${run.workflow_id}/#1`, baseCommit: isDraft ? saved : active, draftHead: saved, versionFiles: files(isDraft ? 'after' : 'before') })
    }
    if (String(url).includes('/snapshot-v2/state')) return response({ protocol: 'editor-deployment-state-v1', workflowId: run.workflow_id, packId: 'pack-1', canDeploy: true, savedCommit: saved, activeCommit: scenario === 'stale-active' ? 'f'.repeat(40) : active })
    if (url === '/api/auto-heal/editor-deploy/snapshot-v2') {
      const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init!.body as Blob) })
      return response({ ...deployed, requestId: JSON.parse(text).requestId, snapshotProtocol: 'saved-tree-v2', candidateCommit: scenario === 'bad-ack' ? active : saved, expectedActiveCommit: active })
    }
    return prior(url, init)
  })
  renderPanel(); await prepareUnifiedReview()
  await userEvent.click(screen.getByRole('button', { name: '确认差异并保存' }))
  await userEvent.click(await screen.findByRole('button', { name: '部署修复' }))
  if (['stale-token', 'stale-active', 'wf-production', 'legacy-draft'].includes(scenario)) {
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(requests('editor-deploy/snapshot-v2')).toHaveLength(0)
  } else {
    expect(await screen.findByText('更新：scripts/test.sh')).toBeInTheDocument()
    expect(screen.getByText(new RegExp(`当前生效 tag：.*deploy/${run.workflow_id}/#1`))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认部署' })).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
    await userEvent.click(screen.getByRole('button', { name: '确认部署' }))
    await waitFor(() => expect(requests('editor-deploy/snapshot-v2')).toHaveLength(1))
    const body = requests('editor-deploy/snapshot-v2')[0][1]!.body as Blob
    const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(body) })
    expect(JSON.parse(text)).toMatchObject({ workflowId: run.workflow_id, packId: 'pack-1', candidateCommit: saved, expectedDraftToken: token, expectedActiveCommit: active, snapshotProtocol: 'saved-tree-v2', confirmedFileDiff: true })
    if (scenario === 'bad-ack') expect(await screen.findByText(/部署结果无法确认/)).toBeInTheDocument()
    else await waitFor(() => expect(requests('run')).toHaveLength(1))
  }
  expect(requests('deploy')).toHaveLength(0); expect(requests('save')).toHaveLength(0)
})

it('does not send a save while capability negotiation is pending', async () => {
  const pending = deferredResponse(), prior = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => url === '/api/auto-heal/capabilities' ? pending.promise : prior(url, init))
  renderPanel(); await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  expect(await screen.findByText(/正在确认宿主保存能力/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
  expect(requests('save')).toHaveLength(0); expect(requests('unified-save')).toHaveLength(0)
  await act(async () => pending.resolve(response({ autoHealUnifiedSave: false })))
  expect(await screen.findByText(/兼容保存的数据库和 Git 写入范围/)).toBeInTheDocument()
})

it('cancels the displayed review without sending and requires a fresh review', async () => {
  mockCapabilityAndBaselines({ unavailable: false }); renderPanel(); await prepareUnifiedReview()
  expect(screen.getAllByText(/title/).length).toBeGreaterThan(0)
  await userEvent.click(screen.getByRole('button', { name: '取消核对' }))
  expect(screen.queryByRole('button', { name: '确认差异并保存' })).not.toBeInTheDocument()
  expect(requests('unified-save')).toHaveLength(0); expect(requests('save')).toHaveLength(0)
  expect(screen.getByRole('button', { name: '保存修复' }).closest('.max-w-2xl')).toBeInTheDocument()
  const previousReads = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/editor-draft')).length
  await userEvent.click(screen.getByRole('button', { name: '保存修复' }))
  expect(await screen.findByRole('region', { name: '保存前核对' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/editor-draft'))).toHaveLength(previousReads + 1)
  expect(requests('unified-save')).toHaveLength(0); expect(requests('save')).toHaveLength(0)
})

it('requires explicit confirmation that legacy deployment may run a different draft', async () => {
  renderPanel(); const button = await saveFix(true)
  await userEvent.click(button)
  expect(screen.getByRole('dialog', { name: '确认兼容部署' })).toHaveTextContent('将部署服务端此刻读取的草稿，不保证等于本次修复')
  expect(screen.getByRole('dialog')).toHaveTextContent('自动运行可能验证不同内容')
  expect(requests('deploy')).toHaveLength(0); expect(requests('run')).toHaveLength(0)
  await userEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(requests('deploy')).toHaveLength(0)
  await userEvent.click(button)
  await userEvent.click(screen.getByRole('button', { name: '了解风险，确认部署' }))
  await waitFor(() => expect(requests('deploy')).toHaveLength(1))
})

it('rechecks the local guard after opening the compatibility confirmation', async () => {
  renderPanel(); const button = await saveFix(false)
  await userEvent.click(button)
  act(() => { const owner = beginEditorWorkflowWrite(run.workflow_id); finishEditorWorkflowWrite(run.workflow_id, owner) })
  await userEvent.click(screen.getByRole('button', { name: '了解风险，确认部署' }))
  expect((await screen.findAllByText(/保存后数据库写入状态已变化/)).length).toBeGreaterThan(0)
  expect(requests('deploy')).toHaveLength(0)
})

it.each(['fetch', 'body'] as const)('retains the browser deployment marker after a %s timeout, including a late success', async stage => {
  const pending = deferredResponse()
  let streamController: ReadableStreamDefaultController<Uint8Array> | undefined
  if (stage === 'fetch') deployResponse = () => pending.promise
  else {
    const stream = new ReadableStream<Uint8Array>({ start(controller) { streamController = controller } })
    deployResponse = async () => new Response(stream)
  }
  renderPanel(); const button = await saveFix(false)
  await userEvent.click(button)
  const marker = `clawweb.autoheal.review-blocked.v1.${encodeURIComponent(run.workflow_id)}`
  vi.useFakeTimers()
  try {
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '了解风险，确认部署' })); await Promise.resolve(); await Promise.resolve() })
    expect(requests('deploy')).toHaveLength(1)
    expect(localStorage.getItem(marker)).not.toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(AUTO_HEAL_LEGACY_TIMEOUT_MS + 1) })
    expect(screen.getByText(/兼容部署超时，结果未知/)).toBeInTheDocument()
    expect(isWorkflowDeploymentPending(run.workflow_id)).toBe(false)
    expect(localStorage.getItem(marker)).not.toBeNull()
    expect(requests('run')).toHaveLength(0)
    await act(async () => {
      if (stage === 'fetch') pending.resolve(response(deployed))
      else { streamController!.enqueue(new TextEncoder().encode(JSON.stringify(deployed))); streamController!.close() }
      await Promise.resolve(); await Promise.resolve()
    })
    expect(localStorage.getItem(marker)).not.toBeNull()
    expect(requests('deploy')).toHaveLength(1); expect(requests('run')).toHaveLength(0)
  } finally { vi.useRealTimers() }
})

it('keeps compatibility save blocked after a timeout and remount, even if a late save succeeds', async () => {
  const pending = deferredResponse()
  saveResponse = () => pending.promise
  renderPanel()
  await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  await screen.findByText(/兼容保存的数据库和 Git 写入范围/)
  vi.useFakeTimers()
  try {
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '保存修复' })); await Promise.resolve(); await Promise.resolve() })
    expect(requests('save')).toHaveLength(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(AUTO_HEAL_LEGACY_TIMEOUT_MS + 1) })
    expect(screen.getByText(/兼容保存超时，结果未知/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
    await act(async () => { pending.resolve(response({ ok: true, saved: true, workflowId: run.workflow_id, packId: 'pack-1', branch: `wf/${run.workflow_id}`, commit: 'a'.repeat(40) })); await Promise.resolve(); await Promise.resolve() })
    expect(requests('save')).toHaveLength(1)
    cleanup(); renderPanel()
    expect(screen.getByText(/数据库写入结果未知/)).toBeInTheDocument()
    expect(requests('deploy')).toHaveLength(0)
  } finally { vi.useRealTimers() }
})

it('blocks DB-only fallback when the enterprise workflow has no Git baseline', async () => {
  const prior = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/capabilities') return response({ autoHealUnifiedSave: 'v1-workflow' })
    if (String(url).startsWith('/api/auto-heal/capabilities?workflowId=')) return response({ autoHealUnifiedSave: 'baseline-unavailable' })
    return prior(url, init)
  })
  renderPanel(); await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  expect(await screen.findByText(/缺少数据库草稿或草稿\(wf\)或生效 tag 基线/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
  expect(screen.queryByRole('button', { name: '我已了解风险，继续兼容保存' })).not.toBeInTheDocument()
  expect(requests('save')).toHaveLength(0); expect(requests('unified-save')).toHaveLength(0)
})

it.each([
  ['unauthorized', () => response({ code: 'AUTHENTICATION_REQUIRED' }, 401)],
  ['forbidden', () => response({ code: 'FORBIDDEN' }, 403)],
  ['malformed JSON', () => new Response('{', { status: 200 })],
  ['server failure', () => response({ code: 'CAPABILITIES_UNAVAILABLE' }, 503)],
  ['invalid body', () => response({ unknown: 'response' })],
  ['network failure', () => { throw new Error('offline') }],
])('never offers DB-only fallback after v1-workflow discovery and scoped %s', async (_name, scopedResponse) => {
  const prior = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/capabilities') return response({ autoHealUnifiedSave: 'v1-workflow' })
    if (String(url).startsWith('/api/auto-heal/capabilities?workflowId=')) return scopedResponse()
    return prior(url, init)
  })
  renderPanel(); await userEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('未发送保存')
  expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
  expect(screen.queryByRole('button', { name: '我已了解风险，继续兼容保存' })).not.toBeInTheDocument()
  expect(requests('save')).toHaveLength(0); expect(requests('unified-save')).toHaveLength(0)
})

it('never offers DB-only fallback after a workflow-scoped capability timeout', async () => {
  const pending = deferredResponse(), prior = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/capabilities') return response({ autoHealUnifiedSave: 'v1-workflow' })
    if (String(url).startsWith('/api/auto-heal/capabilities?workflowId=')) return pending.promise
    return prior(url, init)
  })
  renderPanel(); vi.useFakeTimers()
  try {
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始诊断' })); await Promise.resolve(); await Promise.resolve() })
    await act(async () => { await vi.advanceTimersByTimeAsync(10_001) })
    expect(screen.getByRole('alert')).toHaveTextContent('未发送保存')
    expect(screen.getByRole('button', { name: '保存修复' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '我已了解风险，继续兼容保存' })).not.toBeInTheDocument()
    expect(requests('save')).toHaveLength(0); expect(requests('unified-save')).toHaveLength(0)
  } finally { vi.useRealTimers() }
})
