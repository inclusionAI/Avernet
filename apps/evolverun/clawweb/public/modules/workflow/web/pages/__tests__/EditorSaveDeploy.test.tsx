import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parse, stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ db: null as WorkflowSpec | null, fetchJson: vi.fn(), deploy: vi.fn(), history: vi.fn(), saveDb: vi.fn(), gitSave: vi.fn(), get: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.db, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
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
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { sessionFor, updateVersion, type Snapshot } from '../../editor/packVersionSession'

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
  fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); fireEvent.click(await screen.findByRole('button', { name: path, exact: true }))
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
  mocks.get.mockImplementation(async () => mocks.db)
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: input.workflowId, packId: input.packId, deployNumber: 2, version: 3, tagName: 'deploy/test/2' }))
  mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
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
      return { branch: `saved/${mocks.db!.id}`, commit: B, requestId: input.requestId, version: 2, packId }
    }
    return url.includes('version=draft') ? saved : current
  })
})
afterEach(() => { cleanup(); state().reset({ force: true }); vi.restoreAllMocks() })

describe('Editor DB workflow and saved Pack files', () => {
  it('saves canvas to DB and file edits to Git separately, then deploys the saved candidate', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Canvas edit'))
    await editFile('scripts/run', 'new script'); click('保存到草稿')
    await screen.findByText(new RegExp(`文件已保存到 saved commit ${B}`))
    const writes = mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)
    expect(writes.map(([, init]) => init.method)).toEqual(['POST', 'PUT'])
    const dbInput = JSON.parse(await readBlob(writes[0][1].body))
    expect(dbInput).toMatchObject({ workflowId: mocks.db!.id, spec: { title: 'Canvas edit' } })
    expect(dbInput).not.toHaveProperty('packId')
    const input = JSON.parse(await readBlob(writes[1][1].body))
    expect(input).toMatchObject({ sourceVersion: 'current', sourceCommit: A, expectedSavedHead: null, requestId: expect.stringMatching(/^[\da-f-]{36}$/) })
    expect(input.changes).toEqual([expect.objectContaining({ path: 'scripts/run', mode: '100755', contentBase64: btoa('new script') })])
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')).toEqual(current.versionFiles.find(item => item.path === 'assets/raw'))
    expect(saved.versionFiles.find(item => item.path === 'flows/custom.yaml')).toEqual(current.versionFiles.find(item => item.path === 'flows/custom.yaml'))
    expect(state().isDirty).toBe(false)
    click('部署工作流'); await screen.findByRole('button', { name: '确认部署' }); click('确认部署')
    await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.db!.id, packId: 'pack', candidateCommit: B }))
  })
  it.each(['identity', 'path'] as const)('saves a Pack %s change with canvas edits and deploys the resulting candidate', async change => {
    await mount(); act(() => state().updateSpecField('title', 'Keep canvas'))
    const packId = change === 'identity' ? 'renamed-pack' : 'pack'
    const path = change === 'path' ? 'flows/other.yaml' : 'flows/custom.yaml'
    const originalFile = current.versionFiles.find(item => item.path === 'flows/custom.yaml')!
    if (change === 'path') {
      fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
      await screen.findByRole('button', { name: 'scripts/run' })
      const session = sessionFor(mocks.db!.id)
      act(() => updateVersion(session, 'current', { files: session.state.versions.current.files.map(item =>
        item.path === 'flows/custom.yaml' ? { ...item, path } : item) }))
    }
    await editFile('workflow.pack.yaml', stringify({ id: packId, workflows: [{ id: mocks.db!.id, file: path }] }))
    click('保存到草稿'); await screen.findByText(new RegExp(`文件已保存到 saved commit ${B}`))
    const writes = mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)
    expect(writes.map(([, init]) => init.method)).toEqual(['POST', 'PUT'])
    const dbInput = JSON.parse(await readBlob(writes[0][1].body))
    expect(dbInput).toMatchObject({ workflowId: mocks.db!.id, spec: { title: 'Keep canvas' } })
    if (change === 'identity') expect(dbInput.packId).toBe(packId)
    else expect(dbInput).not.toHaveProperty('packId')
    const gitInput = JSON.parse(await readBlob(writes[1][1].body))
    expect(gitInput).toMatchObject({ sourceVersion: 'current', sourceCommit: A, expectedSavedHead: null })
    const manifestChange = gitInput.changes.find((item: { path: string }) => item.path === 'workflow.pack.yaml')
    expect(parse(atob(manifestChange.contentBase64))).toMatchObject({ id: packId, workflows: [{ id: mocks.db!.id, file: path }] })
    if (change === 'path') {
      expect(gitInput.changes).toContainEqual({ path: 'flows/custom.yaml', operation: 'delete' })
      expect(gitInput.changes).toContainEqual({ ...originalFile, path, operation: 'create' })
      expect(saved.versionFiles.some(item => item.path === 'flows/custom.yaml')).toBe(false)
    }
    expect(saved.versionFiles.find(item => item.path === path)).toEqual({ ...originalFile, path })
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')).toEqual(current.versionFiles.find(item => item.path === 'assets/raw'))
    expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).toMatchObject({ packId, baseCommit: B })
    expect(state().spec!.title).toBe('Keep canvas'); expect(state().isDirty).toBe(false)
    click('部署工作流'); await screen.findByRole('button', { name: '确认部署' }); click('确认部署')
    await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.db!.id, packId, candidateCommit: B }))
  })
  it('retains edits and blocks Git when a Pack rename is acknowledged with the wrong identity', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Keep canvas'))
    await editFile('workflow.pack.yaml', stringify({ id: 'renamed-pack', workflows: [{ id: mocks.db!.id, file: 'flows/custom.yaml' }] }))
    mocks.fetchJson.mockResolvedValueOnce({ ok: true, saved: true, workflowId: mocks.db!.id, packId: 'pack' })
    click('保存到草稿'); expect(await screen.findAllByText(/Git 保存未发起/)).not.toHaveLength(0)
    expect(state().isDirty).toBe(true); expect(state().spec!.title).toBe('Keep canvas')
    expect((screen.getByLabelText('文件内容') as HTMLTextAreaElement).value).toContain('renamed-pack')
    expect(putCalls()).toHaveLength(0)
    expect(sessionFor(mocks.db!.id).state.versions.current.blocked).toBe(true)
  })
  it('saves DB layout and leaves Git untouched for canvas-only edits', async () => {
    mocks.db!.nodes = [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' }, _x: 501, _y: 202 }]
    state().reset({ force: true }); state().loadSpec(mocks.db!); current = snapshot('current', { ...mocks.db!, nodes: mocks.db!.nodes.map(({ _x, _y, ...node }) => node) })
    await mount(); act(() => state().updateSpecField('title', 'Updated')); click('保存')
    await screen.findByText(/工作流已保存到数据库/)
    expect(state().positions.done).toEqual({ x: 501, y: 202 }); expect(putCalls()).toHaveLength(0)
  })
  it('does not replace the DB draft merely because Git YAML differs', async () => {
    current = snapshot('current', { ...mocks.db!, title: 'Git version' }); await mount()
    expect(state().spec!.title).toBe('Original')
    act(() => state().updateSpecField('title', 'DB edit')); click('保存'); await screen.findByText(/工作流已保存到数据库/)
    expect(mocks.db!.title).toBe('DB edit'); expect(putCalls()).toHaveLength(0)
  })
  it('rejects divergent canvas and workflow-file edits without losing either', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Canvas'))
    await editFile('flows/custom.yaml', stringify({ ...mocks.db!, title: 'Files' })); click('保存到草稿')
    await screen.findByText(/画布和文件同时修改/)
    expect(state().spec!.title).toBe('Canvas'); expect((screen.getByLabelText('文件内容') as HTMLTextAreaElement).value).toContain('Files')
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0)
  })
  it('routes a file-only workflow YAML edit to DB and adopts it on canvas', async () => {
    await mount(); await editFile('flows/custom.yaml', stringify({ ...mocks.db!, title: 'File edit' }))
    click('保存到草稿'); await screen.findByText(/工作流已保存到数据库/)
    expect(state().spec!.title).toBe('File edit'); expect(state().isDirty).toBe(false); expect(putCalls()).toHaveLength(0)
    expect(sessionFor(mocks.db!.id).state.versions.current.files).toEqual(current.versionFiles)
  })
  it('reports partial success on Git conflict after DB save, preserving files and blocking deploy', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Saved DB'))
    await editFile('scripts/run', 'Keep local')
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => init?.method === 'PUT' ? Promise.reject({ status: 409, body: '{"code":"VERSION_CONFLICT"}' }) : original(url, init))
    click('保存到草稿'); await screen.findByText(/部分成功：工作流已保存到数据库/)
    expect(state().isDirty).toBe(false); expect(mocks.db!.title).toBe('Saved DB')
    const version = sessionFor(mocks.db!.id).state.versions.current
    expect(version.snapshot?.baseCommit).toBe(A); expect(version.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Keep local'))
    expect(putCalls()).toHaveLength(1); click('部署工作流'); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('retains both edits and makes no Git write when DB save fails', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Keep canvas')); await editFile('scripts/run', 'Keep file')
    mocks.fetchJson.mockRejectedValueOnce(new Error('DB offline')); click('保存到草稿'); await screen.findByText(/DB offline/)
    expect(state().isDirty).toBe(true); expect(screen.getByLabelText('文件内容')).toHaveValue('Keep file'); expect(putCalls()).toHaveLength(0)
  })
  it('blocks file writes without canWrite but allows a DB-only save', async () => {
    delete current.canWrite; await mount(); act(() => state().updateSpecField('title', 'DB only')); click('保存')
    await screen.findByText(/工作流已保存到数据库/); expect(putCalls()).toHaveLength(0)
    await editFile('scripts/run', 'local'); expect(screen.getByRole('button', { name: '保存到草稿' })).toBeDisabled()
    expect(state().spec!.title).toBe('DB only')
  })
  it('keeps pending drafts and Ctrl-S cannot bypass the apply boundary', async () => {
    await mount(); act(() => state().setPendingEdit('yaml', 'unapplied'))
    fireEvent.keyDown(window, { key: 's', ctrlKey: true }); await screen.findByText(/存在未应用的配置草稿/)
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0); expect(state().pendingEdits).toEqual({ yaml: 'unapplied' })
    click('部署'); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('does not mark edits made during DB save clean', async () => {
    await mount(); act(() => state().updateSpecField('title', 'First'))
    const pending = deferred<unknown>(); mocks.fetchJson.mockImplementationOnce(() => pending.promise)
    click('保存'); act(() => state().updateSpecField('title', 'Later'))
    await act(async () => { pending.resolve({ ok: true, saved: true, workflowId: mocks.db!.id, packId: 'pack' }); await pending.promise })
    expect(state().spec!.title).toBe('Later'); expect(state().isDirty).toBe(true)
  })
  it('ignores late DB save completion for a replaced canvas lifetime', async () => {
    await mount(); act(() => state().updateSpecField('title', 'First'))
    const pending = deferred<unknown>(); mocks.fetchJson.mockImplementationOnce(() => pending.promise); click('保存')
    act(() => { state().reset({ force: true }); state().loadSpec({ ...mocks.db!, title: 'Different lifetime' }) })
    await act(async () => { pending.resolve({ ok: true, saved: true, workflowId: mocks.db!.id, packId: 'pack' }); await pending.promise })
    expect(state().spec!.title).toBe('Different lifetime')
  })
  it('rejects deployment confirmation if canvas edits happen after opening it', async () => {
    await mount(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('草稿版（saved）')
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).not.toBeNull())
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); click('部署'); await screen.findByRole('button', { name: '确认部署' })
    act(() => state().updateSpecField('title', 'Later')); click('确认部署')
    expect(screen.getByText(/确认期间编辑内容已变化/)).toBeInTheDocument(); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('uses captured Git baseline after a dirty file session unmount/remount', async () => {
    const view = render(<Editor embedded initialWorkflowId={mocks.db!.id} />)
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.current.loading).toBe(false))
    await editFile('scripts/run', 'Persist local'); view.unmount(); current = { ...current, baseCommit: 'c'.repeat(40) }
    render(<Editor embedded initialWorkflowId={mocks.db!.id} />); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('保存到草稿')
    await screen.findByText(/文件已保存到 saved commit/)
    expect(JSON.parse(await readBlob(putCalls()[0][1].body)).sourceCommit).toBe(A)
  })
  it('retains a new draft and refuses a blind retry when creation is unknown', async () => {
    mocks.db = null; state().reset({ force: true }); mocks.fetchJson.mockRejectedValue(new TypeError('lost')); render(<Editor />)
    click('更多操作'); fireEvent.click(screen.getByRole('menuitem', { name: '新建工作流' }))
    fireEvent.change(screen.getByLabelText('标识'), { target: { value: 'brand-new' } }); fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'New' } }); click('Create'); click('保存')
    await screen.findByText(/新建结果尚未确认/); expect(state().spec?.id).toBe('brand-new'); expect(state().isDirty).toBe(true)
    click('保存'); await screen.findByText(/勿重试创建/)
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })
  it('does not retry an unknown deployment without a journal', async () => {
    await mount(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('草稿版（saved）')
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).not.toBeNull())
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); mocks.deploy.mockRejectedValueOnce(new TypeError('network'))
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); click('确认部署'); await screen.findByText(/没有日志恢复或安全自动重试/)
    click('部署'); expect(mocks.deploy).toHaveBeenCalledTimes(1)
    expect(mocks.fetchJson.mock.calls.every(([url]) => !url.includes('pack-operations'))).toBe(true)
  })
  it('holds an unverifiable Git result as unknown without cleaning or retrying the candidate', async () => {
    await mount(); await editFile('scripts/run', 'uncertain')
    mocks.fetchJson.mockImplementationOnce(async () => ({ branch: `saved/${mocks.db!.id}`, commit: B, requestId: 'wrong', version: 2, packId: 'pack' }))
    click('保存到草稿'); await screen.findByRole('region', { name: '保存结果对账' })
    const session = sessionFor(mocks.db!.id)
    expect(session.state.reconciliation?.attempt.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('uncertain'))
    expect(session.state.versions.current.blocked).toBe(true)
    expect(screen.queryByRole('button', { name: /重试原始/ })).not.toBeInTheDocument()
    click('部署工作流'); expect(mocks.deploy).not.toHaveBeenCalled(); expect(putCalls()).toHaveLength(1)
    const reconciliation = session.state.reconciliation, files = session.state.versions.current.files
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    expect(screen.queryByRole('region', { name: '保存结果对账' })).not.toBeInTheDocument()
    expect(screen.queryByText(/响应无法验证|文件仍保留；请勿盲目重试/)).not.toBeInTheDocument()
    act(() => state().updateSpecField('title', 'Independent DB edit')); click('保存')
    await screen.findByText(/工作流已保存到数据库/)
    expect(mocks.db!.title).toBe('Independent DB edit'); expect(putCalls()).toHaveLength(1)
    expect(session.state.reconciliation).toBe(reconciliation)
    expect(session.state.versions.current.files).toEqual(files)
    expect(session.state.versions.current.blocked).toBe(true)
  })
  it('blocks confirmation if the database changes after the dialog opens', async () => {
    await mount(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('草稿版（saved）')
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).not.toBeNull())
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); click('部署'); await screen.findByRole('button', { name: '确认部署' })
    mocks.get.mockResolvedValue({ ...mocks.db!, title: 'External change' }); click('确认部署')
    await screen.findByText(/数据库草稿或本地编辑已变化/); expect(mocks.deploy).not.toHaveBeenCalled()
  })

  it('confirms and uses the captured saved head when editing current files', async () => {
    current = { ...current, draftHead: B }; await mount()
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.version).toBe('draft'))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('当前版（wf）'); click('scripts/run')
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'current edit' } }); click('保存到草稿')
    await screen.findByText(/文件已保存到 saved commit/)
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(B))
    expect(JSON.parse(await readBlob(putCalls()[0][1].body))).toMatchObject({ sourceVersion: 'current', sourceCommit: A, expectedSavedHead: B })
  })
  it('does not accept an unverifiable DB response or start a Git write', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Keep canvas')); await editFile('scripts/run', 'Keep file')
    mocks.fetchJson.mockResolvedValueOnce({ ok: true, saved: true, workflowId: 'wrong', packId: 'pack' })
    click('保存到草稿'); await screen.findByText(/数据库保存响应无法验证/)
    expect(state().isDirty).toBe(true); expect(screen.getByLabelText('文件内容')).toHaveValue('Keep file'); expect(putCalls()).toHaveLength(0)
  })

})
