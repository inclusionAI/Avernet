import type { ReactNode } from 'react'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter, MemoryRouter, Link, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ id: '', savedCommit: '', baseline: null as WorkflowSpec | null, casGet: vi.fn(), fetchJson: vi.fn(), deploy: vi.fn(), saveDb: vi.fn(), gitSave: vi.fn(), get: vi.fn(), completeSave: vi.fn(), history: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.baseline, databaseSpec: mocks.baseline, isLoading: false }),
  useSaveWorkflowToDb: () => ({ mutateAsync: mocks.saveDb, isPending: false }), useFacadeBindings: () => ({ data: [] }),
  useAutoHealSave: () => ({ mutateAsync: mocks.gitSave, isPending: false }), useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'user' }) }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: (url: string, init?: RequestInit) => url.includes('/snapshot-v2/state')
  ? Promise.resolve({ protocol: 'editor-deployment-state-v1', workflowId: mocks.id, packId: 'pack', canDeploy: true, savedCommit: mocks.savedCommit, activeCommit: 'a'.repeat(40) })
  : url.endsWith('/editor-draft') && init?.method === 'GET'
  ? (mocks.casGet(url, init), Promise.resolve({ workflowId: mocks.id, packId: 'pack', specJson: JSON.stringify({ id: mocks.id, title: 'Flow', version: '1', nodes: [] }), draftToken: 'a'.repeat(64) }))
  : mocks.fetchJson(url, init), api: { workflows: { getHistory: mocks.history, get: mocks.get, getDeploySnapshot: vi.fn() } } }))
