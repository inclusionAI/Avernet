import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { parse, stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ db: null as WorkflowSpec | null, baseline: null as WorkflowSpec | null, fetchJson: vi.fn(), deploy: vi.fn(), history: vi.fn(), saveDb: vi.fn(), gitSave: vi.fn(), get: vi.fn(), deployHook: vi.fn(), realDeploy: false, serverRequests: [] as any[], serverReleases: [] as any[], serverResponses: [] as any[] }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.db, databaseSpec: mocks.baseline, isLoading: false }),
  useAutoHealDeploy: (options: { editorGuard: boolean }) => {
    mocks.deployHook(options)
    const real = useRealAutoHealDeploy(options)
    return mocks.realDeploy ? { ...real, mutateAsync: (input: any) => { void mocks.deploy(input); return real.mutateAsync(input) } } : { mutateAsync: mocks.deploy, isPending: false }
  },
  useSaveWorkflowToDb: () => ({ mutateAsync: mocks.saveDb }), useAutoHealSave: () => ({ mutateAsync: mocks.gitSave }),
  useFacadeBindings: () => ({ data: [] }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson,
  api: { workflows: { getHistory: mocks.history, get: mocks.get, getDeploySnapshot: vi.fn() } } }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { useAutoHealDeploy as useRealAutoHealDeploy } from '../../api/autoHealWrites'
import { validateWorkflowSnapshotRequest } from '../../api/validateWorkflowSnapshotRequest'
import { readDatabaseWorkflowDraft } from '../../api/editorWorkflowSource'
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { parseWorkflowYaml } from '../../editor/yamlDraft'
import { sessionFor, publish, type Snapshot } from '../../editor/packVersionSession'

