import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { useAutoHealDeploy as useRealDeploy } from '../../api/autoHealWrites'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import { stringify } from 'yaml'
const mocks = vi.hoisted(() => ({ spec: null as any, baseline: null as any, casGet: vi.fn(), snapshot: null as any, deploymentState: null as any, fetchJson: vi.fn(), get: vi.fn(), deploy: vi.fn() }))
vi.mock('../../api/hooks', async () => {
  const { useAutoHealSave } = await vi.importActual<typeof import('../../api/autoHealWrites')>('../../api/autoHealWrites')
  return { useEditorWorkflowDocument: () => ({ data: mocks.spec, databaseSpec: mocks.baseline, isLoading: false }), useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, mutate: (input: any, callbacks: any) => { void mocks.deploy(input).then(callbacks.onSuccess, callbacks.onError) }, isPending: false }), useAutoHealDiagnose: () => ({ mutate: (_: any, callbacks: any) => callbacks.onSuccess({ ok: true, workflowId: mocks.spec.id, diagnosisId: 'd', summary: 'diagnosis', errorChain: [], fixSuggestions: [], fixedYaml: '', fixedSpec: mocks.spec, diff: [], rawResponse: '' }) }), useAutoHealSave }
})
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => null }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: (url: string, init?: RequestInit) => url === '/api/auto-heal/capabilities' ? Promise.resolve({ autoHealUnifiedSave: false }) : url.includes('/snapshot-v2/state') ? Promise.resolve(mocks.deploymentState) : url.endsWith('/editor-draft') && init?.method === 'GET'
  ? (mocks.casGet(url, init), Promise.resolve({ workflowId: mocks.spec.id, packId: 'pack', specJson: JSON.stringify(mocks.spec), draftToken: 'a'.repeat(64) }))
  : mocks.fetchJson(url, init), api: { workflows: { get: mocks.get, getHistory: async () => ({ history: [], active: null }), getDeploySnapshot: vi.fn() } } }))
vi.mock('../../api/useAutoHealSourceRun', () => ({ useAutoHealSourceRun: () => ({ mutate: vi.fn(), isPending: false }) }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { readDatabaseWorkflowDraft } from '../../api/editorWorkflowSource'
import { useEditorStore } from '../../editor/store'
import { sessionFor, publish, releaseCleanSession } from '../../editor/packVersionSession'
import { createWorkflowPack } from '../../editor/newWorkflowSave'
import { assertDeploymentAvailable, clearReconciledDeployment } from '../../editor/deploymentGuard'
import Editor from '../Editor'
import AutoHealPanel from '../../components/AutoHealPanel'
let sequence = 0
async function setup(source: 'draft' | 'current' = 'draft', fresh = false, existingId?: string) {
  const id = existingId ?? `deploy-db-${++sequence}`, commit = 'a'.repeat(40)
  mocks.spec = { id, title: 'DB draft', version: '1', nodes: [] }
  mocks.snapshot = { packPath: '', workflowId: id, packId: 'pack', canWrite: true, baseRef: `origin/wf/${id}`, baseCommit: commit, draftHead: commit,
    versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' },
      { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify({ ...mocks.spec, title: 'Old Git' })), mode: '100644' }] }
  mocks.deploymentState = { protocol: 'editor-deployment-state-v1', workflowId: id, packId: 'pack', canDeploy: true, savedCommit: commit, activeCommit: fresh ? null : commit }
  if (source === 'current') mocks.snapshot = { ...mocks.snapshot, baseRef: `refs/tags/deploy/${id}/#1`, draftHead: null }
  useEditorStore.getState().reset({ force: true })
  const session = sessionFor(id)
  if (!fresh) publish(session, { ...session.state, version: source, versions: { ...session.state.versions,
    [source]: { snapshot: mocks.snapshot, files: structuredClone(mocks.snapshot.versionFiles), loading: false, blocked: false, error: null, notice: null } } })
  mocks.fetchJson.mockReset(); mocks.deploy.mockReset(); mocks.get.mockReset()
  mocks.fetchJson.mockImplementation(async (url: string) => {
    if (fresh && url.includes('/snapshot-v2/state')) return { protocol: 'editor-deployment-state-v1', workflowId: id, savedCommit: mocks.snapshot.baseCommit, activeCommit: null }
    if (fresh && url.endsWith('version=current')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    if (source === 'current' && url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    if (url.endsWith('version=current') && source === 'draft') return { ...mocks.snapshot, baseRef: `refs/tags/deploy/${id}/#1`, draftHead: null }
    return mocks.snapshot
  }); mocks.get.mockResolvedValue(mocks.spec)
  mocks.baseline = await readDatabaseWorkflowDraft(id)
  useEditorStore.getState().loadSpec(mocks.spec, mocks.baseline)
  mocks.get.mockClear(); mocks.fetchJson.mockClear(); mocks.casGet.mockClear()
  const view = render(<MemoryRouter><Editor embedded initialWorkflowId={id} /></MemoryRouter>)
  return { id, session, view }
}
beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks() })
it('allows deployment confirmation without including unsaved canvas edits', async () => {
  await setup(); await act(async () => {}); act(() => useEditorStore.getState().updateSpecField('title', 'Unsaved'))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' })
  expect(screen.getByText(/未保存的修改，本次部署不包含这些修改/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('deploys the saved files and DB draft even when Git YAML differs', async () => {
  const { id } = await setup()
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 2, version: 2, tagName: 'wf-tag', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: id, candidateCommit: 'a'.repeat(40) })))
  expect(mocks.fetchJson.mock.calls.map(([url]) => url)).toEqual([`/api/workflows/${id}/pack-version?version=draft`, `/api/workflows/${id}/pack-version?version=current`])
  expect(mocks.casGet).toHaveBeenCalledWith(`/api/workflows/${id}/editor-draft`, expect.objectContaining({ method: 'GET' }))
  expect(mocks.casGet.mock.invocationCallOrder[0]).toBeLessThan(mocks.deploy.mock.invocationCallOrder[0])
  expect(await screen.findByText(/部署成功/)).toBeInTheDocument()
})
function realConflictTransport(gate?: Promise<void>, editorGuard = true) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  // Match Editor: its guarded transport preserves the bound request identity.
  const hook = renderHook(() => useRealDeploy({ editorGuard }), { wrapper })
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementationOnce(async (_url, init) => {
    expect(_url).toBe(editorGuard ? '/api/auto-heal/editor-deploy/snapshot-v2' : '/api/auto-heal/deploy')
    const input = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init!.body as Blob) })
    if (gate) await gate
    return new Response(JSON.stringify({ code: 'DEPLOY_TREE_CONFLICT', requestId: JSON.parse(input).requestId }), { status: 409 })
  })
  return { deploy: (input: Parameters<typeof hook.result.current.mutateAsync>[0]) => hook.result.current.mutateAsync(input), restore: () => fetchSpy.mockRestore() }
}

