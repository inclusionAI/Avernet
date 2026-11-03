import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { RenderResult } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ spec: undefined as WorkflowSpec | undefined, baseline: undefined as WorkflowSpec | null | undefined,
  db: null as WorkflowSpec | null, fetchJson: vi.fn(), get: vi.fn(), history: vi.fn(), snapshot: vi.fn(), deploy: vi.fn(), validate: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.spec, databaseSpec: mocks.baseline, activeDeployNumber: mocks.spec && !mocks.baseline ? 7 : null, isLoading: !mocks.spec }),
  useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson,
  api: { workflows: { get: mocks.get, getHistory: mocks.history, getDeploySnapshot: mocks.snapshot, validate: mocks.validate } } }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { publish, sessionFor, updateVersion, type Snapshot } from '../../editor/packVersionSession'

const state = () => useEditorStore.getState()
const A = 'a'.repeat(40)
let sequence = 0
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }))
const writes = () => mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)
const gitReads = () => mocks.fetchJson.mock.calls.filter(([url]) => /pack-version|pack-scripts/.test(url))
const blobText = (blob: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { resolve, promise } }
function snapshot(version: 'draft' | 'current' = 'current'): Snapshot {
  const spec = mocks.spec!
  const file = (path: string, text: string) => ({ path, mode: '100644' as const, contentBase64: btoa(text) })
  return { workflowId: spec.id, packId: 'pack', canWrite: true, baseCommit: A, draftHead: version === 'draft' ? A : null,
    baseRef: `origin/${version === 'draft' ? 'saved' : 'wf'}/${spec.id}`, versionFiles: [
      file('workflow.pack.yaml', stringify({ id: 'pack', workflows: [spec.id] })),
      file(`workflows/${spec.id}.yaml`, stringify(spec)), file('scripts/run', 'original'),
    ] }
}
let queryClient: QueryClient | null = null
function mount(): RenderResult {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><Editor embedded initialWorkflowId={mocks.spec!.id} /></QueryClientProvider>)
}
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); state().reset({ force: true })
  mocks.spec = { id: `workflow-isolation-${++sequence}`, title: 'DB draft', version: '1', nodes: [] }
  mocks.baseline = structuredClone(mocks.spec); mocks.db = structuredClone(mocks.spec)
  mocks.get.mockImplementation(async () => {
    if (!mocks.db) throw { status: 404, body: '{"code":"WORKFLOW_NOT_FOUND"}' }
    return mocks.db
  })
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.validate.mockImplementation(async (spec: WorkflowSpec) => ({ valid: true, normalizedSpec: spec }))
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: input.workflowId, packId: input.packId, deployNumber: 2, version: 2, tagName: 'deployed' }))
  mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/api/auto-heal/save') {
      const input = JSON.parse(await blobText(init!.body as Blob)); mocks.db = input.spec
      return { ok: true, saved: true, workflowId: input.workflowId, packId: 'pack' }
    }
    return snapshot(url.endsWith('version=draft') ? 'draft' : 'current')
  })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => {
  cleanup(); queryClient?.clear(); queryClient = null
  if (mocks.spec) { const session = sessionFor(mocks.spec.id); publish(session, { ...session.state, uploading: false, saving: false }) }
  state().reset({ force: true }); vi.restoreAllMocks()
})