vi.mock('@xyflow/react', () => ({ ReactFlowProvider: ({ children }: { children: ReactNode }) => children }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { readDatabaseWorkflowDraft } from '../../api/editorWorkflowSource'
import { useEditorStore } from '../../editor/store'
import { sessionFor } from '../../editor/packVersionSession'
import { stringify } from 'yaml'
import Editor from '../Editor'
const A = 'a'.repeat(40), B = 'b'.repeat(40)
let seq = 0
function snapshot(version: 'current' | 'draft' = 'current') { return { packPath: '', workflowId: mocks.id, packId: 'pack', canWrite: true, baseRef: (version === 'current' ? `refs/tags/deploy/${mocks.id}/#1` : `origin/wf/${mocks.id}`), baseCommit: A, draftHead: version === 'current' ? null : A, versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [{ id: mocks.id, file: 'flows/main.yaml' }] })), mode: '100644' }, { path: 'flows/main.yaml', contentBase64: btoa(stringify({ id: mocks.id, title: 'Flow', version: '1', nodes: [] })), mode: '100644' }, { path: 'scripts/test', contentBase64: 'b2xk', mode: '100644' }] } }
function Shell() { const navigate = useNavigate(); return <><Link to="/outside">离开</Link><button onClick={() => navigate(-1)}>后退</button><Routes><Route path="/editor" element={<Editor embedded initialWorkflowId={mocks.id} />} /><Route path="/outside" element={<h1>外部</h1>} /></Routes></> }
function mount() { return render(<MemoryRouter initialEntries={['/outside', '/editor']} initialIndex={1}><Shell /></MemoryRouter>) }
async function openFile() { fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); fireEvent.click(await screen.findByRole('button', { name: 'scripts/test' })) }
function enableUnifiedSave() {
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (String(url).endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    throw new Error(`Unexpected request: ${url}`)
  })
}
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
beforeEach(async () => {
  mocks.id = `pack-safe-${++seq}`; mocks.savedCommit = A; vi.clearAllMocks()
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/unified-save') && init?.method === 'GET') return new Response(null, { status: 404 })
    throw new Error(`Unexpected request: ${url}`)
  }))
  useEditorStore.getState().reset({ force: true })
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.get.mockImplementation(async () => ({ id: mocks.id, title: 'Flow', version: '1', nodes: [] }))
  mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init.body as Blob) })
      return { branch: `wf/${mocks.id}`, commit: B, requestId: JSON.parse(text).requestId, packId: 'pack', version: 2 }
    }
    return snapshot(url.includes('version=draft') ? 'draft' : 'current')
  })
  mocks.baseline = await readDatabaseWorkflowDraft(mocks.id)
  useEditorStore.getState().loadSpec(mocks.baseline!, mocks.baseline)
  mocks.get.mockClear(); mocks.fetchJson.mockClear(); mocks.casGet.mockClear()
  vi.spyOn(window, 'alert').mockImplementation(() => {}); vi.spyOn(window, 'confirm').mockImplementation(message => String(message).includes('兼容模式') || String(message).includes('统一保存不可用'))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('Editor and Pack candidate isolation', () => {
  it('saves file edits through the shared Pack path without legacy DB or Git writes', async () => {
    let saved = snapshot('draft')
    const originalFetchJson = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url: string, init?: RequestInit) => url.includes('version=draft') ? Promise.resolve(saved) : originalFetchJson(url, init))
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
      if (String(url).endsWith('/unified-save') && init?.method === 'POST') {
        const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init.body as Blob) })
        const input = JSON.parse(text)
        expect(input.expectedDraftToken).toBe('a'.repeat(64))
        expect(input.changes).toEqual([expect.objectContaining({ path: 'scripts/test', contentBase64: btoa('new yaml') })])
        const files = saved.versionFiles.map(f => f.path === 'flows/main.yaml'
          ? { ...f, contentBase64: btoa(stringify(JSON.parse(input.specJson), { sortMapEntries: true, lineWidth: 0 })) }
          : f.path === 'scripts/test' ? { ...f, contentBase64: btoa('new yaml') } : f)
        saved = { ...saved, baseCommit: B, draftHead: B, versionFiles: files }
        return new Response(JSON.stringify({ requestId: input.requestId, outcome: 'confirmed', reconciliationRequired: false,
          evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit: B, draftToken: 'a'.repeat(64) } }))
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    mount(); await openFile()
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'new yaml' } })
    fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
    await screen.findByText(new RegExp(`已保存到草稿 commit ${B}`))
    expect(mocks.casGet).toHaveBeenCalledWith(`/api/workflows/${mocks.id}/editor-draft`, expect.objectContaining({ method: 'GET' }))
    const unifiedWrite = vi.mocked(fetch).mock.calls.findIndex(([, init]) => init?.method === 'POST')
    expect(unifiedWrite).toBeGreaterThanOrEqual(0)
    expect(mocks.casGet.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(fetch).mock.invocationCallOrder[unifiedWrite])
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(screen.queryByText(/工作流保存已阻止/)).not.toBeInTheDocument()
    expect(mocks.saveDb).not.toHaveBeenCalled(); expect(mocks.gitSave).not.toHaveBeenCalled(); expect(useEditorStore.getState().isDirty).toBe(false)
  })
  it('blocks deploy with a dirty file candidate even after switching tabs', async () => {
    mount(); await openFile(); fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'dirty' } })
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    expect(screen.queryByText(/切换标签后仍保留/)).not.toBeInTheDocument()
    expect(screen.getByText(/导出仅包含已应用的画布工作流 spec/)).toHaveTextContent('不包含 Pack 版本文件（包括 YAML、脚本和资源）、未应用草稿或画布节点位置')
    fireEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(screen.getByText(/版本文件存在未保存更改/)).toBeInTheDocument(); expect(mocks.deploy).not.toHaveBeenCalled()
    expect(mocks.saveDb).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); fireEvent.click(screen.getByRole('button', { name: 'scripts/test' }))
    expect(screen.getByLabelText('文件内容')).toHaveValue('dirty')
  })
  it('blocks deploying if file changes while the draft read is in flight', async () => {
    const pending = deferred<unknown>(); mount(); await openFile()
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    mocks.fetchJson.mockReturnValueOnce(pending.promise)
    fireEvent.click(screen.getByRole('button', { name: '部署' }))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); fireEvent.click(screen.getByRole('button', { name: 'scripts/test' }))
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'late change' } })
    await act(async () => { pending.resolve(snapshot('draft')); await pending.promise })
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    expect(screen.getByText(/读取期间编辑内容或数据库草稿已变化/)).toBeInTheDocument(); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('does not deploy after an unsuccessful Pack save and retains the file', async () => {
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
      if (String(url).endsWith('/unified-save') && init?.method === 'POST') {
        const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init.body as Blob) })
        return new Response(JSON.stringify({ requestId: JSON.parse(text).requestId, outcome: 'rejected', reconciliationRequired: false,
          evidence: { db: 'not_started', git: 'not_started' } }), { status: 409 })
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    mount(); await openFile(); fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'unsaved' } })
    fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
    await waitFor(() => expect(sessionFor(mocks.id).state.postSaveVerificationError).toContain('统一保存结果为 rejected'))
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); fireEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(mocks.deploy).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    fireEvent.click(screen.getByRole('button', { name: 'scripts/test' })); expect(screen.getByLabelText('文件内容')).toHaveValue('unsaved')
  })
  it('blocks save, deploy, version/workspace switches, links and programmatic back during upload', async () => {
    let reader!: FileReader
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (this: FileReader) { reader = this })
    mount(); await openFile()
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['new'], 'new')] } })
    expect(screen.queryByRole('button', { name: '保存到草稿' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
    expect(await screen.findByText(/保存、上传或部署状态未完成/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /草稿版/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /当前版/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); expect(screen.getByRole('tab', { name: '脚本文件' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('button', { name: '部署' })); expect(mocks.deploy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: '离开' })); fireEvent.click(screen.getByRole('button', { name: '后退' }))
    expect(screen.queryByRole('heading', { name: '外部' })).not.toBeInTheDocument(); expect(window.alert).toHaveBeenCalled()
    const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true)
    Object.defineProperty(reader, 'result', { value: new Uint8Array([110, 101, 119]).buffer }); act(() => reader.dispatchEvent(new ProgressEvent('load')))
    expect(screen.getByLabelText('文件内容')).toHaveValue('new')
    expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
  })
  it.each([404, 403, 503])('blocks the script-page toolbar save after draft read error %i', async status => {
    enableUnifiedSave()
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url: string, init?: RequestInit) => url.includes('version=draft')
      ? Promise.reject(Object.assign(new Error('unavailable'), { status })) : original(url, init))
    mount()
    act(() => useEditorStore.getState().updateSpecField('title', 'unsaved canvas'))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    await waitFor(() => expect(screen.queryByText('正在读取版本文件…')).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText(/Git 版本文件尚未成功读取/)).toHaveTextContent('本地草稿仍保留')
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    expect(mocks.saveDb).not.toHaveBeenCalled()
    expect(useEditorStore.getState().spec?.title).toBe('unsaved canvas')
    expect(useEditorStore.getState().isDirty).toBe(true)
  })
  it('blocks toolbar save when a cached draft refresh fails after the canvas becomes dirty', async () => {
    enableUnifiedSave()
    const pending = deferred<unknown>()
    mount(); await openFile()
    mocks.fetchJson.mockReturnValueOnce(pending.promise)
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await screen.findByText('正在读取版本文件…')
    act(() => useEditorStore.getState().updateSpecField('title', 'local canvas during refresh'))
    await act(async () => { pending.reject(Object.assign(new Error('unavailable'), { status: 503 })); await pending.promise.catch(() => {}) })
    expect(screen.getByText(/版本服务暂不可用/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText(/Git 版本文件尚未成功读取/)).toHaveTextContent('本地草稿仍保留')
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toHaveLength(0)
    expect(useEditorStore.getState().spec?.title).toBe('local canvas during refresh')
    expect(useEditorStore.getState().isDirty).toBe(true)
  })
  it('blocks the script-page toolbar save while the draft read is pending', async () => {
    enableUnifiedSave()
    const pending = deferred<unknown>(), original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url: string, init?: RequestInit) => url.includes('version=draft') ? pending.promise : original(url, init))
    mount()
    act(() => useEditorStore.getState().updateSpecField('title', 'unsaved while loading'))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    await screen.findByText('正在读取版本文件…')
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText(/Git 版本文件尚未成功读取/)).toHaveTextContent('本地草稿仍保留')
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    expect(useEditorStore.getState().spec?.title).toBe('unsaved while loading')
    expect(useEditorStore.getState().isDirty).toBe(true)
    await act(async () => { pending.resolve(snapshot('draft')); await pending.promise })
  })
  it('warns and allows cancellation on route leave while retaining dirty files', async () => {
    mount(); await openFile(); fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'draft' } })
    fireEvent.click(screen.getByRole('link', { name: '离开' }))
    expect(window.confirm).toHaveBeenCalled(); expect(screen.queryByRole('heading', { name: '外部' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('文件内容')).toHaveValue('draft')
  })
  it('blocks a native browser POP during upload before the router can unmount the editor', async () => {
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(() => {})
    const go = vi.spyOn(window.history, 'go').mockImplementation(() => {})
    window.history.replaceState({ idx: 4 }, '', '/editor')
    render(<BrowserRouter><Shell /></BrowserRouter>); await openFile()
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['new'], 'new')] } })
    act(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { idx: 3 } })))
    expect(go).toHaveBeenCalledWith(1)
    expect(screen.getByRole('tab', { name: '脚本文件' })).toHaveAttribute('aria-selected', 'true')
    expect(window.alert).toHaveBeenCalled()
    act(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { idx: 4 } })))
    window.history.replaceState(null, '', '/')
  })

  it('rejects a remotely advanced candidate until its baseline has been read', async () => {
    mount(); await openFile()
    const session = sessionFor(mocks.id)
    expect(session.state.versions.draft.snapshot?.baseCommit).toBe(A)
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    // The host authorizes the newly advanced 草稿(wf) head, while this session
    // still holds the older baseline. The editor must detect that mismatch.
    mocks.savedCommit = B
    mocks.fetchJson.mockResolvedValueOnce({ ...snapshot('draft'), baseCommit: B, draftHead: B })
    fireEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(await screen.findByText(/草稿文件快照与已读取基线不一致/)).toBeInTheDocument()
    expect(session.state.versions.draft.snapshot?.baseCommit).toBe(A)
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  })

  it('blocks confirmation when Pack files changed after the deployment dialog opened', async () => {
    mount(); await waitFor(() => expect(screen.queryByText('正在读取完整 Git 基线…')).not.toBeInTheDocument()); fireEvent.click(screen.getByRole('button', { name: '部署' }))
    await screen.findByRole('button', { name: '确认部署' })
    fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    fireEvent.click(await screen.findByRole('button', { name: 'scripts/test' }))
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'changed after confirm' } })
    fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
    expect(screen.getByText(/确认期间编辑内容已变化/)).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  })

})