it('unlocks only the rejected Editor attempt and allows deployment after the candidate is fixed', async () => {
  const { id, session } = await setup()
  const otherKey = 'clawweb.autoheal.review-blocked.v1.other'
  localStorage.setItem(otherKey, '1')
  const transport = realConflictTransport()
  mocks.deploy.mockImplementationOnce(transport.deploy).mockImplementationOnce(async input => ({
    ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 1, version: 1, tagName: 'v1', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit,
  }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(1))
  await waitFor(() => expect(session.state.deployment).toBeNull())
  expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${id}`)).toBeNull()
  transport.restore()
  expect(localStorage.getItem(otherKey)).toBe('1')
  expect(screen.queryByText(/没有日志恢复/)).not.toBeInTheDocument()
  // Model a separately repaired, newly 草稿(wf) Pack; the next confirmation must use it.
  const active = { ...mocks.snapshot, baseRef: `refs/tags/deploy/${id}/#1`, draftHead: null }
  const repaired = { ...mocks.snapshot, baseCommit: 'b'.repeat(40), draftHead: 'b'.repeat(40) }
  mocks.snapshot = repaired
  mocks.deploymentState = { ...mocks.deploymentState, savedCommit: repaired.baseCommit, activeCommit: active.baseCommit }
  mocks.fetchJson.mockImplementation(async (url: string) => url.endsWith('version=current') ? active : repaired)
  act(() => publish(session, { ...session.state, versions: { ...session.state.versions,
    draft: { ...session.state.versions.draft, snapshot: repaired, files: structuredClone(repaired.versionFiles) } } }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  expect(await screen.findByText(/部署成功/)).toBeInTheDocument()
  expect(mocks.deploy).toHaveBeenCalledTimes(2)
  expect(mocks.deploy.mock.calls[1][0]).toEqual(expect.objectContaining({
    workflowId: id, candidateCommit: 'b'.repeat(40), expectedActiveCommit: 'a'.repeat(40),
    snapshotProtocol: 'saved-tree-v2', confirmedFileDiff: true,
  }))
})

it.each(['conflict', 'conflict marker failure', 'success', 'success marker failure', 'unknown'] as const)(
  'isolates a late %s response from a replacement session while preserving workflow safety', async outcome => {
    const { id, session, view } = await setup()
    const key = `clawweb.autoheal.review-blocked.v1.${id}`
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    const conflict = outcome.startsWith('conflict') ? realConflictTransport(gate) : null
    mocks.deploy.mockImplementation(async input => {
      if (conflict) return conflict.deploy(input)
      await gate
      if (outcome === 'unknown') throw new Error('late unknown response')
      return { ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 2, version: 2, tagName: 'wf-tag', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit }
    })
    fireEvent.click(screen.getByRole('button', { name: '部署' }))
    await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
    await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(1))
    expect(localStorage.getItem(key)).not.toBeNull()
    const originalSession = useEditorStore.getState().sessionId
    act(() => { useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec({ ...mocks.spec, title: 'New session' }) })
    expect(useEditorStore.getState().sessionId).not.toBe(originalSession)
    expect(screen.getByRole('status', { name: '部署操作' })).toHaveTextContent('部署操作正在进行')
    expect(screen.queryByRole('button', { name: /我已人工核对/ })).not.toBeInTheDocument()
    expect(() => clearReconciledDeployment(id, { gitAndDatabaseConfirmed: true })).toThrow(/正在进行/)
    view.unmount()
    render(<MemoryRouter><Editor embedded initialWorkflowId={id} /></MemoryRouter>)
    expect(screen.getByRole('status', { name: '部署操作' })).toHaveTextContent('部署操作正在进行')
    expect(screen.queryByRole('button', { name: /我已人工核对/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(mocks.deploy).toHaveBeenCalledTimes(1)
    const remove = outcome.includes('marker failure')
      ? vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('storage failure') }) : null
    try {
      await act(async () => { release(); await gate })
      await waitFor(() => expect(session.state.deployment?.pending).not.toBe(true))
      expect(screen.queryByText(/已阻止部署|部署成功/)).not.toBeInTheDocument()
      if (outcome === 'conflict' || outcome === 'success') {
        expect(screen.queryByRole('alert', { name: '部署操作' })).not.toBeInTheDocument()
        expect(localStorage.getItem(key)).toBeNull()
        expect(session.state.deployment).toBeNull()
      } else {
        expect(localStorage.getItem(key)).not.toBeNull()
        expect(session.state.deployment?.pending).toBe(false)
        expect(screen.getByRole('alert', { name: '部署操作' })).toBeInTheDocument()
        expect(screen.getByRole('button', { name: /我已人工核对/ })).toBeInTheDocument()
      }
    } finally { remove?.mockRestore(); conflict?.restore() }
  },
)

it.each(['success', 'conflict', 'unknown'] as const)('does not clear a replacement marker or overwrite a newer request after late %s', async outcome => {
  const { id, session } = await setup()
  const key = `clawweb.autoheal.review-blocked.v1.${id}`
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const conflict = outcome === 'conflict' ? realConflictTransport(gate) : null
  mocks.deploy.mockImplementation(async input => {
    if (conflict) return conflict.deploy(input)
    await gate
    if (outcome === 'unknown') throw new Error('lost')
    return { ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 2, version: 2, tagName: 'wf-tag' }
  })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(1))
  const old = session.state.deployment!
  localStorage.setItem(key, 'newer-marker')
  const replacement = { ...old, input: { ...old.input, requestId: 'newer-request' }, pending: false, error: 'newer state' }
  act(() => publish(session, { ...session.state, deployment: replacement }))
  try {
    await act(async () => { release(); await gate })
    await waitFor(() => expect(screen.queryByText(/部署操作正在进行/)).not.toBeInTheDocument())
    expect(localStorage.getItem(key)).toBe('newer-marker')
    expect(session.state.deployment).toBe(replacement)
    expect(screen.getByRole('alert', { name: '部署操作' })).toHaveTextContent('newer state')
  } finally { conflict?.restore() }
})

it('does not clear a changed marker when the old successful request still owns the in-memory slot', async () => {
  const { id, session } = await setup()
  const key = `clawweb.autoheal.review-blocked.v1.${id}`
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  mocks.deploy.mockImplementation(async () => { await gate; return { ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 2, version: 2, tagName: 'wf-tag' } })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(1))
  const old = session.state.deployment
  localStorage.setItem(key, 'newer-marker')
  await act(async () => { release(); await gate })
  await waitFor(() => expect(screen.getByRole('alert', { name: '部署操作' })).toHaveTextContent('部署结果未知'))
  expect(localStorage.getItem(key)).toBe('newer-marker')
  expect(session.state.deployment).toBe(old)
  fireEvent.click(screen.getByRole('button', { name: /我已人工核对/ }))
  expect(screen.getByText(/部署请求与核对标记不匹配/)).toBeInTheDocument()
  expect(screen.queryByRole('dialog', { name: '清除浏览器核对标记' })).not.toBeInTheDocument()
  expect(localStorage.getItem(key)).toBe('newer-marker')
})

it('refuses reconciliation when the marker changes while the confirmation dialog is open', async () => {
  const { id } = await setup()
  const key = `clawweb.autoheal.review-blocked.v1.${id}`
  mocks.deploy.mockRejectedValue(new Error('lost'))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  fireEvent.click(await screen.findByRole('button', { name: /我已人工核对/ }))
  localStorage.setItem(key, 'newer-marker')
  fireEvent.click(screen.getByRole('checkbox', { name: /我已人工核对 Git 和数据库/ }))
  fireEvent.click(screen.getByRole('button', { name: '清除当前工作流标记' }))
  expect(await screen.findByText(/已确认人工核对，但无法清除浏览器核对标记/)).toBeInTheDocument()
  expect(localStorage.getItem(key)).toBe('newer-marker')
})

it('does not unlock for an untrusted error mimicking the conflict response', async () => {
  const { id, session } = await setup()
  mocks.deploy.mockRejectedValue(Object.assign(new Error('API 409: {"code":"DEPLOY_TREE_CONFLICT"}'),
    { status: 409, body: '{"code":"DEPLOY_TREE_CONFLICT"}' }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  expect(await screen.findByText(/没有日志恢复/)).toBeInTheDocument()
  expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${id}`)).not.toBeNull()
  expect(session.state.deployment).not.toBeNull()
})

it('keeps unknown deployment visible and never offers journal recovery or retries', async () => {
  await setup(); mocks.deploy.mockRejectedValue(new Error('lost'))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  expect(await screen.findByText(/没有日志恢复或安全自动重试/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /恢复部署/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(mocks.deploy).toHaveBeenCalledTimes(1)
  expect(mocks.fetchJson.mock.calls.every(([url]) => !url.includes('pack-operations'))).toBe(true)
})
it('blocks deployment when wf draft is absent instead of falling back to the active tag', async () => {
  const { id } = await setup('current')
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/无法确认数据库草稿和草稿文件，已阻止部署/)).toBeInTheDocument()
  expect(mocks.fetchJson.mock.calls.map(([url]) => url)).toEqual([
    `/api/workflows/${id}/pack-version?version=draft`, `/api/workflows/${id}/pack-version?version=current`,
  ])
  expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  expect(screen.queryByText(/部署成功/)).not.toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it.each([
  ['generic 404', { status: 404, body: 'not found' }],
  ['wrong 404 code', { status: 404, body: '{"code":"OTHER"}' }],
  ['forbidden', { status: 403, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }],
  ['unavailable', { status: 503, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }],
  ['network failure', new Error('offline')],
])('never falls back on %s', async (_label, failure) => {
  const { id } = await setup('current')
  mocks.fetchJson.mockRejectedValue(failure)
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/已阻止部署/)).toBeInTheDocument()
  expect(mocks.fetchJson.mock.calls.map(([url]) => url)).toEqual([`/api/workflows/${id}/pack-version?version=draft`, `/api/workflows/${id}/pack-version?version=current`])
  expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('refuses current when saved exists or appeared after the draft 404', async () => {
  await setup('current')
  mocks.fetchJson.mockImplementation(async (url: string) => {
    if (url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    return { ...mocks.snapshot, draftHead: 'b'.repeat(40) }
  })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/无法确认数据库草稿和草稿文件，已阻止部署/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('refuses a changed current baseline when no saved ref exists', async () => {
  await setup('current')
  mocks.fetchJson.mockImplementation(async (url: string) => {
    if (url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    return { ...mocks.snapshot, baseCommit: 'b'.repeat(40) }
  })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/无法确认数据库草稿和草稿文件，已阻止部署/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('passes the current commit as a precondition and does not claim success on a race', async () => {
  const { id } = await setup('draft')
  mocks.deploy.mockRejectedValue({ status: 409, body: '{"code":"CANDIDATE_NOT_SAVED"}' })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: id, candidateCommit: 'a'.repeat(40), expectedActiveCommit: 'a'.repeat(40), snapshotProtocol: 'saved-tree-v2' })))
  expect(await screen.findByText(/部署失败或结果未知/)).toBeInTheDocument()
  expect(screen.queryByText(/部署成功/)).not.toBeInTheDocument()
})
it('rejects a current baseline that was read while saved existed', async () => {
  const { session } = await setup('current')
  act(() => {
    const current = session.state.versions.current
    publish(session, { ...session.state, versions: { ...session.state.versions,
      current: { ...current, snapshot: { ...current.snapshot!, draftHead: 'b'.repeat(40) } } } })
  })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/无法确认数据库草稿和草稿文件，已阻止部署/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('blocks saved-tree confirmation if local file edits happen during the fresh read', async () => {
  const { session } = await setup('draft')
  let resolve!: (value: unknown) => void
  const waiting = new Promise(r => { resolve = r })
  mocks.fetchJson.mockImplementation(async (url: string) => {
    if (url.endsWith('version=draft')) return waiting
    return { ...mocks.snapshot, baseRef: `refs/tags/deploy/${mocks.spec.id}/#1`, draftHead: null }
  })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await waitFor(() => expect(mocks.fetchJson).toHaveBeenCalledTimes(2))
  act(() => {
    const current = session.state.versions.current
    publish(session, { ...session.state, versions: { ...session.state.versions,
      current: { ...current, files: [...current.files, { path: 'scripts/new', contentBase64: '', mode: '100644' }] } } })
  })
  await act(async () => { resolve(mocks.snapshot); await waiting })
  expect(await screen.findByText(/读取期间编辑内容或数据库草稿已变化/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})


function panelRun(id: string): FlowRun {
  return { flow_id: 'run', workflow_id: id, workflow_title: 'DB draft', status: 'failed', triggered_by: 'manual', node_count: 1,
    succeeded_count: 0, failed_count: 1, total_duration_ms: 1, total_token_usage: null, started_at: '2026-01-01T00:00:00Z',
    completed_at: null, user_id: null, origin_bot_id: null, plugin_version: null, engine: null, workflow_version: 1, workflow_deploy_number: 1 }
}
function openPanel(id: string) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
  // Use the real save mutation and guard; replace only its HTTP endpoint.
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    if (url === '/api/auto-heal/capabilities') return new Response(JSON.stringify({ autoHealUnifiedSave: false }))
    if (url !== '/api/auto-heal/save' || init.method !== 'POST') throw new Error(`Unexpected request: ${url}`)
    const body = await new Promise<string>(resolve => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init.body as Blob)
    })
    const input = JSON.parse(body)
    return new Response(JSON.stringify({ ok: true, saved: true, workflowId: input.workflowId, packId: 'pack', branch: `wf/${input.workflowId}`, commit: 'a'.repeat(40) }))
  })
  return render(<QueryClientProvider client={client}><AutoHealPanel run={panelRun(id)} onClose={() => {}} /></QueryClientProvider>)
}
async function confirmAutoHealDeploy(button: HTMLElement) {
  fireEvent.click(button)
  fireEvent.click(await screen.findByRole('button', { name: '了解风险，确认部署' }))
}
async function diagnoseAndSave() {
  fireEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  await screen.findByText(/兼容保存的数据库和 Git 写入范围及并发保护由宿主决定/)
  fireEvent.click(await screen.findByRole('button', { name: '保存修复' }))
  return screen.findByRole('button', { name: '部署修复' })
}
it('deploys a reopened saved-only workflow without manually loading draft', async () => {
  const id = `created-before-reload-${++sequence}`, commit = 'a'.repeat(40)
  const spec = { id, title: 'DB draft', version: '1', nodes: [] }
  const saved = { workflowId: id, packId: 'pack', canWrite: true, baseRef: `origin/wf/${id}`, baseCommit: commit, draftHead: commit,
    versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' },
      { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' }] }
  mocks.fetchJson.mockImplementation(async (_url, init) => init?.method === 'POST'
    ? { ok: true, saved: true, workflowId: id, packId: 'pack', branch: `wf/${id}`, commit } : saved)
  mocks.spec = spec
  mocks.get.mockResolvedValue(spec)
  await createWorkflowPack(spec, 'pack')
  expect(sessionFor(id).state.creation?.phase).toBe('completed')
  // Release the in-memory session to model a page reload; storage is retained.
  releaseCleanSession(id, sessionFor(id))
  const { session } = await setup('draft', true, id)
  await waitFor(() => expect(session.state.versions.current.loading).toBe(false))
  expect(session.state.versions.current.snapshot).toBeNull()
  await waitFor(() => expect(session.state.versions.draft.snapshot).not.toBeNull())
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 1, version: 1, tagName: 'v1', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  expect(await screen.findByText(/部署成功/)).toBeInTheDocument()
  expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: id, candidateCommit: 'a'.repeat(40) }))
  expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(id)}`)).toBeNull()
})
it('blocks AutoHeal after Editor loses its acknowledgement and the panel remounts', async () => {
  const { id } = await setup(); mocks.deploy.mockRejectedValue(new Error('lost'))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  await screen.findByText(/没有日志恢复/)
  cleanup(); openPanel(id)
  expect(await screen.findByRole('alert')).toHaveTextContent('存在未确认的操作')
  fireEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  expect(await screen.findByRole('button', { name: '保存修复' })).toBeDisabled()
  cleanup(); openPanel(id)
  expect(await screen.findByRole('alert')).toHaveTextContent('存在未确认的操作')
  expect(mocks.deploy).toHaveBeenCalledTimes(1)
})
it('unlocks an AutoHeal tree conflict and permits a retry after repair', async () => {
  const { id } = await setup(); cleanup(); openPanel(id)
  const deployButton = await diagnoseAndSave()
  const transport = realConflictTransport(undefined, false)
  mocks.deploy.mockImplementationOnce(transport.deploy).mockImplementationOnce(async input => ({
    ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 1, version: 1, tagName: 'v1', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit,
  }))
  await confirmAutoHealDeploy(deployButton)
  await waitFor(() => expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${id}`)).toBeNull())
  expect(screen.queryByText(/结果可能未知/)).not.toBeInTheDocument()
  transport.restore()
  expect(screen.queryByRole('button', { name: '部署修复' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '重新诊断' }))
  await confirmAutoHealDeploy(await diagnoseAndSave())
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(2))
})

