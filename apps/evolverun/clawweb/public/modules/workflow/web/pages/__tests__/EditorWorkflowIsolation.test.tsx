import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { RenderResult } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { webcrypto } from 'node:crypto'
import { editorActiveDeploymentToken } from '../../../server/services/editor-active-deployment-token'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ spec: undefined as WorkflowSpec | undefined, baseline: undefined as WorkflowSpec | null | undefined,
  db: null as WorkflowSpec | null, fetchJson: vi.fn(), proof: vi.fn(), get: vi.fn(), history: vi.fn(), snapshot: vi.fn(), deploy: vi.fn(), validate: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.spec, databaseSpec: mocks.baseline, activeDeployNumber: mocks.spec && !mocks.baseline ? 7 : null, isLoading: !mocks.spec }),
  useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: (url: string, init?: RequestInit) => url.includes('/snapshot-v2/state') ? mocks.proof(url, init) : mocks.fetchJson(url, init),
  api: { workflows: { get: mocks.get, getHistory: mocks.history, getDeploySnapshot: mocks.snapshot, validate: mocks.validate } } }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { readDatabaseWorkflowDraft, readEditorWorkflowSource } from '../../api/editorWorkflowSource'
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { publish, sessionFor, updateVersion, type Snapshot } from '../../editor/packVersionSession'
import { editorWorkflowWriteState } from '../../editor/workflowWriteGuard'