describe('workflow editing is independent of script services', () => {
  it('opens the DB draft without any Git request or Git authority UI', async () => {
    mount(); await waitFor(() => expect(state().spec).toEqual(mocks.spec))
    expect(gitReads()).toEqual([])
    expect(state()._databaseSpec).toEqual(mocks.baseline)
    expect(screen.queryByText(/Git 基线|写入能力/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '以此 Git 基线加载画布' })).not.toBeInTheDocument()
  })
  it('does not contact either persistence service for an unchanged existing draft', async () => {
    mount(); click('保存'); await screen.findByText(/没有需要保存的更改/)
    expect(gitReads()).toEqual([]); expect(writes()).toEqual([])
  })
  it('invalidates editor and workflow-list caches after a direct database save', async () => {
    mount(); const invalidate = vi.spyOn(queryClient!, 'invalidateQueries')
    act(() => state().updateSpecField('title', 'Persisted draft'))
    click('保存'); await screen.findByText(/工作流已保存到数据库/)
    expect(invalidate.mock.calls.map(([input]) => input.queryKey)).toEqual([
      ['editor-workflow-document', mocks.spec!.id], ['db-workflows'], ['workflow-types'],
    ])
  })
  it('blocks a scripts save while a workflow save is pending after a tab switch', async () => {
    mount(); act(() => state().updateSpecField('title', 'Pending workflow'))
    const pending = deferred<unknown>()
    mocks.fetchJson.mockImplementationOnce(async (_url: string, init?: RequestInit) => {
      mocks.db = JSON.parse(await blobText(init!.body as Blob)).spec
      return pending.promise
    })
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('Pending workflow'))
    expect(writes()).toHaveLength(1)
    tab('脚本文件')
    await screen.findByText(/Pack：/)
    const scriptSave = screen.getByRole('button', { name: '保存到草稿', exact: true })
    expect(scriptSave).toBeDisabled()
    fireEvent.click(scriptSave); await act(async () => {})
    expect(writes()).toHaveLength(1)
    await act(async () => { pending.resolve({ ok: true, saved: true, workflowId: mocks.spec!.id, packId: 'pack' }) })
  })
  it('preserves a null DB baseline for an active-only workflow and saves it without createOnly or Git', async () => {
    mocks.baseline = null; mocks.db = null
    mount(); await waitFor(() => expect(state().spec).toEqual(mocks.spec))
    expect(state()._databaseSpec).toBeNull()
    act(() => state().updateSpecField('title', 'First DB draft'))
    click('保存'); await screen.findByText(/工作流已保存到数据库/)
    expect(state()._databaseSpec?.title).toBe('First DB draft')
    expect(writes()).toHaveLength(1)
    expect(JSON.parse(await blobText(writes()[0][1].body))).toEqual({ workflowId: mocks.spec!.id, spec: state().spec, expectedDraftAbsent: true, expectedActiveDeployNumber: 7 })
    expect(gitReads()).toEqual([])
  })
  it('does not present a historical release as active when the active pointer is absent', async () => {
    mocks.history.mockResolvedValue({ active: null, history: [{ deployNumber: 9, version: 9, action: 'deploy' }] })
    mount(); await screen.findByText('未发布 · 数据库暂存稿')
    expect(mocks.snapshot).not.toHaveBeenCalled()
    expect(screen.queryByText(/生效 v9|最新发布 v9/)).not.toBeInTheDocument()
  })
  it('reports an unavailable active status without claiming unpublished or blocking a DB save', async () => {
    mocks.history.mockRejectedValue(new Error('history unavailable'))
    mount(); await screen.findByText(/生效版本状态读取失败/)
    expect(screen.queryByText('未发布 · 数据库暂存稿')).not.toBeInTheDocument()
    act(() => state().updateSpecField('title', 'DB independent'))
    click('保存'); await screen.findByText(/工作流已保存到数据库/)
    expect(mocks.db?.title).toBe('DB independent'); expect(gitReads()).toEqual([])
  })
  it('only persists applied YAML on top-level save; typing and Apply do not save', async () => {
    mount(); click('YAML')
    const edited = { ...mocks.spec!, title: 'Applied draft' }
    fireEvent.change(screen.getByLabelText('工作流 YAML'), { target: { value: stringify(edited) } })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await screen.findByText(/存在未应用的配置草稿/)
    expect(writes()).toEqual([]); expect(state().spec?.title).toBe('DB draft')
    click('校验并应用'); await screen.findByText(/尚未保存到服务器/)
    expect(mocks.validate).toHaveBeenCalledWith(edited)
    expect(state().spec?.title).toBe('Applied draft'); expect(state().isDirty).toBe(true)
    expect(mocks.db?.title).toBe('DB draft'); expect(writes()).toEqual([])
    click('保存'); await screen.findByText(/工作流已保存到数据库/)
    expect(mocks.db?.title).toBe('Applied draft'); expect(state().isDirty).toBe(false)
    expect(writes()).toHaveLength(1); expect(writes()[0][1].method).toBe('POST')
    const body = JSON.parse(await blobText(writes()[0][1].body))
    expect(body).toEqual({ workflowId: edited.id, spec: edited })
    expect(gitReads()).toEqual([]); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('hides a scripts 503 after returning to workflow and still saves a dirty canvas', async () => {
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes('pack-version')) throw { status: 503, body: 'Git offline' }
      const input = JSON.parse(await blobText(init!.body as Blob)); mocks.db = input.spec
      return { ok: true, saved: true, workflowId: input.workflowId, packId: 'pack' }
    })
    mount(); tab('脚本文件'); await screen.findAllByText(/版本服务暂不可用/)
    tab('工作流编排'); expect(screen.queryByText(/版本服务暂不可用/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Git 基线|未授予|写入能力/)).not.toBeInTheDocument()
    act(() => state().updateSpecField('title', 'After script failure'))
    click('保存'); await screen.findByText(/工作流已保存到数据库/)
    expect(mocks.db?.title).toBe('After script failure'); expect(writes().map(([, init]) => init.method)).toEqual(['POST'])
    tab('脚本文件'); expect(screen.getAllByText(/版本服务暂不可用/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/工作流已保存到数据库/)).not.toBeInTheDocument()
  })
  it.each(['dirty', 'blocked', 'loading', 'saving', 'uploading', 'unknown'] as const)('saves DB without changing script state: %s', async risk => {
    const id = mocks.spec!.id, session = sessionFor(id), base = snapshot()
    const files = base.versionFiles.map(f => f.path === 'scripts/run' ? { ...f, contentBase64: btoa('keep local') } : f)
    updateVersion(session, 'current', { snapshot: { ...base, canWrite: false }, files,
      blocked: risk === 'blocked', loading: risk === 'loading', error: risk === 'blocked' ? 'Git blocked' : null })
    const reconciliation = risk === 'unknown' ? { token: {}, reason: 'no-response' } as NonNullable<typeof session.state.reconciliation> : null
    publish(session, { ...session.state, saving: risk === 'saving', uploading: risk === 'uploading', reconciliation })
    const retainedState = session.state
    mount(); act(() => state().updateSpecField('title', `DB despite ${risk}`))
    if (risk === 'saving') {
      // A Pack-file save already contains a DB write component; switching tabs
      // must not expose a second document-save guard.
      expect(screen.getByRole('button', { name: '正在保存…', exact: true })).toBeDisabled()
      fireEvent.keyDown(window, { key: 's', ctrlKey: true })
      await act(async () => {})
      expect(screen.queryByText(/工作流已保存到数据库/)).not.toBeInTheDocument(); expect(writes()).toEqual([])
    } else {
      expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
      fireEvent.keyDown(window, { key: 's', ctrlKey: true }); await screen.findByText(/工作流已保存到数据库/)
      expect(mocks.db?.title).toBe(`DB despite ${risk}`); expect(writes().map(([, init]) => init.method)).toEqual(['POST'])
    }
    expect(session.state).toBe(retainedState)
    expect(session.state.versions.current.files).toEqual(files)
    expect(session.state.reconciliation).toBe(reconciliation)
    expect(session.state.saving).toBe(risk === 'saving'); expect(session.state.uploading).toBe(risk === 'uploading')
    expect(session.state.versions.current.blocked).toBe(risk === 'blocked')
    expect(gitReads()).toEqual([])
    // Release synthetic locks so later tests retain realistic independent sessions.
    act(() => publish(session, { ...session.state, saving: false, uploading: false }))
  })
  it('keeps DB errors and unsaved edits visible without a Git operation', async () => {
    mount(); act(() => state().updateSpecField('title', 'Keep canvas'))
    mocks.fetchJson.mockRejectedValue(new Error('DB offline')); click('保存')
    await screen.findByText(/DB offline/)
    expect(state().spec?.title).toBe('Keep canvas'); expect(state().isDirty).toBe(true)
    expect(gitReads()).toEqual([]); expect(writes().map(([, init]) => init.method)).toEqual(['POST'])
  })
  it('does not overwrite canvas edits with a late source result', async () => {
    const original = mocks.spec!, draft = mocks.baseline
    mocks.spec = undefined; mocks.baseline = undefined
    const view = render(<Editor embedded initialWorkflowId={original.id} />)
    act(() => { state().loadSpec(original); state().updateSpecField('title', 'Local edit') })
    mocks.spec = { ...original, title: 'Late data' }; mocks.baseline = draft
    view.rerender(<Editor embedded initialWorkflowId={original.id} />)
    expect(state().spec?.title).toBe('Local edit'); expect(state().isDirty).toBe(true)
    await act(async () => {})
    expect(gitReads()).toEqual([])
  })
  it('retains newer edits during a DB save without overwriting them on verified completion', async () => {
    mount(); act(() => state().updateSpecField('title', 'Submitted'))
    const pending = deferred<unknown>()
    mocks.fetchJson.mockImplementationOnce(async (_url, init) => {
      mocks.db = JSON.parse(await blobText(init.body)).spec
      return pending.promise
    })
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('Submitted'))
    act(() => state().updateSpecField('title', 'Newer'))
    await act(async () => { pending.resolve({ ok: true, saved: true, workflowId: mocks.spec!.id, packId: 'pack' }); await pending.promise })
    expect(state().spec?.title).toBe('Newer'); expect(state().isDirty).toBe(true)
    expect(state()._databaseSpec?.title).toBe('Submitted'); expect(gitReads()).toEqual([])
  })
  it('ignores completion from a replaced editor lifetime, including its save message', async () => {
    mount(); act(() => state().updateSpecField('title', 'Submitted'))
    const pending = deferred<unknown>()
    mocks.fetchJson.mockImplementationOnce(async (_url, init) => {
      mocks.db = JSON.parse(await blobText(init.body)).spec
      return pending.promise
    })
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('Submitted'))
    act(() => { state().reset({ force: true }); state().loadSpec({ ...mocks.spec!, title: 'Replacement lifetime' }) })
    await act(async () => { pending.resolve({ ok: true, saved: true, workflowId: mocks.spec!.id, packId: 'pack' }); await pending.promise })
    expect(state().spec?.title).toBe('Replacement lifetime')
    expect(state()._databaseSpec?.title).toBe('Replacement lifetime')
    expect(screen.queryByText(/工作流已保存到数据库/)).not.toBeInTheDocument()
  })
  it.each(['draft', 'current'] as const)('fetches a fresh %s deployment baseline only on explicit deploy', async source => {
    mocks.fetchJson.mockImplementation(async url => {
      if (source === 'current' && url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
      return snapshot(source)
    })
    mount(); await act(async () => {})
    expect(gitReads()).toEqual([])
    click('部署'); const confirm = await screen.findByRole('button', { name: '确认部署' })
    expect(gitReads().map(([url]) => url)).toEqual([
      `/api/workflows/${mocks.spec!.id}/pack-version?version=draft`,
      ...(source === 'current' ? [`/api/workflows/${mocks.spec!.id}/pack-version?version=current`] : []),
    ])
    fireEvent.click(confirm); await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.spec!.id, candidateCommit: A }))
  })
})