it('does not let an old AutoHeal success clear a newer marker', async () => {
  const { id } = await setup(); cleanup(); openPanel(id)
  const key = `clawweb.autoheal.review-blocked.v1.${id}`
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  mocks.deploy.mockImplementation(async () => { await gate; return { ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 1, version: 1, tagName: 'v1' } })
  await confirmAutoHealDeploy(await diagnoseAndSave())
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(1))
  localStorage.setItem(key, 'newer-marker')
  await act(async () => { release(); await gate })
  expect(await screen.findByText(/部署已确认成功，但无法清除浏览器核对标记/)).toBeInTheDocument()
  expect(localStorage.getItem(key)).toBe('newer-marker')
  fireEvent.click(screen.getByRole('button', { name: /我已人工核对/ }))
  expect(await screen.findByText(/部署请求与核对标记不匹配/)).toBeInTheDocument()
  expect(screen.queryByRole('dialog', { name: '清除浏览器核对标记' })).not.toBeInTheDocument()
  expect(localStorage.getItem(key)).toBe('newer-marker')
})

it('quarantines unverifiable marker removal and permits explicit reconciliation when storage returns', async () => {
  const { id } = await setup()
  const key = `clawweb.autoheal.review-blocked.v1.${id}`
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  mocks.deploy.mockImplementation(async input => { await gate; return { ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 1, version: 1, tagName: 'v1', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit } })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  await screen.findByRole('button', { name: '确认部署' }); fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(1))
  const actualGet = Storage.prototype.getItem
  let failed = false
  const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, name: string) {
    const value = actualGet.call(this, name)
    if (name === key && value === null && !failed) { failed = true; throw new Error('verification unavailable') }
    return value
  })
  try {
    await act(async () => { release(); await gate })
    expect(await screen.findByRole('alert', { name: '部署操作' })).toHaveTextContent('无法清除')
    expect(localStorage.getItem(key)).toBeNull()
    expect(() => assertDeploymentAvailable(id)).toThrow(/未确认/)
    fireEvent.click(screen.getByRole('button', { name: /我已人工核对/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /我已人工核对 Git 和数据库/ }))
    fireEvent.click(screen.getByRole('button', { name: '清除当前工作流标记' }))
    await waitFor(() => expect(() => assertDeploymentAvailable(id)).not.toThrow())
  } finally { spy.mockRestore() }
})

