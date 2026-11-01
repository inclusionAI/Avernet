import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parse, stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ db: null as WorkflowSpec | null, fetchJson: vi.fn(), deploy: vi.fn(), history: vi.fn(), saveDb: vi.fn(), gitSave: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.db, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
  useSaveWorkflowToDb: () => ({ mutateAsync: mocks.saveDb }), useAutoHealSave: () => ({ mutateAsync: mocks.gitSave }),
  useFacadeBindings: () => ({ data: [] }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson,
  api: { workflows: { getHistory: mocks.history, getDeploySnapshot: vi.fn() } } }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { sessionFor, type Snapshot } from '../../editor/packVersionSession'

const A = 'a'.repeat(40), B = 'b'.repeat(40)
let sequence = 0
let current: Snapshot, saved: Snapshot
const state = () => useEditorStore.getState()
const file = (path: string, text: string, mode: '100644' | '100755' = '100644') => ({ path, contentBase64: btoa(text), mode })
function snapshot(version: 'current' | 'draft', spec = mocks.db!): Snapshot {
  return { workflowId: spec.id, packId: 'pack', canWrite: true, baseRef: `origin/${version === 'draft' ? 'saved' : 'wf'}/${spec.id}`,
    baseCommit: version === 'draft' ? B : A, draftHead: version === 'draft' ? B : null,
    versionFiles: [file('workflow.pack.yaml', stringify({ id: 'pack', workflows: [{ id: spec.id, file: 'flows/custom.yaml' }] })),
      file('flows/custom.yaml', stringify(spec)), file('scripts/run', 'original', '100755'), { path: 'assets/raw', contentBase64: 'AP8C', mode: '100644' }] }
}
function readBlob(blob: Blob): Promise<string> { return new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) }) }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { resolve, promise } }
async function mount() { render(<Editor embedded initialWorkflowId={mocks.db!.id} />); await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.current.loading).toBe(false)) }
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))
async function editFile(path: string, text: string) {
  fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click(path)
  fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: text } })
}
function putCalls() { return mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT') }
beforeEach(() => {
  vi.resetAllMocks(); state().reset({ force: true })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  mocks.db = { id: `editor-save-${++sequence}`, title: 'Original', version: '1', nodes: [] }
  state().loadSpec(mocks.db)
  current = snapshot('current'); saved = snapshot('draft')
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: input.workflowId, packId: input.packId, deployNumber: 2, version: 3, tagName: 'deploy/test/2' }))
  mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      const input = JSON.parse(await readBlob(init.body as Blob))
      const byPath = new Map((input.sourceVersion === 'draft' ? saved : current).versionFiles.map(item => [item.path, item]))
      for (const change of input.changes) {
        if (change.operation === 'delete') byPath.delete(change.path)
        else byPath.set(change.path, { path: change.path, mode: change.mode, contentBase64: change.contentBase64 })
      }
      const packId = parse(atob(byPath.get('workflow.pack.yaml')!.contentBase64)).id
      saved = { ...saved, packId, versionFiles: [...byPath.values()] }
      return { branch: `saved/${mocks.db!.id}`, commit: B, requestId: input.requestId, version: 2, packId }
    }
    return url.includes('version=draft') ? saved : current
  })
})
afterEach(() => { cleanup(); state().reset({ force: true }); vi.restoreAllMocks() })