const A = 'a'.repeat(40), B = 'b'.repeat(40), C = 'c'.repeat(40)
let sequence = 0
let current: Snapshot, saved: Snapshot, unifiedAvailable: boolean, draftToken: string
const state = () => useEditorStore.getState()
const file = (path: string, text: string, mode: '100644' | '100755' = '100644') => ({ path, contentBase64: btoa(text), mode })
function snapshot(version: 'current' | 'draft', spec = mocks.db!): Snapshot {
  return { ...{ packPath: '' }, workflowId: spec.id, packId: 'pack', canWrite: true, baseRef: (version !== 'draft' ? `refs/tags/deploy/${spec.id}/#1` : `origin/wf/${spec.id}`),
    baseCommit: version === 'draft' ? B : A, draftHead: version === 'draft' ? B : null,
    versionFiles: [file('workflow.pack.yaml', stringify({ id: 'pack', workflows: [{ id: spec.id, file: 'flows/custom.yaml' }] })),
      file('flows/custom.yaml', stringify(spec)), file('scripts/run', 'original', '100755'), { path: 'assets/raw', contentBase64: 'AP8C', mode: '100644' }] }
}
function readBlob(blob: Blob): Promise<string> { return new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) }) }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((r, j) => { resolve = r; reject = j }); return { resolve, reject, promise } }
async function mount(version: 'current' | 'draft' = 'current') {
  const session = sessionFor(mocks.db!.id)
  publish(session, { ...session.state, version })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><Editor embedded initialWorkflowId={mocks.db!.id} /></QueryClientProvider>)
  await waitFor(() => expect(session.state.versions[version].loading).toBe(false))
}
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))
const confirmDeploy = () => { fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); click('确认部署') }
beforeEach(async () => {
  vi.resetAllMocks(); state().reset({ force: true })
  mocks.realDeploy = false; mocks.serverRequests = []; mocks.serverReleases = []; mocks.serverResponses = []
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  mocks.db = { id: `divergence-${++sequence}`, title: 'Original', version: '1', nodes: [{ id: 'start', title: 'Start', phase: 'execute', executor: { type: 'done' } }] }
  const git = { ...mocks.db, title: 'Different Git' }
  expect(validateWorkflowSnapshotRequest(JSON.stringify(parseWorkflowYaml(stringify(git))), git.id)).toEqual(git)
  current = snapshot('current'); saved = snapshot('draft'); unifiedAvailable = true; draftToken = 'a'.repeat(64)
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.get.mockImplementation(async () => mocks.db)
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: input.workflowId, packId: input.packId, deployNumber: 2, version: 3, tagName: 'deploy/test/2', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit }))
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/editor-deploy/snapshot-v2') && init?.method === 'POST') {
      const input = JSON.parse(await readBlob(init.body as Blob))
      mocks.serverRequests.push(input)
      const workflow = saved.versionFiles.find(item => item.path === 'flows/custom.yaml')!
      const gitSpec = parseWorkflowYaml(atob(workflow.contentBase64))
      if (stringify(gitSpec, { sortMapEntries: true }) !== stringify(mocks.db, { sortMapEntries: true })) {
        const body = { code: 'SNAPSHOT_DB_MISMATCH', requestId: input.requestId }
        mocks.serverResponses.push({ status: 409, body })
        return new Response(JSON.stringify(body), { status: 409 })
      }
      mocks.serverReleases.push(input.candidateCommit)
      mocks.serverResponses.push({ status: 200 })
      return new Response(JSON.stringify({ ok: true, deployed: true, workflowId: input.workflowId, packId: input.packId, deployNumber: 2, version: 3, tagName: 'deploy/test/2', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit }))
    }
    if (url.endsWith('/unified-save') && init?.method === 'GET') return unifiedAvailable ? new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' })) : new Response(null, { status: 404 })
    return new Response(JSON.stringify(await mocks.fetchJson(url, init)))
  }))
  mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.includes('/snapshot-v2/state')) return { protocol: 'editor-deployment-state-v1', workflowId: mocks.db!.id, packId: saved.packId, canDeploy: true, savedCommit: saved.baseCommit, activeCommit: A }
    if (url.endsWith('/editor-draft')) {
      if (init?.method === 'GET') return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken }
      const input = JSON.parse(await readBlob(init!.body as Blob)); expect(input.expectedDraftToken).toBe('a'.repeat(64)); mocks.db = input.spec
      return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'b'.repeat(64) }
    }
    if (url.endsWith('/unified-save') && init?.method === 'POST') {
      const input = JSON.parse(await readBlob(init.body as Blob))
      if (input.sourceVersion !== 'draft' || input.sourceCommit !== saved.baseCommit || input.expectedSavedHead !== saved.draftHead || input.expectedDraftToken !== draftToken) throw new Error('unexpected unified baseline')
      const spec = JSON.parse(input.specJson) as WorkflowSpec
      const files = new Map(saved.versionFiles.map(item => [item.path, { ...item }]))
      const workflow = files.get('flows/custom.yaml')!
      files.set(workflow.path, { ...workflow, contentBase64: btoa(stringify(spec, { sortMapEntries: true, lineWidth: 0 })) })
      for (const change of input.changes) {
        if (change.path === 'workflow.pack.yaml' || change.path === 'flows/custom.yaml') throw new Error('protected unified path')
        if (change.operation === 'delete') files.delete(change.path)
        else files.set(change.path, { path: change.path, mode: change.mode, contentBase64: change.contentBase64 })
      }
      mocks.db = spec; draftToken = 'b'.repeat(64)
      saved = { ...saved, baseCommit: C, draftHead: C, versionFiles: [...files.values()] }
      return { requestId: input.requestId, outcome: 'confirmed', reconciliationRequired: false,
        evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit: C, draftToken } }
    }
    if (url === '/api/auto-heal/save') {
      const input = JSON.parse(await readBlob(init!.body as Blob))
      mocks.db = input.spec
      return { ok: true, saved: true, workflowId: input.workflowId, packId: input.packId ?? 'pack' }
    }
    if (init?.method === 'PUT') {
      const input = JSON.parse(await readBlob(init.body as Blob))
      const byPath = new Map((input.sourceVersion === 'draft' ? saved : current).versionFiles.map(item => [item.path, item]))
      for (const change of input.changes) {
        if (change.operation === 'delete') byPath.delete(change.path)
        else byPath.set(change.path, { path: change.path, mode: change.mode, contentBase64: change.contentBase64 })
      }
      const packId = parse(atob(byPath.get('workflow.pack.yaml')!.contentBase64)).id
      saved = { ...saved, packId, versionFiles: [...byPath.values()] }
      return { branch: `wf/${mocks.db!.id}`, commit: B, requestId: input.requestId, version: 2, packId }
    }
    return url.includes('version=draft') ? saved : current
  })
  mocks.baseline = await readDatabaseWorkflowDraft(mocks.db.id)
  state().loadSpec(mocks.db, mocks.baseline)
  mocks.fetchJson.mockClear(); mocks.get.mockClear()
})
afterEach(() => { cleanup(); state().reset({ force: true }); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('DB / Git divergence in the editor', () => {
  it('shows a divergence warning and leaves save and deploy available', async () => {
    saved = snapshot('draft', { ...mocks.db!, title: 'Different Git' })
    await mount('draft')
    expect(await screen.findByText(/(?:Git.*数据库|数据库.*Git).*(?:不同|差异|不一致)/)).toBeVisible()
    expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
    expect(screen.getByRole('button', { name: '部署', exact: true })).toBeEnabled()
    expect(state().spec!.title).toBe('Original')
  })

  it('opens review for a root Pack draft over a historical packs/<packId> active Pack', async () => {
    mocks.realDeploy = true
    current = { ...snapshot('current'), packPath: 'packs/pack' }
    saved = { ...snapshot('draft'), packPath: '', versionFiles: [
      ...snapshot('draft').versionFiles,
      file('packs/pack/workflow.pack.yaml', stringify({ id: 'pack', workflows: [{ id: mocks.db!.id, file: 'flows/custom.yaml' }] })),
      file('packs/pack/flows/custom.yaml', stringify(mocks.db!)),
    ] }
    await mount('draft')
    click('部署')
    await screen.findByRole('button', { name: '确认部署' })
    confirmDeploy()
    await screen.findByText(/部署成功：/)
    expect(mocks.serverReleases).toEqual([B])
  })
  it('does not discard the DB candidate when Git YAML cannot be parsed', async () => {
    saved.versionFiles = saved.versionFiles.map(item => item.path === 'flows/custom.yaml' ? file(item.path, 'id: [unterminated') : item)
    await mount('draft')
    act(() => state().updateSpecField('title', 'DB candidate despite invalid Git'))
    click('保存')
    await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(mocks.db!.title).toBe('DB candidate despite invalid Git')
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')?.contentBase64).toBe('AP8C')
  })
  it('saves the current candidate without mandatory reconciliation, preserving Pack files and leases', async () => {
    saved = snapshot('draft', { ...mocks.db!, title: 'Different Git' })
    await mount('draft')
    act(() => state().updateSpecField('title', 'My unsaved candidate'))
    click('保存')
    await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    const posts = mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')
    expect(posts).toHaveLength(1)
    const request = JSON.parse(await readBlob(posts[0][1].body))
    expect(JSON.parse(request.specJson).title).toBe('My unsaved candidate')
    expect(request).toMatchObject({ sourceVersion: 'draft', sourceCommit: B, expectedSavedHead: B, expectedDraftToken: 'a'.repeat(64) })
    expect(saved.versionFiles.find(item => item.path === 'scripts/run')).toMatchObject({ contentBase64: btoa('original'), mode: '100755' })
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')?.contentBase64).toBe('AP8C')
    expect(screen.queryByRole('region', { name: '工作流快照审阅' })).not.toBeInTheDocument()
  })
  it('rejects mismatched DB and Git with a request-bound 409, then saves and deploys the synchronized candidate', async () => {
    mocks.realDeploy = true
    saved = snapshot('draft', { ...mocks.db!, title: 'Different Git' })
    await mount('draft')
    act(() => state().updateSpecField('title', 'Local edit'))
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); confirmDeploy()
    expect(await screen.findByText(/数据库草稿与待部署的 Git 工作流不一致或无法安全比较/)).toBeVisible()
    expect(mocks.serverRequests).toHaveLength(1); expect(mocks.serverReleases).toEqual([])
    expect(mocks.serverResponses).toEqual([{ status: 409, body: { code: 'SNAPSHOT_DB_MISMATCH', requestId: mocks.serverRequests[0].requestId } }])
    expect(state().spec!.title).toBe('Local edit'); expect(state().isDirty).toBe(true)
    expect(mocks.serverRequests[0]).toMatchObject({ candidateCommit: B, expectedActiveCommit: A, snapshotProtocol: 'saved-tree-v2' })
    expect(mocks.db!.title).toBe('Original')
    expect(atob(saved.versionFiles.find(item => item.path === 'flows/custom.yaml')!.contentBase64)).toContain('Different Git')
    expect(screen.queryByText(/部署成功/)).not.toBeInTheDocument()
    expect(mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(0)
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(mocks.db!.title).toBe('Local edit')
    expect(parse(atob(saved.versionFiles.find(item => item.path === 'flows/custom.yaml')!.contentBase64))).toEqual(mocks.db)
    expect(saved.baseCommit).toBe(C)
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); confirmDeploy()
    await screen.findByText(/部署成功/)
    expect(mocks.serverRequests).toHaveLength(2)
    expect(mocks.serverResponses[1]).toEqual({ status: 200 })
    expect(mocks.serverRequests[1]).toMatchObject({ candidateCommit: C, expectedActiveCommit: A, expectedDraftToken: 'b'.repeat(64) })
    expect(mocks.serverReleases).toEqual([C])
  })
  it.each([
    ['current matches DB, draft differs', false, true],
    ['current differs from DB, draft matches', true, false],
  ])('bases deployment warning and server decision on the draft when %s', async (_label, currentDiffers, draftDiffers) => {
    mocks.realDeploy = true
    current = snapshot('current', { ...mocks.db!, title: currentDiffers ? 'Current Git' : 'Original' })
    saved = snapshot('draft', { ...mocks.db!, title: draftDiffers ? 'Draft Git' : 'Original' })
    await mount('draft')
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    click('当前版（生效 tag）')
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.current.snapshot?.baseCommit).toBe(A))
    expect(sessionFor(mocks.db!.id).state.version).toBe('current')
    click('部署')
    await screen.findByRole('button', { name: '确认部署' })
    const notice = '已保存 Git 工作流与数据库草稿不同；当前运行时可能读取数据库草稿，服务端会拒绝此部署。请先保存当前内容，再重新部署。保存可能立即影响使用数据库草稿的运行端。'
    const warnings = screen.queryAllByRole('status').filter(item => item.textContent === notice)
    expect(warnings).toHaveLength(draftDiffers ? 1 : 0)
    if (draftDiffers) expect(warnings[0]).toBeVisible()
    confirmDeploy()
    if (draftDiffers) {
      expect(await screen.findByText(/数据库草稿与待部署的 Git 工作流不一致或无法安全比较/)).toBeVisible()
      expect(mocks.serverReleases).toEqual([])
      expect(mocks.serverResponses[0]).toEqual({ status: 409, body: { code: 'SNAPSHOT_DB_MISMATCH', requestId: mocks.serverRequests[0].requestId } })
      expect(screen.queryByText(/部署成功/)).not.toBeInTheDocument()
    } else {
      await screen.findByText(/部署成功/)
      expect(mocks.serverReleases).toEqual([B])
    }
    expect(mocks.serverRequests).toHaveLength(1)
    expect(mocks.serverRequests[0]).toMatchObject({ candidateCommit: B, expectedActiveCommit: A })
  })
  it('deploys only the saved snapshot while warning that unsaved canvas edits are excluded', async () => {
    mocks.realDeploy = true
    await mount('draft')
    act(() => state().updateSpecField('title', 'Not in release'))
    click('部署')
    await screen.findByRole('button', { name: '确认部署' })
    const warnings = screen.getAllByRole('status').filter(item => item.textContent === '当前存在未保存的修改，本次部署不包含这些修改；如需部署它们，请先保存。')
    expect(warnings).toHaveLength(1)
    expect(warnings[0]).toBeVisible()
    expect(mocks.deploy).not.toHaveBeenCalled()
    confirmDeploy(); await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ candidateCommit: B, expectedActiveCommit: A, snapshotProtocol: 'saved-tree-v2' }))
    expect(mocks.serverResponses).toEqual([{ status: 200 }]); expect(mocks.serverReleases).toEqual([B])
    expect(state().spec!.title).toBe('Not in release'); expect(state().isDirty).toBe(true)
    expect(mocks.db!.title).toBe('Original')
    expect(mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(0)
  })
  it('rejects deployment when the draft changes after reviewing the saved snapshot', async () => {
    await mount('draft')
    act(() => state().updateSpecField('title', 'Unsaved at review'))
    click('部署'); await screen.findByRole('button', { name: '确认部署' })
    act(() => state().updateSpecField('title', 'Edited after review'))
    confirmDeploy()
    await waitFor(() => expect(screen.getByText(/(?:变化|变更|重新确认)/)).toBeVisible())
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(state().spec!.title).toBe('Edited after review'); expect(state().isDirty).toBe(true)
  })
  it('rejects confirmation if the DB token changes during review, retaining unsaved edits', async () => {
    await mount('draft'); act(() => state().updateSpecField('title', 'Unsaved at review'))
    click('部署'); await screen.findByRole('button', { name: '确认部署' })
    draftToken = 'c'.repeat(64)
    confirmDeploy()
    await waitFor(() => expect(screen.getByText(/(?:变化|变更|重新)/)).toBeVisible())
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(state().spec!.title).toBe('Unsaved at review'); expect(state().isDirty).toBe(true)
  })
  it('rejects confirmation if canvas positions change during review', async () => {
    await mount('draft'); act(() => state().updateSpecField('title', 'Unsaved at review'))
    click('部署'); await screen.findByRole('button', { name: '确认部署' })
    act(() => state().moveNode('start', 321, 456))
    confirmDeploy()
    await waitFor(() => expect(screen.getByText(/(?:变化|变更|重新确认)/)).toBeVisible())
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(state().positions.start).toEqual({ x: 321, y: 456 }); expect(state().spec!.title).toBe('Unsaved at review')
  })
  it('retains edits when the server rejects a deployment due to a changed Git lease', async () => {
    await mount('draft'); act(() => state().updateSpecField('title', 'Unsaved at review'))
    mocks.deploy.mockRejectedValueOnce(new Error('Git head changed; CAS conflict'))
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); confirmDeploy()
    await waitFor(() => expect(mocks.deploy).toHaveBeenCalledOnce())
    await screen.findByText(/Git head changed|CAS conflict/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ candidateCommit: B, expectedActiveCommit: A, expectedDraftToken: 'a'.repeat(64) }))
    expect(state().spec!.title).toBe('Unsaved at review'); expect(state().isDirty).toBe(true)
    expect(mocks.deploy).toHaveBeenCalledOnce()
  })
  it('retains later local edits during a delayed save without a duplicate write', async () => {
    await mount('draft')
    const original = mocks.fetchJson.getMockImplementation()!, gate = deferred<void>()
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/unified-save') && init?.method === 'POST') await gate.promise
      return original(url, init)
    })
    act(() => state().updateSpecField('title', 'Submitted')); click('保存')
    await waitFor(() => expect(mocks.fetchJson.mock.calls.some(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')).toBe(true))
    act(() => state().updateSpecField('title', 'Later local edit'))
    await act(async () => gate.resolve())
    await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(mocks.db!.title).toBe('Submitted')
    expect(state().spec!.title).toBe('Later local edit'); expect(state().isDirty).toBe(true)
    expect(mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(1)
  })
  it('keeps the local candidate on a failed unified write and does not silently retry', async () => {
    await mount('draft')
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/unified-save') && init?.method === 'POST') throw new Error('simulated write failure')
      return original(url, init)
    })
    act(() => state().updateSpecField('title', 'Keep on failure')); click('保存')
    await waitFor(() => expect(mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(1))
    await waitFor(() => expect(screen.getByRole('alert')).toBeVisible())
    expect(state().spec!.title).toBe('Keep on failure'); expect(state().isDirty).toBe(true)
    expect(mocks.db!.title).toBe('Original')
  })
})