describe('cold deployment preserves pre-existing layout while detecting new layout changes', () => {
  async function mountLayoutOnlyDraft(moved = true) {
    mocks.spec = { ...mocks.spec!, nodes: [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' }, _x: 80, _y: 80 }] }
    mocks.baseline = structuredClone(mocks.spec); mocks.db = structuredClone(mocks.spec)
    mount()
    if (moved) act(() => state().moveNode('done', 400, 200))
    expect(state().isDirty).toBe(false); expect(state().isLayoutDirty).toBe(moved)
    click('保存'); await screen.findByText(/没有需要保存的更改/)
    expect(state().isLayoutDirty).toBe(moved)
    expect(gitReads()).toEqual([]); expect(writes()).toEqual([])
    const session = sessionFor(mocks.spec.id)
    expect(session.state.versions.current.snapshot).toBeNull()
    expect(session.state.versions.draft.snapshot).toBeNull()
    expect(screen.getByRole('tab', { name: '脚本文件' })).toHaveAttribute('aria-selected', 'false')
    return session
  }

  it.each(['draft', 'current'] as const)('deploys a fresh %s after a layout-only drag and DB no-op save without opening scripts', async source => {
    const session = await mountLayoutOnlyDraft()
    mocks.fetchJson.mockImplementation(async url => {
      if (source === 'current' && url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
      return snapshot(source)
    })
    click('部署'); const confirm = await screen.findByRole('button', { name: '确认部署' })
    expect(gitReads().map(([url]) => url)).toEqual([
      `/api/workflows/${mocks.spec!.id}/pack-version?version=draft`,
      ...(source === 'current' ? [`/api/workflows/${mocks.spec!.id}/pack-version?version=current`] : []),
    ])
    fireEvent.click(confirm)
    await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledOnce()
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.spec!.id, candidateCommit: A }))
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
    mocks.fetchJson.mockReturnValueOnce(pending.promise)
    click('部署'); expect(gitReads()).toHaveLength(1)
    act(() => state().moveNode('done', 600, 300))
    await act(async () => { pending.resolve(snapshot('draft')); await pending.promise })
    expect(await screen.findByText(/读取期间.*布局.*变化/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled(); expect(writes()).toEqual([])
    expect(state().positions.done).toEqual({ x: 600, y: 300 }); expect(state().isLayoutDirty).toBe(true)
  })

  it.each([false, true])('blocks layout changes while the confirmation is open (already layout dirty: %s)', async moved => {
    await mountLayoutOnlyDraft(moved)
    click('部署'); const confirm = await screen.findByRole('button', { name: '确认部署' })
    act(() => state().moveNode('done', 600, 300))
    mocks.get.mockClear()
    fireEvent.click(confirm)
    expect(await screen.findByText(/确认期间编辑内容已变化/)).toBeInTheDocument()
    expect(mocks.get).not.toHaveBeenCalled(); expect(mocks.deploy).not.toHaveBeenCalled()
    expect(state().positions.done).toEqual({ x: 600, y: 300 }); expect(state().isLayoutDirty).toBe(true)
    expect(writes()).toEqual([])
  })

  it.each([false, true])('blocks layout changes during the final DB verification (already layout dirty: %s)', async moved => {
    await mountLayoutOnlyDraft(moved)
    click('部署'); const confirm = await screen.findByRole('button', { name: '确认部署' })
    const pending = deferred<WorkflowSpec>()
    mocks.get.mockClear().mockReturnValueOnce(pending.promise)
    fireEvent.click(confirm); await waitFor(() => expect(mocks.get).toHaveBeenCalledOnce())
    act(() => state().moveNode('done', 600, 300))
    await act(async () => { pending.resolve(mocks.db!); await pending.promise })
    expect(await screen.findByText(/数据库草稿或本地编辑已变化/)).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled(); expect(writes()).toEqual([])
    expect(state().positions.done).toEqual({ x: 600, y: 300 }); expect(state().isLayoutDirty).toBe(true)
  })
})