it.each(['success', 'unknown'] as const)('keeps AutoHeal %s pending across a switch to Editor', async outcome => {
  const { id } = await setup(); cleanup(); openPanel(id)
  const key = `clawweb.autoheal.review-blocked.v1.${id}`
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  mocks.deploy.mockImplementation(async () => {
    await gate
    if (outcome === 'unknown') throw new Error('lost')
    return { ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 1, version: 1, tagName: 'v1' }
  })
  await confirmAutoHealDeploy(await diagnoseAndSave())
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(1))
  const marker = localStorage.getItem(key)
  cleanup(); render(<MemoryRouter><Editor embedded initialWorkflowId={id} /></MemoryRouter>)
  expect(await screen.findByRole('status', { name: '部署操作' })).toHaveTextContent('正在进行')
  expect(screen.queryByRole('button', { name: /我已人工核对/ })).not.toBeInTheDocument()
  expect(() => clearReconciledDeployment(id, { gitAndDatabaseConfirmed: true })).toThrow(/正在进行/)
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(mocks.deploy).toHaveBeenCalledTimes(1)
  expect(localStorage.getItem(key)).toBe(marker)
  await act(async () => { release(); await gate })
  await waitFor(() => expect(screen.queryByRole('status', { name: '部署操作' })).not.toBeInTheDocument())
  if (outcome === 'success') expect(localStorage.getItem(key)).toBeNull()
  else {
    expect(localStorage.getItem(key)).toBe(marker)
    expect(await screen.findByRole('alert', { name: '部署人工核对标记' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /我已人工核对/ })).toBeInTheDocument()
  }
})