describe('Editor full Pack snapshot save and confirmed deployment', () => {
  it('merges canvas YAML at the manifest path and file edits against the captured read baseline', async () => {
    await mount()
    act(() => state().updateSpecField('title', 'Canvas edit'))
    await editFile('scripts/run', 'new script')
    click('保存到草稿')
    await screen.findByText(new RegExp(`已保存到草稿 commit ${B}`))
    expect(putCalls()).toHaveLength(1)
    const input = JSON.parse(await readBlob(putCalls()[0][1].body))
    expect(input).toMatchObject({ sourceVersion: 'current', sourceCommit: A, expectedSavedHead: null, requestId: expect.stringMatching(/^[\da-f-]{36}$/) })
    expect(input.changes).toHaveLength(2)
    const yaml = input.changes.find((change: { path: string }) => change.path === 'flows/custom.yaml')
    expect(parse(atob(yaml.contentBase64))).toMatchObject({ title: 'Canvas edit' })
    expect(input.changes.find((change: { path: string }) => change.path === 'scripts/run')).toMatchObject({ mode: '100755', contentBase64: btoa('new script') })
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')).toEqual(current.versionFiles.find(item => item.path === 'assets/raw'))
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => !init?.method)).toHaveLength(1)
    expect(state().isDirty).toBe(false)
    expect(mocks.saveDb).not.toHaveBeenCalled(); expect(mocks.gitSave).not.toHaveBeenCalled()
    click('部署工作流')
    expect(await screen.findByText(/草稿 commit：/)).toHaveTextContent(B)
    click('确认部署')
    await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.db!.id, packId: 'pack', candidateCommit: B, requestId: expect.stringMatching(/^[\da-f-]{36}$/) }))
  })
  it.each([false, true])('binds a legal Pack rename with canvas edits=%s and deploys the new identity', async editCanvas => {
    await mount()
    if (editCanvas) act(() => state().updateSpecField('title', 'Canvas plus rename'))
    await editFile('workflow.pack.yaml', stringify({ id: 'renamed-pack', workflows: [{ id: mocks.db!.id, file: 'flows/custom.yaml' }] }))
    await editFile('scripts/run', 'renamed script')
    click('保存到草稿'); await screen.findByText(new RegExp(`已保存到草稿 commit ${B}`))
    expect(state().isDirty).toBe(false)
    expect(state().spec!.id).toBe(mocks.db!.id)
    expect(state().spec!.title).toBe(editCanvas ? 'Canvas plus rename' : 'Original')
    const session = sessionFor(mocks.db!.id)
    expect(session.state.versions.draft.snapshot!.packId).toBe('renamed-pack')
    expect(session.state.versions.draft.snapshot!.baseCommit).toBe(B)
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')).toEqual(current.versionFiles.find(item => item.path === 'assets/raw'))
    expect(saved.versionFiles.find(item => item.path === 'scripts/run')).toMatchObject({ mode: '100755', contentBase64: btoa('renamed script') })
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => !init?.method)).toHaveLength(1)
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    act(() => state().updateSpecField('title', 'Next canvas edit'))
    click('保存'); await waitFor(() => expect(state().isDirty).toBe(false))
    expect(putCalls()).toHaveLength(2)
    expect(JSON.parse(await readBlob(putCalls()[1][1].body))).toMatchObject({ sourceVersion: 'draft', sourceCommit: B, expectedSavedHead: B })
    expect(screen.queryByText(/数据库初始文档与 Git 基线不一致/)).not.toBeInTheDocument()
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); click('确认部署')
    await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.db!.id, packId: 'renamed-pack', candidateCommit: B }))
  })
  it('does not clean a renamed canvas candidate when the server returns the previous Pack ID', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Keep canvas'))
    await editFile('workflow.pack.yaml', stringify({ id: 'renamed-pack', workflows: [{ id: mocks.db!.id, file: 'flows/custom.yaml' }] }))
    mocks.fetchJson.mockImplementationOnce(async (_url, init) => ({ branch: `saved/${mocks.db!.id}`, commit: B,
      requestId: JSON.parse(await readBlob(init!.body as Blob)).requestId, version: 2, packId: 'pack' }))
    click('保存到草稿'); await screen.findByRole('region', { name: '保存结果对账' })
    expect(state().isDirty).toBe(true); expect(state().spec!.title).toBe('Keep canvas')
    expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).toBeNull()
    expect(sessionFor(mocks.db!.id).state.reconciliation!.attempt.expectedPackId).toBe('renamed-pack')
  })
  it('keeps the DB initial positions after verifying the Git execution spec', async () => {
    mocks.db!.nodes = [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' }, _x: 501, _y: 202 }]
    state().reset({ force: true }); state().loadSpec(mocks.db!)
    const gitSpec = { ...mocks.db!, nodes: mocks.db!.nodes.map(({ _x, _y, ...node }) => node) }
    current = snapshot('current', gitSpec)
    await mount()
    expect(state().positions.done).toEqual({ x: 501, y: 202 })
    expect(screen.queryByText(/数据库初始文档与 Git 基线不一致/)).not.toBeInTheDocument()
    act(() => state().updateSpecField('title', 'Updated'))
    click('保存'); await screen.findByText(/完整版本已保存/)
    expect(state().positions.done).toEqual({ x: 501, y: 202 })
    const input = JSON.parse(await readBlob(putCalls()[0][1].body))
    expect(parse(atob(input.changes[0].contentBase64)).nodes[0]).not.toHaveProperty('_x')
  })
  it('reports DB/Git mismatch explicitly and permits selecting Git instead of silently rebasing', async () => {
    current = snapshot('current', { ...mocks.db!, title: 'Git version' })
    await mount()
    expect(screen.getByText(/数据库初始文档与 Git 基线不一致/)).toBeInTheDocument()
    click('保存'); await waitFor(() => expect(putCalls()).toHaveLength(0))
    click('以此 Git 基线加载画布')
    await waitFor(() => expect(state().spec!.title).toBe('Git version'))
    act(() => state().updateSpecField('title', 'Updated'))
    click('保存'); await screen.findByText(/完整版本已保存/)
  })
  it('rejects divergent canvas/file changes to the same YAML without losing either edit', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Canvas'))
    await editFile('flows/custom.yaml', stringify({ ...mocks.db!, title: 'Files' }))
    click('保存到草稿')
    await screen.findByText(/画布和文件编辑同时修改/)
    expect(state().spec!.title).toBe('Canvas'); expect((screen.getByLabelText('文件内容') as HTMLTextAreaElement).value).toContain('Files')
    expect(putCalls()).toHaveLength(0)
  })
  it('adopts file-only workflow changes after save without a stale canvas overwriting them', async () => {
    await mount(); await editFile('flows/custom.yaml', stringify({ ...mocks.db!, title: 'File edit' }))
    click('保存到草稿'); await screen.findByText(/完整版本已保存/)
    expect(state().spec!.title).toBe('File edit'); expect(state().isDirty).toBe(false)
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); click('保存')
    expect(putCalls()).toHaveLength(1)
  })
  it('preserves merged YAML on CAS conflict and never fetches a new baseline at save time', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Keep local'))
    mocks.fetchJson.mockRejectedValueOnce({ status: 409, body: '{"code":"VERSION_CONFLICT"}' })
    click('保存'); await screen.findAllByText(/保存冲突/)
    expect(state().isDirty).toBe(true)
    const session = sessionFor(mocks.db!.id)
    expect(session.state.versions.current.snapshot?.baseCommit).toBe(A)
    expect(parse(atob(session.state.versions.current.files.find(item => item.path === 'flows/custom.yaml')!.contentBase64)).title).toBe('Keep local')
    expect(mocks.fetchJson).toHaveBeenCalledTimes(2)
    click('部署'); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('blocks a public host lacking canWrite without globally disabling the editor', async () => {
    delete current.canWrite; await mount(); act(() => state().updateSpecField('title', 'Local'))
    click('保存'); await screen.findByText(/未授予版本写入权限/)
    expect(putCalls()).toHaveLength(0); expect(state().isDirty).toBe(true)
  })
  it('keeps pending drafts and Ctrl-S cannot bypass the apply boundary', async () => {
    await mount(); act(() => state().setPendingEdit('yaml', 'unapplied'))
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await screen.findByText(/存在未应用的配置草稿/)
    expect(putCalls()).toHaveLength(0); expect(state().pendingEdits).toEqual({ yaml: 'unapplied' })
    click('部署'); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('does not mark edits made while a save is in flight clean', async () => {
    await mount(); act(() => state().updateSpecField('title', 'First'))
    const pending = deferred<unknown>(); mocks.fetchJson.mockImplementationOnce(() => pending.promise)
    click('保存'); act(() => state().updateSpecField('title', 'Later'))
    const input = JSON.parse(await readBlob(putCalls()[0][1].body))
    await act(async () => { pending.resolve({ branch: `saved/${mocks.db!.id}`, commit: B, requestId: input.requestId, version: 2, packId: 'pack' }); await pending.promise })
    expect(state().spec!.title).toBe('Later'); expect(state().isDirty).toBe(true)
  })
  it('ignores a late save completion for a replaced canvas lifetime', async () => {
    await mount(); act(() => state().updateSpecField('title', 'First'))
    const pending = deferred<unknown>(); mocks.fetchJson.mockImplementationOnce(() => pending.promise)
    click('保存'); const input = JSON.parse(await readBlob(putCalls()[0][1].body))
    act(() => { state().reset({ force: true }); state().loadSpec({ ...mocks.db!, title: 'Different lifetime' }) })
    await act(async () => { pending.resolve({ branch: `saved/${mocks.db!.id}`, commit: B, requestId: input.requestId, version: 2, packId: 'pack' }); await pending.promise })
    expect(state().spec!.title).toBe('Different lifetime')
  })
  it('rejects a deployment confirmation if edits happen after opening it', async () => {
    await mount(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('草稿版（saved）'); await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).not.toBeNull()); fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); click('部署'); await screen.findByRole('button', { name: '确认部署' })
    act(() => state().updateSpecField('title', 'Later'))
    click('确认部署'); expect(screen.getByText(/确认期间编辑内容已变化/)).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('retains the original Git baseline through a dirty canvas unmount/remount', async () => {
    const view = render(<Editor embedded initialWorkflowId={mocks.db!.id} />)
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.current.loading).toBe(false))
    act(() => state().updateSpecField('title', 'Persist local'))
    view.unmount()
    current = { ...current, baseCommit: 'c'.repeat(40) }
    render(<Editor embedded initialWorkflowId={mocks.db!.id} />)
    click('保存'); await screen.findByText(/完整版本已保存/)
    const input = JSON.parse(await readBlob(putCalls()[0][1].body))
    expect(input.sourceCommit).toBe(A)
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => !init?.method)).toHaveLength(1)
  })
  it('prefers the existing saved snapshot on a clean open and validates the database against it', async () => {
    current = { ...current, draftHead: B, versionFiles: snapshot('current', { ...mocks.db!, title: 'Older current' }).versionFiles }
    await mount()
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot?.baseCommit).toBe(B))
    act(() => state().updateSpecField('title', 'Edit saved'))
    click('保存'); await screen.findByText(/完整版本已保存/)
    const input = JSON.parse(await readBlob(putCalls()[0][1].body))
    expect(input).toMatchObject({ sourceVersion: 'draft', sourceCommit: B, expectedSavedHead: B })
  })
  it('can explicitly edit current after a newer saved exists, confirming its captured saved head', async () => {
    current = { ...current, draftHead: B }
    saved = snapshot('draft', { ...mocks.db!, title: 'Saved work' })
    await mount()
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.version).toBe('draft'))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    fireEvent.click(screen.getByRole('button', { name: /当前版/ }))
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    act(() => state().updateSpecField('title', 'Current after publication'))
    click('保存'); await screen.findByText(/完整版本已保存/)
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(B))
    const input = JSON.parse(await readBlob(putCalls()[0][1].body))
    expect(input).toMatchObject({ sourceVersion: 'current', sourceCommit: A, expectedSavedHead: B })
  })
  it('creates with createOnly + requestId, then edits the captured saved snapshot without any DB-first upsert', async () => {
    mocks.db = null
    state().reset({ force: true })
    render(<Editor />)
    click('更多操作'); fireEvent.click(screen.getByRole('menuitem', { name: '新建工作流' }))
    fireEvent.change(screen.getByLabelText('标识'), { target: { value: 'brand-new' } })
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'New' } })
    click('Create')
    expect(state().spec?.id).toBe('brand-new'); expect(state().isDirty).toBe(true)
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/api/auto-heal/save') {
        const input = JSON.parse(await readBlob(init!.body as Blob))
        mocks.db = input.spec
        saved = { ...snapshot('draft', input.spec), packId: input.packId,
          versionFiles: [file('workflow.pack.yaml', stringify({ id: input.packId, workflows: [{ id: input.workflowId, file: 'flows/custom.yaml' }] })), file('flows/custom.yaml', stringify(input.spec))] }
        return { branch: `saved/${input.workflowId}`, commit: B, requestId: input.requestId, version: 1, packId: input.packId }
      }
      if (init?.method === 'PUT') {
        const input = JSON.parse(await readBlob(init.body as Blob))
        return { branch: 'saved/brand-new', commit: 'c'.repeat(40), requestId: input.requestId, version: 2, packId: 'brand-new' }
      }
      return saved
    })
    click('保存'); await screen.findByText(/新建完整版本已保存/)
    const createCall = mocks.fetchJson.mock.calls.find(([url]) => url === '/api/auto-heal/save')!
    expect(JSON.parse(await readBlob(createCall[1].body))).toMatchObject({ workflowId: 'brand-new', createOnly: true, expectedSavedHead: null, requestId: expect.stringMatching(/^[\da-f-]{36}$/) })
    expect(state().isDirty).toBe(false)
    act(() => state().updateSpecField('title', 'New edit'))
    click('保存'); await screen.findByText(/完整版本已保存到 saved commit c/)
    expect(JSON.parse(await readBlob(putCalls()[0][1].body))).toMatchObject({ sourceVersion: 'draft', sourceCommit: B, expectedSavedHead: B })
    expect(mocks.fetchJson.mock.calls.some(([url]) => url === '/api/workflows/save')).toBe(false)
    expect(mocks.saveDb).not.toHaveBeenCalled(); expect(mocks.gitSave).not.toHaveBeenCalled()
  })
  it('retains the deployment request ID after an unknown result and retries the exact candidate', async () => {
    await mount(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('草稿版（saved）'); await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).not.toBeNull()); fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); mocks.deploy.mockRejectedValueOnce(new TypeError('network'))
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); click('确认部署')
    await screen.findByText(/部署结果尚未确认/)
    const first = mocks.deploy.mock.calls[0][0]
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); click('确认部署')
    await screen.findByText(/部署成功/)
    expect(mocks.deploy.mock.calls[1][0]).toEqual(first)
  })
})