const state = () => useEditorStore.getState()
const A = 'a'.repeat(40), B = 'b'.repeat(40)
let sequence = 0
let saved: Snapshot, currentDraftToken: string, unifiedAvailable: boolean
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))
const review = () => fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }))
const writes = () => mocks.fetchJson.mock.calls.filter(([, init]) => init?.method && init.method !== 'GET')
const gitReads = () => mocks.fetchJson.mock.calls.filter(([url]) => /pack-version|pack-scripts/.test(url))
const blobText = (blob: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })
const waitForPack = () => waitFor(() => expect(sessionFor(mocks.spec!.id).state.versions.draft.snapshot).not.toBeNull())
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { resolve, promise } }
function snapshot(version: 'draft' | 'current' = 'current'): Snapshot {
  const spec = mocks.spec!
  const file = (path: string, text: string) => ({ path, mode: '100644' as const, contentBase64: btoa(text) })
  return { ...{ packPath: '' }, workflowId: spec.id, packId: 'pack', canWrite: true, baseCommit: A, draftHead: version === 'draft' ? A : null,
    baseRef: (version !== 'draft' ? `refs/tags/deploy/${spec.id}/#1` : `origin/wf/${spec.id}`), versionFiles: [
      file('workflow.pack.yaml', stringify({ id: 'pack', workflows: [spec.id] })),
      file(`workflows/${spec.id}.yaml`, stringify(spec)), file('scripts/run', 'original'),
    ] }
}
let queryClient: QueryClient | null = null
function mount(): RenderResult {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><Editor embedded initialWorkflowId={mocks.spec!.id} /></QueryClientProvider>)
}
beforeEach(async () => {
  vi.resetAllMocks(); localStorage.clear(); state().reset({ force: true })
  mocks.spec = { id: `workflow-isolation-${++sequence}`, title: 'DB draft', version: '1', nodes: [] }
  mocks.baseline = structuredClone(mocks.spec); mocks.db = structuredClone(mocks.spec)
  saved = snapshot('draft'); currentDraftToken = 'a'.repeat(64); unifiedAvailable = true
  mocks.get.mockImplementation(async () => {
    if (!mocks.db) throw { status: 404, body: '{"code":"WORKFLOW_NOT_FOUND"}' }
    return mocks.db
  })
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.validate.mockImplementation(async (spec: WorkflowSpec) => ({ valid: true, normalizedSpec: spec }))
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: input.workflowId, packId: input.packId, deployNumber: 2, version: 2, tagName: 'deployed', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit }))
  mocks.proof.mockImplementation(async () => ({ protocol: 'editor-deployment-state-v1', workflowId: mocks.spec!.id, packId: 'pack', canDeploy: true, savedCommit: A, activeCommit: A }))
  // Capability has its own protocol; a Pack snapshot is not a valid response.
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/unified-save') && init?.method === 'GET') return unifiedAvailable ? new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' })) : new Response(null, { status: 404 })
    return new Response(JSON.stringify(await mocks.fetchJson(url, init)))
  }))
  mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/editor-draft')) {
      if (init?.method === 'GET') return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: currentDraftToken }
      const input = JSON.parse(await blobText(init!.body as Blob)); expect(input.expectedDraftToken).toBe('a'.repeat(64)); mocks.db = input.spec
      return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'b'.repeat(64) }
    }
    if (url === '/api/auto-heal/editor-save-first-draft') {
      const input = JSON.parse(await blobText(init!.body as Blob)); mocks.db = input.spec
      return { ok: true, saved: true, workflowId: input.workflowId, packId: 'pack' }
    }
    if (url.endsWith('/unified-save') && init?.method === 'POST') {
      const input = JSON.parse(await blobText(init.body as Blob))
      if (input.expectedDraftToken !== currentDraftToken) throw new Error('CAS mismatch')
      mocks.db = JSON.parse(input.specJson); currentDraftToken = 'b'.repeat(64)
      const files = structuredClone(saved.versionFiles).map(file => file.path === `workflows/${mocks.db!.id}.yaml`
        ? { ...file, contentBase64: btoa(stringify(mocks.db, { sortMapEntries: true, lineWidth: 0 })) } : file)
      for (const change of input.changes) {
        const index = files.findIndex(file => file.path === change.path)
        if (change.operation === 'delete') files.splice(index, 1)
        else if (index < 0) files.push({ path: change.path, mode: change.mode, contentBase64: change.contentBase64 })
        else files[index] = { path: change.path, mode: change.mode, contentBase64: change.contentBase64 }
      }
      saved = { ...saved, baseCommit: B, draftHead: B, versionFiles: files }
      return { requestId: input.requestId, outcome: 'confirmed', reconciliationRequired: false,
        evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit: B, draftToken: currentDraftToken } }
    }
    return url.endsWith('version=draft') ? saved : snapshot('current')
  })
  mocks.baseline = await readDatabaseWorkflowDraft(mocks.spec.id)
  mocks.fetchJson.mockClear(); mocks.get.mockClear()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => {
  cleanup(); queryClient?.clear(); queryClient = null
  if (mocks.spec) { const session = sessionFor(mocks.spec.id); publish(session, { ...session.state, uploading: false, saving: false }) }
  state().reset({ force: true }); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

describe('workflow and script persistence safety', () => {
  it('opens the DB draft while an initial Git read is pending without writing either store', async () => {
    const pending = deferred<Snapshot>()
    mocks.fetchJson.mockReturnValueOnce(pending.promise)
    mount(); await waitFor(() => expect(state().spec).toEqual(mocks.spec))
    expect(gitReads()).toHaveLength(1)
    expect(state()._databaseSpec).toEqual(mocks.baseline)
    expect(screen.queryByRole('button', { name: '以此 Git 基线加载画布' })).not.toBeInTheDocument()
    act(() => state().updateSpecField('title', 'Unstored local edit'))
    click('保存'); expect(await screen.findByText(/Git 版本文件尚未成功读取/)).toBeVisible()
    expect(writes()).toEqual([]); expect(state().isDirty).toBe(true)
    await act(async () => { pending.resolve(snapshot('draft')); await pending.promise })
    expect(state().spec?.title).toBe('Unstored local edit'); expect(writes()).toEqual([])
  })
  it('performs no writes for an unchanged draft after reading the Git baseline', async () => {
    mount(); await waitForPack(); click('保存'); await screen.findByText(/没有需要保存的更改/)
    expect(gitReads()).toHaveLength(1); expect(writes()).toEqual([])
  })
  it('invalidates editor and workflow-list caches after a confirmed unified save', async () => {
    mount(); await waitForPack(); const invalidate = vi.spyOn(queryClient!, 'invalidateQueries')
    act(() => state().updateSpecField('title', 'Persisted draft'))
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(invalidate.mock.calls.map(([input]) => input.queryKey)).toEqual([
      ['editor-workflow-document', mocks.spec!.id], ['db-workflows'], ['workflow-types'],
    ])
  })
  it('blocks a scripts save while a workflow save is pending after a tab switch', async () => {
    mount(); await waitForPack(); act(() => state().updateSpecField('title', 'Pending workflow'))
    const pending = deferred<unknown>()
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/unified-save') && init?.method === 'POST') {
        const result = await original(url, init)
        return pending.promise.then(() => result)
      }
      return original(url, init)
    })
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('Pending workflow'))
    expect(writes()).toHaveLength(1)
    tab('脚本文件')
    await screen.findByText(/Pack：/)
    expect(screen.queryByRole('button', { name: '保存到草稿', exact: true })).not.toBeInTheDocument()
    const scriptSave = screen.getByRole('button', { name: '正在保存…', exact: true })
    expect(scriptSave).toBeDisabled()
    fireEvent.click(scriptSave); await act(async () => {})
    expect(writes()).toHaveLength(1)
    await act(async () => { pending.resolve({ workflowId: mocks.spec!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'b'.repeat(64) }) })
  })
  it('restores an active-only workflow then saves the complete snapshot without createOnly', async () => {
    mocks.baseline = null; mocks.db = null
    vi.stubGlobal('crypto', webcrypto)
    const id = mocks.spec!.id, specJson = JSON.stringify(mocks.spec)
    const activeToken = editorActiveDeploymentToken(id, { pack_id: 'pack', deploy_number: 7, version: 3, tag_name: 'v3', action: 'deploy', spec_json: specJson })
    const active = { packId: 'pack', activeToken, deployNumber: 7, version: 3, tagName: 'v3', action: 'deploy', isActive: true }
    mocks.history.mockResolvedValue({ workflowId: id, history: [], active })
    mocks.snapshot.mockResolvedValue({ workflowId: id, deployNumber: 7, version: 3, tagName: 'v3', action: 'deploy', specJson })
    const source = await readEditorWorkflowSource(id)
    expect(source.databaseSpec).toBeNull(); expect(source.activeDeployNumber).toBe(7)
    mocks.spec = source.spec
    mount(); await waitFor(() => expect(state().spec).toEqual(mocks.spec)); await waitForPack()
    expect(state()._databaseSpec).toBeNull()
    expect(state()._databasePackId).toBe('pack'); expect(state()._activeBaseline?.activeToken).toBe(activeToken)
    act(() => state().updateSpecField('title', 'First DB draft'))
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(state()._databaseSpec?.title).toBe('First DB draft')
    expect(state()._databasePackId).toBe('pack'); expect(state()._activeBaseline).toBeUndefined()
    expect(writes()).toHaveLength(2)
    expect(writes()[0][0]).toBe('/api/auto-heal/editor-save-first-draft')
    expect(JSON.parse(await blobText(writes()[0][1].body))).toEqual({ workflowId: mocks.spec!.id, spec: state().spec, packId: 'pack', expectedDraftAbsent: true, expectedActiveDeployNumber: 7, expectedActiveToken: activeToken })
    const unified = JSON.parse(await blobText(writes()[1][1].body))
    expect(writes()[1][0]).toBe(`/api/workflows/${mocks.spec!.id}/unified-save`)
    expect(JSON.parse(unified.specJson)).toEqual(state().spec)
    expect(atob(saved.versionFiles.find(f => f.path === `workflows/${mocks.spec!.id}.yaml`)!.contentBase64)).toContain('First DB draft')
    expect(gitReads().length).toBeGreaterThanOrEqual(2)
  })
  it('does not present a historical release as active when the active pointer is absent', async () => {
    mocks.history.mockResolvedValue({ active: null, history: [{ deployNumber: 9, version: 9, action: 'deploy' }] })
    mount(); await screen.findByText('未发布 · 数据库暂存稿')
    expect(mocks.snapshot).not.toHaveBeenCalled()
    expect(screen.queryByText(/生效 v9|最新发布 v9/)).not.toBeInTheDocument()
  })
  it('reports an unavailable active status without claiming unpublished or blocking a DB save', async () => {
    mocks.history.mockRejectedValue(new Error('history unavailable'))
    mount(); await screen.findByText(/生效版本状态读取失败/); await waitForPack()
    expect(screen.queryByText('未发布 · 数据库暂存稿')).not.toBeInTheDocument()
    act(() => state().updateSpecField('title', 'DB independent'))
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(mocks.db?.title).toBe('DB independent')
    expect(gitReads().map(([url]) => url)).toEqual([
      `/api/workflows/${mocks.spec!.id}/pack-version?version=draft`,
      `/api/workflows/${mocks.spec!.id}/pack-version?version=draft`,
    ])
    expect(writes()).toHaveLength(1); expect(writes()[0][0]).toBe(`/api/workflows/${mocks.spec!.id}/unified-save`)
  })
  it('only persists applied YAML on top-level save; typing and Apply do not save', async () => {
    mount(); await waitForPack(); click('YAML')
    const edited = { ...mocks.spec!, title: 'Applied draft' }
    fireEvent.change(screen.getByLabelText('工作流 YAML'), { target: { value: stringify(edited) } })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await screen.findByText(/存在未应用的配置草稿/)
    expect(writes()).toEqual([]); expect(state().spec?.title).toBe('DB draft')
    click('校验并应用'); await screen.findByText(/尚未保存到服务器/)
    expect(mocks.validate).toHaveBeenCalledWith(edited)
    expect(state().spec?.title).toBe('Applied draft'); expect(state().isDirty).toBe(true)
    expect(mocks.db?.title).toBe('DB draft'); expect(writes()).toEqual([])
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(mocks.db?.title).toBe('Applied draft'); expect(state().isDirty).toBe(false)
    expect(writes()).toHaveLength(1); expect(writes()[0][1].method).toBe('POST')
    const body = JSON.parse(await blobText(writes()[0][1].body))
    expect(body.expectedDraftToken).toBe('a'.repeat(64)); expect(JSON.parse(body.specJson)).toEqual(edited)
    expect(atob(saved.versionFiles.find(f => f.path === `workflows/${edited.id}.yaml`)!.contentBase64)).toContain('Applied draft')
    expect(gitReads().length).toBeGreaterThanOrEqual(2); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('keeps the Git read failure visible on save after returning to workflow and preserves canvas edits', async () => {
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes('pack-version')) throw { status: 503, body: 'Git offline' }
      if (url.endsWith('/editor-draft') && init?.method === 'GET') return original(url, init)
      throw new Error(`unexpected request: ${url}`)
    })
    mount(); tab('脚本文件'); await screen.findAllByText(/版本服务暂不可用/)
    tab('工作流编排')
    act(() => state().updateSpecField('title', 'After script failure'))
    click('保存'); expect(await screen.findByText(/Git 版本文件尚未成功读取/)).toHaveTextContent('版本服务暂不可用')
    expect(mocks.db?.title).toBe('DB draft'); expect(writes()).toEqual([]); expect(state().isDirty).toBe(true)
    tab('脚本文件'); expect(screen.getAllByText(/版本服务暂不可用/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/工作流已保存到数据库/)).not.toBeInTheDocument()
  })
  it.each(['dirty', 'blocked', 'loading', 'saving', 'uploading', 'unknown'] as const)('blocks unsafe shared save and retains script state: %s', async risk => {
    if (risk === 'dirty') unifiedAvailable = false
    const id = mocks.spec!.id, session = sessionFor(id), base = snapshot()
    const files = base.versionFiles.map(f => f.path === 'scripts/run' ? { ...f, contentBase64: btoa('keep local') } : f)
    updateVersion(session, 'current', { snapshot: { ...base, canWrite: false }, files,
      blocked: risk === 'blocked', loading: risk === 'loading', error: risk === 'blocked' ? 'Git blocked' : null })
    const reconciliation = risk === 'unknown' ? { token: {}, reason: 'no-response' } as NonNullable<typeof session.state.reconciliation> : null
    publish(session, { ...session.state, version: 'current', saving: risk === 'saving', uploading: risk === 'uploading', reconciliation })
    mount(); act(() => state().updateSpecField('title', `Keep ${risk}`))
    if (risk === 'saving') {
      expect(screen.getByRole('button', { name: '正在保存…', exact: true })).toBeDisabled()
      fireEvent.keyDown(window, { key: 's', ctrlKey: true })
      await act(async () => {})
    } else {
      fireEvent.keyDown(window, { key: 's', ctrlKey: true })
      const expected = risk === 'dirty' ? /统一保存不可用/ : risk === 'blocked' ? /Git blocked/ : risk === 'loading' ? /Git 版本文件尚未成功读取/ : /保存、上传或部署状态未完成/
      expect(await screen.findByText(expected)).toBeVisible()
    }
    expect(writes()).toEqual([]); expect(mocks.db?.title).toBe('DB draft'); expect(state().isDirty).toBe(true)
    expect(session.state.versions.current.files).toEqual(files)
    expect(session.state.reconciliation).toBe(reconciliation)
    expect(session.state.saving).toBe(risk === 'saving'); expect(session.state.uploading).toBe(risk === 'uploading')
    expect(session.state.versions.current.blocked).toBe(risk === 'blocked')
    expect(gitReads()).toEqual([])
    act(() => publish(session, { ...session.state, saving: false, uploading: false }))
  })
  it('keeps DB errors and unsaved edits visible without a Git write', async () => {
    mount(); await waitForPack(); act(() => state().updateSpecField('title', 'Keep canvas'))
    mocks.fetchJson.mockRejectedValue(new Error('DB offline')); click('保存')
    await screen.findByText(/无法核对数据库草稿/)
    expect(state().spec?.title).toBe('Keep canvas'); expect(state().isDirty).toBe(true)
    expect(gitReads()).toHaveLength(1); expect(writes()).toEqual([]); expect(mocks.fetchJson.mock.calls.filter(([url]) => url.endsWith('/editor-draft'))).toHaveLength(1)
  })
  it('does not overwrite canvas edits with a late source result or a pending Git read', async () => {
    const original = mocks.spec!, draft = mocks.baseline, pending = deferred<Snapshot>()
    const pendingSnapshot = snapshot('draft')
    mocks.fetchJson.mockReturnValueOnce(pending.promise)
    mocks.spec = undefined; mocks.baseline = undefined
    const view = render(<Editor embedded initialWorkflowId={original.id} />)
    act(() => { state().loadSpec(original); state().updateSpecField('title', 'Local edit') })
    mocks.spec = { ...original, title: 'Late data' }; mocks.baseline = draft
    view.rerender(<Editor embedded initialWorkflowId={original.id} />)
    expect(state().spec?.title).toBe('Local edit'); expect(state().isDirty).toBe(true)
    await act(async () => { pending.resolve(pendingSnapshot); await pending.promise })
    expect(state().spec?.title).toBe('Local edit'); expect(state().isDirty).toBe(true)
    expect(gitReads()).toHaveLength(1); expect(writes()).toEqual([])
  })
  it('retains newer edits during a DB save without overwriting them on verified completion', async () => {
    mount(); await waitForPack(); act(() => state().updateSpecField('title', 'Submitted'))
    const pending = deferred<unknown>()
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/unified-save') && init?.method === 'POST') {
        const result = await original(url, init)
        return pending.promise.then(() => result)
      }
      return original(url, init)
    })
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('Submitted'))
    act(() => state().updateSpecField('title', 'Newer'))
    await act(async () => { pending.resolve({ workflowId: mocks.spec!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'b'.repeat(64) }); await pending.promise })
    expect(state().spec?.title).toBe('Newer'); expect(state().isDirty).toBe(true)
    expect(state()._databaseSpec?.title).toBe('Submitted'); expect(state()._savedSpec?.title).toBe('Submitted')
    expect(sessionFor(mocks.spec!.id).state.postSaveVerificationError).toBeFalsy()
    expect(sessionFor(mocks.spec!.id).state.versions.draft.snapshot?.baseCommit).toBe(B)
    expect(gitReads()).toHaveLength(2)
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('Newer'))
    await waitFor(() => expect(state().isDirty).toBe(false))
    expect(state()._databaseSpec?.title).toBe('Newer'); expect(writes()).toHaveLength(2)
  })
  it('ignores completion from a replaced editor lifetime, including its save message', async () => {
    mount(); await waitForPack(); act(() => state().updateSpecField('title', 'Submitted'))
    const pending = deferred<unknown>()
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/unified-save') && init?.method === 'POST') {
        const result = await original(url, init)
        return pending.promise.then(() => result)
      }
      return original(url, init)
    })
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('Submitted'))
    act(() => { state().reset({ force: true }); state().loadSpec({ ...mocks.spec!, title: 'Replacement lifetime' }) })
    await act(async () => { pending.resolve({ workflowId: mocks.spec!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'b'.repeat(64) }); await pending.promise })
    expect(state().spec?.title).toBe('Replacement lifetime')
    expect(state()._databaseSpec?.title).toBe('Replacement lifetime')
    expect(screen.queryByText(/工作流已保存到数据库/)).not.toBeInTheDocument()
  })
  it.each(['draft', 'current'] as const)('checks saved and active baselines on explicit deploy (available baseline: %s)', async source => {
    mocks.fetchJson.mockImplementation(async url => {
      if (url.endsWith('/editor-draft')) return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'a'.repeat(64) }
      if (source === 'current' && url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
      return snapshot(url.endsWith('version=draft') ? 'draft' : 'current')
    })
    mount(); await waitFor(() => expect(sessionFor(mocks.spec!.id).state.versions.draft.loading).toBe(false))
    expect(gitReads()).toHaveLength(1)
    click('部署')
    if (source === 'current') {
      expect(await screen.findByText(/无法确认数据库草稿和草稿文件，已阻止部署/)).toBeVisible()
      expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
      expect(mocks.deploy).not.toHaveBeenCalled(); expect(writes()).toEqual([])
    } else {
      const confirm = await screen.findByRole('button', { name: '确认部署' }); review()
      fireEvent.click(confirm); await screen.findByText(/部署成功/)
      expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.spec!.id, candidateCommit: A }))
    }
    expect(gitReads().map(([url]) => url)).toEqual([
      `/api/workflows/${mocks.spec!.id}/pack-version?version=draft`,
      `/api/workflows/${mocks.spec!.id}/pack-version?version=draft`,
      `/api/workflows/${mocks.spec!.id}/pack-version?version=current`,
    ])
  })
})