it('does not clear a replacement marker after reopening AutoHeal recovery for a legacy marker', async () => {
  const { id } = await setup(); cleanup()
  const key = `clawweb.autoheal.review-blocked.v1.${id}`
  localStorage.setItem(key, '1'); openPanel(id)
  fireEvent.click(await screen.findByRole('button', { name: /我已人工核对/ }))
  localStorage.setItem(key, 'newer-marker')
  fireEvent.click(screen.getByRole('checkbox', { name: /我已人工核对 Git 和数据库/ }))
  fireEvent.click(screen.getByRole('button', { name: '清除当前工作流标记' }))
  expect(await screen.findByText(/已确认人工核对，但无法清除/)).toBeInTheDocument()
  expect(localStorage.getItem(key)).toBe('newer-marker')
})

it('blocks Editor after AutoHeal loses its acknowledgement and the editor remounts', async () => {
  const { id } = await setup(); cleanup(); openPanel(id)
  mocks.deploy.mockRejectedValue(new Error('lost'))
  await confirmAutoHealDeploy(await diagnoseAndSave())
  await screen.findByText(/结果可能未知/)
  cleanup(); render(<MemoryRouter><Editor embedded initialWorkflowId={id} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/本浏览器存在未确认的部署/)).toBeInTheDocument()
  expect(mocks.deploy).toHaveBeenCalledTimes(1)
})
it('explicitly clears only the requested workflow marker after manual reconciliation', async () => {
  const markerPrefix = 'clawweb.autoheal.review-blocked.v1.'
  const workflowId = `manual-recovery-${++sequence}`
  const currentKey = markerPrefix + encodeURIComponent(workflowId)
  const otherKey = markerPrefix + 'other-workflow'
  localStorage.setItem(currentKey, '1')
  localStorage.setItem(otherKey, '1')
  const { id } = await setup('draft', false, workflowId)
  await act(async () => {})

  fireEvent.click(screen.getByRole('button', { name: '我已人工核对，清除当前工作流标记…' }))
  const confirm = screen.getByRole('button', { name: '清除当前工作流标记' })
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: /我已人工核对 Git 和数据库/ }))
  fireEvent.click(confirm)

  expect(await screen.findByText(/已清除工作流/)).toBeInTheDocument()
  expect(localStorage.getItem(currentKey)).toBeNull()
  expect(localStorage.getItem(otherKey)).toBe('1')
  expect(screen.queryByRole('alert', { name: '部署人工核对标记' })).not.toBeInTheDocument()
})

