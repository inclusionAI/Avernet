import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import AutoHealPanel from '../AutoHealPanel'
import { savePackVersion } from '../../editor/packVersionSave'
import { isWorkflowDeploymentPending } from '../../editor/deploymentGuard'
import { checkpointOtherPackSessions, hasPostSaveVerificationError, publish, releaseCleanSession, sessionFor, type Session } from '../../editor/packVersionSession'

let sequence = 0
let id: string, client: QueryClient
let ownedSessions: Array<{ id: string; session: Session }> = []
const commit = 'a'.repeat(40)
const fetchMock = vi.fn<typeof fetch>()
const response = (body: unknown) => new Response(JSON.stringify(body))
const readBlob = (blob: Blob) => new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) })
const requests = (suffix: string) => fetchMock.mock.calls.filter(([url]) => String(url) === `/api/auto-heal/${suffix}`)
const file = (path: string, content: string) => ({ path, contentBase64: btoa(content), mode: '100644' as const })
function deferred() { let resolve!: () => void; const promise = new Promise<void>(r => { resolve = r }); return { promise, resolve } }
function renderPanel() {
  const run: FlowRun = { flow_id: 'lock-run', workflow_id: id, workflow_title: 'Workflow', status: 'failed', triggered_by: 'manual', node_count: 1,
    succeeded_count: 0, failed_count: 1, total_duration_ms: 1, total_token_usage: null, started_at: '2026-01-01T00:00:00Z', completed_at: null,
    user_id: null, origin_bot_id: null, plugin_version: null, engine: null, workflow_version: 1, workflow_deploy_number: 1 }
  return render(<QueryClientProvider client={client}><AutoHealPanel run={run} onClose={() => {}} /></QueryClientProvider>)
}
async function diagnose() {
  fireEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  const button = await screen.findByRole('button', { name: '保存修复' })
  await screen.findByText(/兼容保存的数据库和 Git 写入范围/)
  return button
}
async function saveFix() {
  fireEvent.click(await diagnose())
  return screen.findByRole('button', { name: '部署修复' })
}
/** Real Git save pipeline: successful HTTP acknowledgement, then DB postflight failure. */
async function failPostflight() {
  const files = [file('workflow.pack.yaml', stringify({ id: 'pack', workflows: [id] })), file(`workflows/${id}.yaml`, stringify({ id, title: 'Workflow', nodes: [] })), file('scripts/run', 'old')]
  const snapshot = { workflowId: id, packId: 'pack', canWrite: true, baseRef: `origin/wf/${id}`, baseCommit: commit, draftHead: commit, versionFiles: files }
  const session = sessionFor(id)
  ownedSessions.push({ id, session })
  const changed = files.map(item => item.path === 'scripts/run' ? file(item.path, 'new') : item)
  act(() => publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions,
    draft: { snapshot, files: changed, loading: false, error: null, blocked: false, notice: null } } }))
  await act(async () => { expect(await savePackVersion(session, id, 'draft', changed, undefined, async () => { throw new Error('DB postflight mismatch') })).not.toBeNull() })
  expect(session.state.postSaveVerificationError).toBeTruthy()
  return session
}
beforeEach(() => {
  id = `autoheal-lock-${++sequence}`; ownedSessions = []; localStorage.clear()
  client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
  fetchMock.mockReset().mockImplementation(async (url, init) => {
    if (String(url) === `/api/workflows/${id}/pack-version`) {
      const body = JSON.parse(await readBlob(init!.body as Blob))
      return response({ branch: `wf/${id}`, commit, packId: 'pack', requestId: body.requestId, version: 1 })
    }
    if (url === '/api/auto-heal/capabilities') return response({ autoHealUnifiedSave: false })
    if (url === '/api/auto-heal/diagnose') return response({ ok: true, workflowId: id, diagnosisId: 'diagnosis', summary: 'repair', errorChain: [], fixSuggestions: [], fixedYaml: '', fixedSpec: { id, title: 'Workflow', nodes: [] }, diff: [], rawResponse: '' })
    if (url === '/api/auto-heal/save') return response({ ok: true, saved: true, workflowId: id, packId: 'pack', branch: `wf/${id}`, commit })
    if (url === '/api/auto-heal/deploy') return response({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 1, version: 1, tagName: 'v1' })
    throw new Error(`Unexpected request ${String(url)}`)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup(); client.clear()
  for (const { id: sessionId, session } of ownedSessions) {
    const versions = { ...session.state.versions }
    for (const version of ['current', 'draft'] as const) {
      const entry = versions[version]
      versions[version] = { ...entry, blocked: false, files: entry.snapshot?.versionFiles ?? [] }
    }
    publish(session, { ...session.state, versions, postSaveVerificationError: null })
    releaseCleanSession(sessionId, session)
  }
  vi.unstubAllGlobals(); vi.restoreAllMocks()
})

describe('AutoHeal respects the Editor postflight lock', () => {
  it('does not create a Pack session when merely checking an unvisited workflow', () => {
    const unchanged = checkpointOtherPackSessions('__no-workflow__')
    expect(hasPostSaveVerificationError(`${id}-absent`)).toBe(false)
    expect(unchanged()).toBe(true)
    const clean = sessionFor(`${id}-clean`)
    expect(hasPostSaveVerificationError(`${id}-clean`)).toBe(false)
    releaseCleanSession(`${id}-clean`, clean)
    const recreated = sessionFor(`${id}-clean`)
    expect(recreated).not.toBe(clean)
    releaseCleanSession(`${id}-clean`, recreated)
  })
  it('keeps a real failed postflight locked after leaving the editor and blocks AutoHeal save', async () => {
    const session = await failPostflight()
    releaseCleanSession(id, session)
    expect(sessionFor(id)).toBe(session)
    renderPanel()
    expect(await screen.findByText(/数据库后置核对失败/)).toBeInTheDocument()
    expect(await diagnose()).toBeDisabled()
    expect(requests('save')).toHaveLength(0); expect(requests('deploy')).toHaveLength(0)
    expect(session.state.postSaveVerificationError).toBeTruthy()
  })
  it('blocks deploy when the postflight lock appears after AutoHeal save', async () => {
    renderPanel(); await saveFix(); await failPostflight()
    expect(await screen.findByText(/数据库后置核对失败/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '部署修复' })).toBeDisabled()
    expect(requests('save')).toHaveLength(1); expect(requests('deploy')).toHaveLength(0)
  })
  it.each(['save', 'deploy'] as const)('rechecks at real %s mutation serialization after the click', async operation => {
    const gate = deferred(), entered = deferred()
    client = new QueryClient({ mutationCache: new MutationCache({ onMutate: async variables => {
      const note = (variables as { note?: string } | null)?.note
      if (note === `auto-heal ${operation}`) { entered.resolve(); await gate.promise }
    } }), defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    renderPanel()
    const button = operation === 'save' ? await diagnose() : await saveFix()
    fireEvent.click(button)
    if (operation === 'deploy') fireEvent.click(screen.getByRole('button', { name: '了解风险，确认部署' }))
    await entered.promise
    expect(requests(operation)).toHaveLength(0)
    await failPostflight()
    await act(async () => { gate.resolve(); await gate.promise })
    await waitFor(() => expect(screen.queryByText(operation === 'save' ? '正在保存修复…' : '正在部署修复…')).not.toBeInTheDocument())
    expect(requests(operation)).toHaveLength(0)
    if (operation === 'deploy') {
      expect(isWorkflowDeploymentPending(id)).toBe(false)
      expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${id}`)).toBeNull()
    }
  })
})