describe('cold deployment preserves pre-existing layout while detecting new layout changes', () => {
  async function mountLayoutOnlyDraft(moved = true) {
    mocks.spec = { ...mocks.spec!, nodes: [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' }, _x: 80, _y: 80 }] }
    mocks.db = structuredClone(mocks.spec)
    mocks.baseline = await readDatabaseWorkflowDraft(mocks.spec.id)
    mocks.fetchJson.mockClear(); mocks.get.mockClear()
    // Keep the no-baseline fixture: the automatic Git read fails, and the
    // save must not silently fall back to the DB while the layout survives.
    mocks.fetchJson.mockRejectedValueOnce({ status: 503, body: 'Git offline' })
    mount()
    if (moved) act(() => state().moveNode('done', 400, 200))
    expect(state().isDirty).toBe(false); expect(state().isLayoutDirty).toBe(moved)
    const session = sessionFor(mocks.spec.id)
    await waitFor(() => expect(session.state.versions.draft.readFailure).toBeTruthy())
    click('保存'); expect(await screen.findByText(/Git 版本文件尚未成功读取/)).toBeVisible()
    expect(state().isLayoutDirty).toBe(moved)
    expect(gitReads()).toHaveLength(1); expect(writes()).toEqual([])
    expect(session.state.versions.current.snapshot).toBeNull()
    expect(session.state.versions.draft.snapshot).toBeNull()
    expect(screen.getByRole('tab', { name: '脚本文件' })).toHaveAttribute('aria-selected', 'false')
    mocks.fetchJson.mockClear()
    return session
  }

  it.each(['draft', 'current'] as const)('checks saved and active after blocked save with no local Git baseline and a layout-only drag (%s)', async source => {
    const session = await mountLayoutOnlyDraft()
    mocks.fetchJson.mockImplementation(async url => {
      if (url.endsWith('/editor-draft')) return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'a'.repeat(64) }
      if (source === 'current' && url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
      return snapshot(url.endsWith('version=draft') ? 'draft' : 'current')
    })
    click('部署')
    if (source === 'current') {
      expect(await screen.findByText(/无法确认数据库草稿和草稿文件，已阻止部署/)).toBeVisible()
      expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
      expect(mocks.deploy).not.toHaveBeenCalled()
    } else {
      const confirm = await screen.findByRole('button', { name: '确认部署' }); review()
      fireEvent.click(confirm)
      await screen.findByText(/部署成功/)
      expect(mocks.deploy).toHaveBeenCalledOnce()
      expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.spec!.id, candidateCommit: A }))
    }
    expect(gitReads().map(([url]) => url)).toEqual([
      `/api/workflows/${mocks.spec!.id}/pack-version?version=draft`,
      `/api/workflows/${mocks.spec!.id}/pack-version?version=current`,
    ])
    expect(state().isLayoutDirty).toBe(true); expect(state().isDirty).toBe(false)
    expect(state().positions.done).toEqual({ x: 400, y: 200 })
    expect(mocks.db?.nodes[0]).toMatchObject({ _x: 80, _y: 80 })
    expect(writes()).toEqual([])
    expect(session.state.versions.current.snapshot).toBeNull()
    expect(session.state.versions.draft.snapshot).toBeNull()
  })

  it.each([false, true])('blocks layout changes during the initial read (already layout dirty: %s)', async moved => {
    await mountLayoutOnlyDraft(moved)
    const pending = deferred<Snapshot>()
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url: string, init?: RequestInit) =>
      url.endsWith('version=draft') ? pending.promise : original(url, init))
    click('部署')
    await waitFor(() => expect(gitReads().map(([url]) => url)).toEqual([
      `/api/workflows/${mocks.spec!.id}/pack-version?version=draft`,
      `/api/workflows/${mocks.spec!.id}/pack-version?version=current`,
    ]))
    expect(mocks.deploy).not.toHaveBeenCalled(); expect(writes()).toEqual([])
    act(() => state().moveNode('done', 600, 300))
    await act(async () => { pending.resolve(snapshot('draft')); await pending.promise })
    expect(await screen.findByText(/读取期间.*布局.*变化/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled(); expect(writes()).toEqual([])
    expect(state().positions.done).toEqual({ x: 600, y: 300 }); expect(state().isLayoutDirty).toBe(true)
  })

  it.each([false, true])('blocks layout changes while the confirmation is open (already layout dirty: %s)', async moved => {
    await mountLayoutOnlyDraft(moved)
    click('部署'); const confirm = await screen.findByRole('button', { name: '确认部署' }); review()
    act(() => state().moveNode('done', 600, 300))
    const readsBefore = mocks.fetchJson.mock.calls.filter(([url]) => url.endsWith('/editor-draft')).length
    fireEvent.click(confirm)
    expect(await screen.findByText(/确认期间编辑内容已变化/)).toBeInTheDocument()
    expect(mocks.fetchJson.mock.calls.filter(([url]) => url.endsWith('/editor-draft'))).toHaveLength(readsBefore)
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(state().positions.done).toEqual({ x: 600, y: 300 }); expect(state().isLayoutDirty).toBe(true)
    expect(writes()).toEqual([])
  })

  it.each([false, true])('blocks layout changes during the final DB verification (already layout dirty: %s)', async moved => {
    await mountLayoutOnlyDraft(moved)
    click('部署'); const confirm = await screen.findByRole('button', { name: '确认部署' }); review()
    const pending = deferred<unknown>()
    const original = mocks.fetchJson.getMockImplementation()!
    const finalRead = vi.fn()
    mocks.fetchJson.mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/editor-draft') && init?.method === 'GET') { finalRead(); return pending.promise }
      return original(url, init)
    })
    fireEvent.click(confirm); await waitFor(() => expect(finalRead).toHaveBeenCalledOnce())
    act(() => state().moveNode('done', 600, 300))
    await act(async () => { pending.resolve({ workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'a'.repeat(64) }); await pending.promise })
    expect(await screen.findByText(/数据库草稿或本地编辑已变化/)).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled(); expect(writes()).toEqual([])
    expect(state().positions.done).toEqual({ x: 600, y: 300 }); expect(state().isLayoutDirty).toBe(true)
  })
})