it('offers manual reconciliation when a successful deployment cannot clear its marker', async () => {
  const { id } = await setup()
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 3, version: 3, tagName: 'wf-tag-3', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
  const marker = `clawweb.autoheal.review-blocked.v1.${encodeURIComponent(id)}`
  const removeItemSpy = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {})
  fireEvent.click(confirm)

  const recovery = await screen.findByRole('button', { name: '我已人工核对，清除当前工作流标记…' })
  expect(screen.getByRole('alert', { name: '部署操作' })).toHaveTextContent('部署已确认成功，但无法清除浏览器核对标记')
  expect(localStorage.getItem(marker)).not.toBeNull()
  removeItemSpy.mockRestore()

  fireEvent.click(recovery)
  const clearConfirm = screen.getByRole('button', { name: '清除当前工作流标记' })
  fireEvent.click(screen.getByRole('checkbox', { name: /我已人工核对 Git 和数据库/ }))
  fireEvent.click(clearConfirm)
  expect(await screen.findByText(new RegExp(`已清除工作流 ${id}`))).toBeInTheDocument()
  expect(localStorage.getItem(marker)).toBeNull()
  expect(screen.queryByRole('alert', { name: '部署操作' })).not.toBeInTheDocument()
})

it('does not dispatch when the browser cannot persist the marker', async () => {
  await setup()
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
  const storageSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  fireEvent.click(confirm)
  expect(await screen.findByText(/部署未发送/)).toBeInTheDocument()
  storageSpy.mockRestore()
  expect(mocks.deploy).not.toHaveBeenCalled()
})

it('does not treat a fresh saved read as a baseline when local files exist without a snapshot', async () => {
  const { session } = await setup('draft', true)
  await waitFor(() => expect(session.state.versions.draft.snapshot).not.toBeNull())
  act(() => publish(session, { ...session.state, versions: { ...session.state.versions,
    draft: { ...session.state.versions.draft, snapshot: null, error: 'baseline unavailable', files: [{ path: 'orphan', contentBase64: 'eA==', mode: '100644' }] } } }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/版本文件存在未保存更改/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('does not bypass an existing conflicting baseline with a fresh saved read', async () => {
  const { session } = await setup()
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe('a'.repeat(40))
  mocks.deploymentState = { ...mocks.deploymentState, savedCommit: 'b'.repeat(40) }
  mocks.fetchJson.mockImplementation(async (url: string) => url.endsWith('version=current')
    ? { ...mocks.snapshot, baseRef: `refs/tags/deploy/${mocks.spec.id}/#1`, draftHead: null }
    : { ...mocks.snapshot, baseCommit: 'b'.repeat(40), draftHead: 'b'.repeat(40) })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/草稿文件快照与已读取基线不一致/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe('a'.repeat(40))
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('rechecks the shared marker when confirming an already opened Editor dialog', async () => {
  const { id } = await setup()
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
  localStorage.setItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(id)}`, '1')
  fireEvent.click(confirm)
  expect(await screen.findByText(/本浏览器存在未确认的部署/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('does not send AutoHeal deployment if persisting the shared marker fails', async () => {
  const { id } = await setup(); cleanup(); openPanel(id)
  const deploy = await diagnoseAndSave()
  const storageSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  try {
    await confirmAutoHealDeploy(deploy)
    expect(await screen.findByText(/部署未发送/)).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  } finally { storageSpy.mockRestore() }
})
it('rejects layout edits after confirmation even when the Git baseline was already loaded', async () => {
  const { session } = await setup('draft', true)
  act(() => {
    mocks.spec = { ...mocks.spec, nodes: [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' } }] }
    mocks.get.mockResolvedValue(mocks.spec)
    useEditorStore.getState().loadSpec(mocks.spec)
  })
  await waitFor(() => expect(session.state.versions.draft.snapshot).not.toBeNull())
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
  act(() => useEditorStore.getState().moveNode('done', 600, 300))
  fireEvent.click(confirm)
  expect(await screen.findByText(/确认期间编辑内容已变化/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('rejects the fresh saved-only baseline if layout edits appear before confirmation', async () => {
  const { session } = await setup('draft', true)
  act(() => {
    mocks.spec = { ...mocks.spec, nodes: [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' } }] }
    mocks.get.mockResolvedValue(mocks.spec)
    useEditorStore.getState().loadSpec(mocks.spec)
  })
  await waitFor(() => expect(session.state.versions.draft.snapshot).not.toBeNull())
  act(() => publish(session, { ...session.state, versions: { ...session.state.versions, draft: { ...session.state.versions.draft, snapshot: null, files: [], error: 'baseline unavailable' } } }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
  act(() => useEditorStore.getState().moveNode('done', 600, 300))
  fireEvent.click(confirm)
  expect(await screen.findByText(/确认期间编辑内容已变化/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
