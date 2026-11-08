import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parse, stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ db: null as WorkflowSpec | null, baseline: null as WorkflowSpec | null, fetchJson: vi.fn(), deploy: vi.fn(), history: vi.fn(), saveDb: vi.fn(), gitSave: vi.fn(), get: vi.fn(), deployHook: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.db, databaseSpec: mocks.baseline, isLoading: false }),
  useAutoHealDeploy: (options: unknown) => { mocks.deployHook(options); return { mutateAsync: mocks.deploy, isPending: false } },
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
import { readDatabaseWorkflowDraft } from '../../api/editorWorkflowSource'
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { sessionFor, publish, updateVersion, type Snapshot } from '../../editor/packVersionSession'

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
  render(<Editor embedded initialWorkflowId={mocks.db!.id} />)
  await waitFor(() => expect(session.state.versions[version].loading).toBe(false))
}
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))
const confirmDeploy = () => { fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ })); click('确认部署') }
async function editFile(path: string, text: string) {
  fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); fireEvent.click(await screen.findByRole('button', { name: path, exact: true }))
  fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: text } })
}
function putCalls() { return mocks.fetchJson.mock.calls.filter(([url, init]) => init?.method === 'PUT' && !url.endsWith('/editor-draft')) }
beforeEach(async () => {
  vi.resetAllMocks(); state().reset({ force: true })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  mocks.db = { id: `editor-save-${++sequence}`, title: 'Original', version: '1', nodes: [] }
  current = snapshot('current'); saved = snapshot('draft'); unifiedAvailable = false; draftToken = 'a'.repeat(64)
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.get.mockImplementation(async () => mocks.db)
  mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: input.workflowId, packId: input.packId, deployNumber: 2, version: 3, tagName: 'deploy/test/2', snapshotProtocol: input.snapshotProtocol, candidateCommit: input.candidateCommit, expectedActiveCommit: input.expectedActiveCommit }))
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
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

describe('Editor DB workflow and 草稿(wf) Pack files', () => {
  it('requires explicit absence proof and reviews every saved file on first deploy', async () => {
    await mount('draft')
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes('version=current')) throw new Error('404')
      if (url.includes('/snapshot-v2/state')) return { protocol: 'editor-deployment-state-v1', workflowId: mocks.db!.id, packId: 'pack', canDeploy: true, savedCommit: B, activeCommit: null }
      return original(url, init)
    })
    click('部署')
    await screen.findByRole('button', { name: '确认部署' })
    expect(screen.getByText(/不存在（首次部署）/)).toBeVisible()
    for (const entry of saved.versionFiles) expect(screen.getByText(`新增：${entry.path}`)).toBeVisible()
    confirmDeploy(); await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ expectedActiveCommit: '', candidateCommit: B }))
  })

  it.each(['unavailable', 'wrong workflow', 'active tag', 'stale saved'] as const)('does not treat %s as absence proof', async scenario => {
    await mount('draft')
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes('version=current')) throw new Error('403')
      if (url.includes('/snapshot-v2/state')) {
        if (scenario === 'unavailable') throw new Error('403')
        return { protocol: 'editor-deployment-state-v1', workflowId: scenario === 'wrong workflow' ? 'other' : mocks.db!.id, packId: 'pack', canDeploy: true,
          savedCommit: scenario === 'stale saved' ? A : B, activeCommit: scenario === 'active tag' ? A : null }
      }
      return original(url, init)
    })
    click('部署'); await screen.findByText(/无法确认数据库草稿和草稿文件/)
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  })

  it('allows authorized deployment when legacy file writes are unavailable', async () => {
    saved.canWrite = false
    await mount('draft')
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); confirmDeploy()
    await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledTimes(1)
    expect(putCalls()).toHaveLength(0)
  })

  it.each(['absent', 'denied', 'wrong pack', 'stale active'] as const)('rejects %s deploy capability even when file writes are enabled', async scenario => {
    await mount('draft')
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes('/snapshot-v2/state')) return { protocol: 'editor-deployment-state-v1', workflowId: mocks.db!.id,
        packId: scenario === 'wrong pack' ? 'other' : 'pack', ...(scenario === 'absent' ? {} : { canDeploy: scenario !== 'denied' }),
        savedCommit: B, activeCommit: scenario === 'stale active' ? B : A }
      return original(url, init)
    })
    click('部署'); await screen.findByText(/无法确认数据库草稿和草稿文件/)
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled(); expect(putCalls()).toHaveLength(0)
  })

  it('uses the same save and deploy labels on both editor tabs', async () => {
    await mount()
    expect(mocks.deployHook).toHaveBeenCalledWith({ editorGuard: true })
    expect(screen.getByRole('button', { name: '保存', exact: true })).toBeTruthy()
    expect(screen.getByRole('button', { name: '部署', exact: true })).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    expect(screen.getByRole('button', { name: '保存', exact: true })).toBeTruthy()
    expect(screen.getByRole('button', { name: '部署', exact: true })).toBeTruthy()
  })
  it('shows only the shared top-level save and deploy actions on the embedded script tab', async () => {
    await mount()
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    await screen.findByRole('button', { name: 'scripts/run' })
    expect(screen.getAllByRole('button', { name: '保存', exact: true })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: '部署', exact: true })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: '保存到草稿' })).not.toBeInTheDocument()
  })
  it('does not write either store for an unchanged saved draft', async () => {
    unifiedAvailable = true; await mount('draft')
    click('保存'); await screen.findByText('没有需要保存的更改。')
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toEqual([])
    expect(putCalls()).toEqual([]); expect(mocks.db!.title).toBe('Original')
  })
  it('refuses a DB-only save when the unified capability is unavailable', async () => {
    await mount(); act(() => state().updateSpecField('title', 'DB only'))
    click('保存'); await screen.findByText(/统一保存不可用，无法同步草稿\(wf\)工作流 YAML/)
    expect(mocks.db!.title).toBe('Original'); expect(state().spec!.title).toBe('DB only'); expect(state().isDirty).toBe(true)
    expect(putCalls()).toEqual([])
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toEqual([])
  })
  it('saves a file edit together with the verified database document', async () => {
    unifiedAvailable = true; await mount('draft'); await editFile('scripts/run', 'file only')
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    const call = mocks.fetchJson.mock.calls.find(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')!
    const input = JSON.parse(await readBlob(call[1].body))
    expect(input).toMatchObject({ sourceVersion: 'draft', sourceCommit: B, expectedSavedHead: B, expectedDraftToken: 'a'.repeat(64) })
    expect(JSON.parse(input.specJson)).toEqual(mocks.db)
    expect(input.changes).toEqual([expect.objectContaining({ path: 'scripts/run', contentBase64: btoa('file only'), mode: '100755' })])
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')?.contentBase64).toBe('AP8C')
    expect(putCalls()).toEqual([])
  })
  it.each(['工作流编排', '脚本文件'])('saves both canvas and file changes from the %s tab', async tab => {
    unifiedAvailable = true; await mount('draft')
    act(() => state().updateSpecField('title', 'Unified canvas'))
    await editFile('scripts/run', 'unified script')
    fireEvent.click(screen.getByRole('tab', { name: tab }))
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(mocks.db!.title).toBe('Unified canvas')
    expect(saved.versionFiles.find(item => item.path === 'scripts/run')?.contentBase64).toBe(btoa('unified script'))
    expect(atob(saved.versionFiles.find(item => item.path === 'flows/custom.yaml')!.contentBase64)).toContain('Unified canvas')
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')?.contentBase64).toBe('AP8C')
    expect(state().isDirty).toBe(false); expect(putCalls()).toEqual([])
    expect(mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(1)
  })
  it('can select an available Git version after a read failure without discarding canvas edits', async () => {
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => url.includes('version=draft')
      ? Promise.reject({ status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }) : original(url, init))
    await mount('draft')
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.readFailure).toBeTruthy())
    act(() => state().updateSpecField('title', 'Keep while recovering'))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    click('当前版（生效 tag）')
    await screen.findByRole('button', { name: 'scripts/run' })
    click('保存')
    await screen.findByText(/统一保存不可用，无法同步草稿\(wf\)工作流 YAML；数据库和 Git 均未写入/)
    expect(mocks.db!.title).toBe('Original'); expect(state().spec!.title).toBe('Keep while recovering'); expect(state().isDirty).toBe(true)
    expect(sessionFor(mocks.db!.id).state.versions.current.snapshot?.baseCommit).toBe(A)
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toEqual([])
  })
  it.each(['工作流编排', '脚本文件'])('blocks %s save when the selected Git snapshot read failed', async tab => {
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
      return new Response(JSON.stringify(await mocks.fetchJson(url, init)))
    })
    await mount()
    const session = sessionFor(mocks.db!.id)
    act(() => { state().updateSpecField('title', 'Keep local'); updateVersion(session, 'current', { readFailure: '读取失败', error: '读取失败' }) })
    fireEvent.click(screen.getByRole('tab', { name: tab }))
    click('保存')
    await screen.findByText(/Git 版本文件尚未成功读取/)
    expect(mocks.db!.title).toBe('Original')
    expect(state().isDirty).toBe(true)
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0)
  })
  it.each(['工作流编排', '脚本文件'])('blocks %s deployment while a script edit is unsaved', async tab => {
    await mount()
    await editFile('scripts/run', 'not saved')
    fireEvent.click(screen.getByRole('tab', { name: tab }))
    click('部署')
    expect(await screen.findByText(/版本文件存在未保存更改/)).toBeVisible()
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  })
  it.each([
    ['工作流编排', 'current'], ['工作流编排', 'draft'],
    ['脚本文件', 'current'], ['脚本文件', 'draft'],
  ] as const)('blocks deployment from %s when %s has unsaved files', async (tab, dirtyVersion) => {
    await mount()
    const session = sessionFor(mocks.db!.id)
    const baseline = dirtyVersion === 'current' ? current : saved
    act(() => updateVersion(session, dirtyVersion, { snapshot: baseline,
      files: baseline.versionFiles.map(item => item.path === 'scripts/run' ? { ...item, contentBase64: btoa('pending') } : item) }))
    fireEvent.click(screen.getByRole('tab', { name: tab }))
    click('部署')
    expect(await screen.findByText(/版本文件存在未保存更改/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('does not overwrite orphaned local files when opening the scripts tab', async () => {
    await mount()
    const session = sessionFor(mocks.db!.id)
    act(() => updateVersion(session, 'current', { snapshot: null, files: [file('scripts/orphan', 'local')], error: null }))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    await act(async () => {})
    expect(session.state.versions.current.files).toEqual([file('scripts/orphan', 'local')])
    expect(session.state.versions.current.snapshot).toBeNull()
  })
  it('refuses deployment when the other version contains orphaned local files', async () => {
    await mount()
    const session = sessionFor(mocks.db!.id)
    act(() => updateVersion(session, 'draft', { snapshot: null, files: [file('scripts/orphan', 'local')] }))
    saved = { ...current, baseRef: `origin/wf/${mocks.db!.id}`, draftHead: A }
    click('部署')
    expect(await screen.findByText(/版本文件存在未保存更改/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('shows both script versions against the active tag and requires explicit review', async () => {
    saved = { ...saved, versionFiles: saved.versionFiles.map(item => item.path === 'scripts/run' ? file(item.path, 'new saved script', '100755') : item) }
    await mount('draft'); click('部署')
    const button = await screen.findByRole('button', { name: '确认部署' })
    expect(button).toBeDisabled()
    expect(screen.getByText('更新：scripts/run')).toBeVisible()
    expect(screen.getByText(/^original/)).toBeVisible()
    expect(screen.getByText(/^new saved script/)).toBeVisible()
    expect(screen.getByText(new RegExp(`已核对生产 commit：${A}`))).toBeVisible()
    fireEvent.click(button); expect(mocks.deploy).not.toHaveBeenCalled()
    confirmDeploy(); await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ expectedActiveCommit: A, confirmedFileDiff: true, candidateCommit: B }))
  })
  it('fails closed if the active tag cannot be read', async () => {
    await mount('draft')
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => url.includes('version=current')
      ? Promise.reject({ status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }) : original(url, init))
    click('部署'); expect(await screen.findByText(/无法确认数据库草稿和草稿文件/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  })
  it('confirms the complete saved tree and sends the versioned snapshot contract', async () => {
    await mount('draft'); click('部署')
    expect(await screen.findByText(/即将部署以下已保存的草稿\(wf\)完整快照/)).toBeVisible()
    expect(screen.getByText(/部署不会用数据库改写 YAML/)).toBeVisible()
    confirmDeploy(); await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ snapshotProtocol: 'saved-tree-v2', candidateCommit: B, expectedActiveCommit: A, confirmedFileDiff: true, expectedDraftToken: 'a'.repeat(64) }))
  })
  it('does not fall back to production if the wf draft snapshot is missing', async () => {
    await mount('current')
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => url.includes('version=draft')
      ? Promise.reject({ status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }) : original(url, init))
    click('部署'); expect(await screen.findByText(/无法确认数据库草稿和草稿文件/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('treats an acknowledgement without snapshot confirmation as unknown', async () => {
    mocks.deploy.mockImplementation(async input => ({ ok: true, deployed: true, workflowId: input.workflowId,
      packId: input.packId, deployNumber: 2, version: 3, tagName: 'deploy/test/2' }))
    await mount('draft'); click('部署'); await screen.findByRole('button', { name: '确认部署' }); confirmDeploy()
    expect(await screen.findByText(/部署失败或结果未知/)).toBeVisible()
  })
  it('uses the raw CAS draft for deployment checks without stripping user-authored content', async () => {
    const doc = { ...mocks.db!, content: stringify(mocks.db!) }
    mocks.db = doc; state().loadSpec(doc)
    current = snapshot('current', doc); saved = snapshot('draft', doc)
    await mount('draft')
    click('部署')
    expect(await screen.findByRole('button', { name: '确认部署' })).toBeVisible()
    confirmDeploy()
    expect(await screen.findByText(/部署成功/)).toBeVisible()
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: doc.id, candidateCommit: B, expectedDraftToken: 'a'.repeat(64) }))
  })
  it('saves canvas and file edits as one candidate, then deploys the verified 保存版本 commit', async () => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'Canvas edit'))
    const binary = saved.versionFiles.find(item => item.path === 'assets/raw')
    await editFile('scripts/run', 'new script'); click('保存')
    await screen.findByText(new RegExp(`数据库和草稿\\(wf\\)分支已确认保存.*${C}`))
    const calls = mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')
    expect(calls).toHaveLength(1)
    const input = JSON.parse(await readBlob(calls[0][1].body))
    expect(input).toMatchObject({ sourceVersion: 'draft', sourceCommit: B, expectedSavedHead: B, expectedDraftToken: 'a'.repeat(64), requestId: expect.stringMatching(/^[\da-f-]{36}$/) })
    expect(JSON.parse(input.specJson).title).toBe('Canvas edit')
    expect(input.changes).toEqual([expect.objectContaining({ path: 'scripts/run', mode: '100755', contentBase64: btoa('new script') })])
    expect(saved.versionFiles.find(item => item.path === 'assets/raw')).toEqual(binary)
    expect(atob(saved.versionFiles.find(item => item.path === 'flows/custom.yaml')!.contentBase64)).toContain('Canvas edit')
    expect(state().isDirty).toBe(false); expect(putCalls()).toEqual([])
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); confirmDeploy()
    await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: mocks.db!.id, packId: 'pack', candidateCommit: C, expectedDraftToken: 'b'.repeat(64) }))
  })
  it.each(['identity', 'path'] as const)('rejects a Pack %s change without writing either store', async change => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'Keep canvas'))
    const packId = change === 'identity' ? 'renamed-pack' : 'pack'
    const path = change === 'path' ? 'flows/other.yaml' : 'flows/custom.yaml'
    if (change === 'path') {
      const session = sessionFor(mocks.db!.id)
      act(() => updateVersion(session, 'draft', { files: session.state.versions.draft.files.map(item =>
        item.path === 'flows/custom.yaml' ? { ...item, path } : item) }))
    }
    const manifest = stringify({ id: packId, workflows: [{ id: mocks.db!.id, file: path }] })
    await editFile('workflow.pack.yaml', manifest); click('保存')
    expect(await screen.findByText(/统一保存暂不支持修改 Pack manifest、工作流路径或 YAML 文件模式/)).toBeVisible()
    expect(state().isDirty).toBe(true); expect(state().spec!.title).toBe('Keep canvas')
    expect(screen.getByLabelText('文件内容')).toHaveValue(manifest)
    expect(mocks.db!.title).toBe('Original'); expect(saved.baseCommit).toBe(B)
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toEqual([])
    expect(putCalls()).toEqual([]); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('retains a pre-existing cross-store rename lock and does not retry it', async () => {
    await mount()
    await editFile('workflow.pack.yaml', stringify({ id: 'renamed-pack', workflows: [{ id: mocks.db!.id, file: 'flows/custom.yaml' }] }))
    const session = sessionFor(mocks.db!.id)
    const message = 'Pack 重命名 pack → renamed-pack 未完整完成：数据库已确认 Pack ID renamed-pack；请先人工核对，不能自动重试。'
    act(() => {
      publish(session, { ...session.state, uncertainRename: message })
      for (const version of ['current', 'draft'] as const) updateVersion(session, version, { blocked: true, error: message })
    })
    expect(session.state.reconciliation).toBeNull()
    expect(session.state.versions.current.blocked).toBe(true); expect(session.state.versions.draft.blocked).toBe(true)
    expect(screen.getByRole('alert')).toHaveTextContent('数据库已确认 Pack ID renamed-pack')
    expect((screen.getByLabelText('文件内容') as HTMLTextAreaElement).value).toContain('renamed-pack')
    await act(async () => { click('保存'); click('部署') })
    expect(putCalls()).toHaveLength(0); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('retains edits and blocks DB and Git before an unsupported Pack rename', async () => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'Keep canvas'))
    await editFile('workflow.pack.yaml', stringify({ id: 'renamed-pack', workflows: [{ id: mocks.db!.id, file: 'flows/custom.yaml' }] }))
    click('保存'); expect(await screen.findByText(/统一保存暂不支持修改 Pack manifest/)).toBeVisible()
    expect(state().isDirty).toBe(true); expect(state().spec!.title).toBe('Keep canvas')
    expect((screen.getByLabelText('文件内容') as HTMLTextAreaElement).value).toContain('renamed-pack')
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0)
    expect(sessionFor(mocks.db!.id).state.uncertainRename).toBeNull()
  })
  it('saves the DB editor candidate and preserves layout when Git baseline omits coordinates', async () => {
    unifiedAvailable = true
    mocks.db!.nodes = [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' }, _x: 501, _y: 202 }]
    mocks.baseline = await readDatabaseWorkflowDraft(mocks.db!.id)
    state().reset({ force: true }); state().loadSpec(mocks.db!, mocks.baseline)
    saved = snapshot('draft', { ...mocks.db!, nodes: mocks.db!.nodes.map(({ _x, _y, ...node }) => node) })
    const otherFiles = structuredClone(saved.versionFiles.filter(item => item.path !== 'flows/custom.yaml'))
    await mount('draft')
    expect(state().positions.done).toEqual({ x: 501, y: 202 })
    act(() => state().updateSpecField('title', 'Updated'))
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(mocks.db!.title).toBe('Updated')
    expect(mocks.db!.nodes[0]).toMatchObject({ id: 'done', _x: 501, _y: 202 })
    expect(state().spec!.title).toBe('Updated'); expect(state().isDirty).toBe(false)
    expect(state().positions.done).toEqual({ x: 501, y: 202 })
    const posts = mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')
    expect(posts).toHaveLength(1)
    const input = JSON.parse(await readBlob(posts[0][1].body))
    expect(input).toMatchObject({ sourceVersion: 'draft', sourceCommit: B, expectedSavedHead: B, expectedDraftToken: 'a'.repeat(64) })
    expect(JSON.parse(input.specJson)).toEqual(mocks.db)
    expect(parse(atob(saved.versionFiles.find(item => item.path === 'flows/custom.yaml')!.contentBase64))).toEqual(mocks.db)
    expect(saved.baseCommit).toBe(C)
    expect(saved.versionFiles.filter(item => item.path !== 'flows/custom.yaml')).toEqual(otherFiles)
    expect(putCalls()).toHaveLength(0)
  })
  it('saves the edited DB candidate instead of replacing it with a different Git workflow', async () => {
    unifiedAvailable = true
    mocks.db!.nodes = [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' }, _x: 31, _y: 62 }]
    mocks.baseline = await readDatabaseWorkflowDraft(mocks.db!.id)
    state().reset({ force: true }); state().loadSpec(mocks.db!, mocks.baseline)
    saved = snapshot('draft', { ...mocks.db!, title: 'Git version' })
    const otherFiles = structuredClone(saved.versionFiles.filter(item => item.path !== 'flows/custom.yaml'))
    await mount('draft')
    expect(state().spec!.title).toBe('Original')
    act(() => { state().updateSpecField('title', 'DB edit'); state().moveNode('done', 123, 456) })
    click('保存'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(mocks.db!.title).toBe('DB edit')
    expect(mocks.db!.nodes[0]).toMatchObject({ id: 'done', _x: 31, _y: 62 })
    expect(state().spec!.title).toBe('DB edit'); expect(state().isDirty).toBe(false)
    expect(state().positions.done).toEqual({ x: 123, y: 456 })
    const posts = mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')
    expect(posts).toHaveLength(1)
    const input = JSON.parse(await readBlob(posts[0][1].body))
    expect(input).toMatchObject({ sourceVersion: 'draft', sourceCommit: B, expectedSavedHead: B, expectedDraftToken: 'a'.repeat(64) })
    expect(JSON.parse(input.specJson)).toEqual(mocks.db)
    expect(parse(atob(saved.versionFiles.find(item => item.path === 'flows/custom.yaml')!.contentBase64))).toEqual(mocks.db)
    expect(saved.baseCommit).toBe(C)
    expect(saved.versionFiles.filter(item => item.path !== 'flows/custom.yaml')).toEqual(otherFiles)
    expect(putCalls()).toHaveLength(0)
  })
  it('rejects divergent canvas and workflow-file edits without losing either', async () => {
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
      return new Response(JSON.stringify(await mocks.fetchJson(url, init)))
    })
    await mount(); act(() => state().updateSpecField('title', 'Canvas'))
    await editFile('flows/custom.yaml', stringify({ ...mocks.db!, title: 'Files' })); click('保存')
    await screen.findByText(/画布和文件同时修改了工作流 YAML/)
    expect(state().spec!.title).toBe('Canvas'); expect((screen.getByLabelText('文件内容') as HTMLTextAreaElement).value).toContain('Files')
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0)
  })
  it('does not write either store for divergent canvas and YAML edits in compatibility mode', async () => {
    await mount(); act(() => state().updateSpecField('title', 'Canvas'))
    const yaml = stringify({ ...mocks.db!, title: 'Files' })
    await editFile('flows/custom.yaml', yaml); click('保存')
    await screen.findByText(/统一保存不可用，无法同步草稿\(wf\)工作流 YAML；数据库和 Git 均未写入/)
    expect(state().spec!.title).toBe('Canvas')
    expect(screen.getByLabelText('文件内容')).toHaveValue(yaml)
    expect(mocks.db!.title).toBe('Original')
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT' || init?.method === 'POST')).toHaveLength(0)
  })
  it('retains a file-only workflow YAML edit when the legacy protocol cannot save it', async () => {
    await mount(); await editFile('flows/custom.yaml', stringify({ ...mocks.db!, title: 'File edit' }))
    click('保存'); await screen.findByText(/统一保存不可用，无法同步草稿\(wf\)工作流 YAML；数据库和 Git 均未写入/)
    expect(state().spec!.title).toBe('Original'); expect(mocks.db!.title).toBe('Original')
    expect(screen.getByLabelText('文件内容')).toHaveValue(stringify({ ...mocks.db!, title: 'File edit' }))
    expect(putCalls()).toHaveLength(0)
    expect(sessionFor(mocks.db!.id).state.versions.current.files).not.toEqual(current.versionFiles)
    click('部署'); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it.each([
    ['Git conflict', 409, { code: 'VERSION_CONFLICT' }],
    ['origin refusal', 403, { code: 'ORIGIN_NOT_ALLOWED' }],
    ['unknown failure', 500, { code: 'REQUEST_FAILED' }],
  ] as const)('quarantines a unified %s response, preserving both drafts and refusing retry', async (_name, status, response) => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'Keep canvas'))
    await editFile('scripts/run', 'Keep local')
    const originalFetch = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/unified-save') && init?.method === 'POST'
      ? Promise.resolve(new Response(JSON.stringify(response), { status })) : originalFetch(url, init))
    click('保存'); await waitFor(() => expect(screen.getByText(new RegExp(`统一保存未能完整确认.*HTTP ${status}`))).toBeVisible())
    const version = sessionFor(mocks.db!.id).state.versions.draft
    expect(state().spec!.title).toBe('Keep canvas'); expect(state().isDirty).toBe(true)
    expect(mocks.db!.title).toBe('Original'); expect(version.snapshot?.baseCommit).toBe(B)
    expect(version.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Keep local'))
    expect(putCalls()).toHaveLength(0)
    expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(1)
    await waitFor(() => expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled())
    click('保存'); await screen.findByText(/数据库写入结果未终结，需人工核对/)
    click('部署'); expect(mocks.deploy).not.toHaveBeenCalled()
    expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(1)
  })
  it('keeps a partial unified response locked and reports its outcome rather than claiming DB or Git success', async () => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'Keep canvas'))
    await editFile('scripts/run', 'Keep local')
    const originalFetch = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).endsWith('/unified-save') && init?.method === 'POST') {
        const input = JSON.parse(await readBlob(init.body as Blob))
        return new Response(JSON.stringify({ requestId: input.requestId, outcome: 'partial', reconciliationRequired: true,
          evidence: { db: 'confirmed', git: 'unknown' } }), { status: 200 })
      }
      return originalFetch(url, init)
    })
    click('保存'); await waitFor(() => expect(screen.getByText(/统一保存结果为 partial；需要人工核对数据库与 Git/)).toBeVisible())
    expect(state().spec!.title).toBe('Keep canvas'); expect(state().isDirty).toBe(true)
    expect(screen.getByLabelText('文件内容')).toHaveValue('Keep local')
    expect(sessionFor(mocks.db!.id).state.postSaveVerificationError).toBeTruthy()
    expect(screen.queryByText(/数据库和草稿\(wf\)分支已确认保存/)).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled())
    click('保存'); await screen.findByText(/数据库写入结果未终结，需人工核对/)
    expect(putCalls()).toEqual([]); expect(mocks.deploy).not.toHaveBeenCalled()
    expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(1)
  })
  it('does not hide a different read error after an unknown unified result', async () => {
    unifiedAvailable = true; await mount('draft'); await editFile('scripts/run', 'Keep local')
    const originalFetch = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/unified-save') && init?.method === 'POST'
      ? Promise.reject(new Error('connection lost')) : originalFetch(url, init))
    click('保存'); await screen.findByText(/统一保存未能完整确认/)
    const session = sessionFor(mocks.db!.id)
    act(() => updateVersion(session, 'draft', { error: '另一次版本读取失败' }))
    expect(screen.getByText('另一次版本读取失败')).toBeVisible()
    expect(screen.getByText(/统一保存未能完整确认/)).toBeVisible()
    expect(screen.getByLabelText('文件内容')).toHaveValue('Keep local'); expect(putCalls()).toEqual([])
  })
  it('retains both edits and makes no Git write when DB save fails', async () => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'Keep canvas')); await editFile('scripts/run', 'Keep file')
    mocks.fetchJson.mockRejectedValueOnce(new Error('DB offline')); click('保存'); await screen.findByText(/无法核对数据库草稿/)
    expect(state().isDirty).toBe(true); expect(screen.getByLabelText('文件内容')).toHaveValue('Keep file'); expect(putCalls()).toHaveLength(0)
  })
  it('does not infer unified-save permission from a denied legacy canWrite flag', async () => {
    current.canWrite = false; await mount(); act(() => state().updateSpecField('title', 'DB only'))
    await editFile('scripts/run', 'local'); click('保存')
    await screen.findByText(/统一保存不可用，无法同步草稿\(wf\)工作流 YAML；数据库和 Git 均未写入/)
    expect(sessionFor(mocks.db!.id).state.versions.current.snapshot?.canWrite).toBe(false)
    expect(mocks.db!.title).toBe('Original'); expect(state().spec!.title).toBe('DB only'); expect(state().isDirty).toBe(true)
    expect(screen.getByLabelText('文件内容')).toHaveValue('local'); expect(putCalls()).toHaveLength(0)
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toEqual([])
  })
  it('keeps pending drafts and Ctrl-S cannot bypass the apply boundary', async () => {
    await mount(); act(() => state().setPendingEdit('yaml', 'unapplied'))
    fireEvent.keyDown(window, { key: 's', ctrlKey: true }); await screen.findByText(/存在未应用的配置草稿/)
    expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0); expect(state().pendingEdits).toEqual({ yaml: 'unapplied' })
    click('部署'); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('does not mark edits made during unified save clean', async () => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'First'))
    const pending = deferred<void>(), original = mocks.fetchJson.getMockImplementation()!
    let submitted: WorkflowSpec | undefined
    mocks.fetchJson.mockImplementation(async (url, init) => {
      if (url.endsWith('/unified-save') && init?.method === 'POST') {
        submitted = JSON.parse(JSON.parse(await readBlob(init.body as Blob)).specJson)
        const receipt = await original(url, init)
        await pending.promise; return receipt
      }
      return original(url, init)
    })
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('First'))
    act(() => state().updateSpecField('title', 'Later'))
    await act(async () => { pending.resolve(); await pending.promise })
    await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
    expect(submitted?.title).toBe('First'); expect(state()._databaseSpec?.title).toBe('First')
    expect(state().spec!.title).toBe('Later'); expect(state().isDirty).toBe(true)
    expect(sessionFor(mocks.db!.id).state.postSaveVerificationError).toBeFalsy(); expect(putCalls()).toEqual([])
  })
  it('does not apply a late unified save to a replaced canvas lifetime', async () => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'First'))
    const pending = deferred<void>(), original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation(async (url, init) => {
      if (url.endsWith('/unified-save') && init?.method === 'POST') {
        const receipt = await original(url, init); await pending.promise; return receipt
      }
      return original(url, init)
    })
    click('保存'); await waitFor(() => expect(mocks.db?.title).toBe('First'))
    act(() => { state().reset({ force: true }); state().loadSpec({ ...mocks.db!, title: 'Different lifetime' }, mocks.baseline) })
    await act(async () => { pending.resolve(); await pending.promise })
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.saving).toBe(false))
    expect(state().spec!.title).toBe('Different lifetime'); expect(state()._databaseSpec?.title).toBe('Original')
    expect(sessionFor(mocks.db!.id).state.postSaveVerificationError).toBeTruthy()
    expect(putCalls()).toEqual([])
  })
  it('rejects deployment confirmation if canvas edits happen after opening it', async () => {
    await mount(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('草稿版（wf）')
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).not.toBeNull())
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); click('部署'); await screen.findByRole('button', { name: '确认部署' })
    act(() => state().updateSpecField('title', 'Later')); confirmDeploy()
    expect(screen.getByText(/确认期间编辑内容已变化/)).toBeInTheDocument(); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('does not replace a dirty session baseline when saved moves during unmount', async () => {
    unifiedAvailable = true
    const session = sessionFor(mocks.db!.id); publish(session, { ...session.state, version: 'draft' })
    const view = render(<Editor embedded initialWorkflowId={mocks.db!.id} />)
    await waitFor(() => expect(session.state.versions.draft.snapshot?.baseCommit).toBe(B))
    await editFile('scripts/run', 'Persist local'); view.unmount()
    saved = { ...saved, baseCommit: C, draftHead: C }
    const originalFetch = vi.mocked(fetch).getMockImplementation()!
    let captured: Record<string, unknown> | undefined
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).endsWith('/unified-save') && init?.method === 'POST') {
        captured = JSON.parse(await readBlob(init.body as Blob))
        return new Response(JSON.stringify({ code: 'VERSION_CONFLICT' }), { status: 409 })
      }
      return originalFetch(url, init)
    })
    render(<Editor embedded initialWorkflowId={mocks.db!.id} />); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('保存')
    await screen.findByText(/统一保存未能完整确认.*HTTP 409/)
    expect(captured).toMatchObject({ sourceCommit: B, expectedSavedHead: B })
    expect(session.state.versions.draft.snapshot?.baseCommit).toBe(B)
    expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Persist local'))
    expect(mocks.db!.title).toBe('Original'); expect(putCalls()).toEqual([])
    expect(session.state.postSaveVerificationError).toBeTruthy()
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
    await mount(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('草稿版（wf）')
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).not.toBeNull())
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); mocks.deploy.mockRejectedValueOnce(new TypeError('network'))
    click('部署'); await screen.findByRole('button', { name: '确认部署' }); confirmDeploy(); await screen.findByText(/没有日志恢复或安全自动重试/)
    click('部署'); expect(mocks.deploy).toHaveBeenCalledTimes(1)
    expect(mocks.fetchJson.mock.calls.every(([url]) => !url.includes('pack-operations'))).toBe(true)
  })
  it('holds an unverifiable unified receipt as unknown without cleaning or retrying the candidate', async () => {
    unifiedAvailable = true; await mount('draft'); await editFile('scripts/run', 'uncertain')
    const originalFetch = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/unified-save') && init?.method === 'POST'
      ? Promise.resolve(new Response(JSON.stringify({ requestId: 'wrong', outcome: 'confirmed', reconciliationRequired: false,
        evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit: C, draftToken: 'b'.repeat(64) } })))
      : originalFetch(url, init))
    click('保存'); await screen.findByText(/统一保存未能完整确认/)
    const session = sessionFor(mocks.db!.id)
    expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('uncertain'))
    expect(session.state.versions.draft.snapshot?.baseCommit).toBe(B)
    expect(session.state.postSaveVerificationError).toBeTruthy(); expect(putCalls()).toHaveLength(0)
    click('部署'); expect(mocks.deploy).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    act(() => state().updateSpecField('title', 'Independent DB edit')); click('保存')
    await screen.findByText(/数据库写入结果未终结，需人工核对/)
    expect(mocks.db!.title).toBe('Original'); expect(state().isDirty).toBe(true)
    expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(1)
    expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('uncertain'))
  })
  it('blocks confirmation if the database changes after the dialog opens', async () => {
    await mount(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('草稿版（wf）')
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).not.toBeNull())
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); click('部署'); await screen.findByRole('button', { name: '确认部署' })
    mocks.db = { ...mocks.db!, title: 'External change' }; confirmDeploy()
    await screen.findByText(/数据库草稿或本地编辑已变化/); expect(mocks.deploy).not.toHaveBeenCalled()
  })

  it('refuses confirmation when the CAS row changes but its visible document stays the same', async () => {
    await mount('draft'); click('部署'); await screen.findByRole('button', { name: '确认部署' })
    const prior = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => url.endsWith('/editor-draft') && init?.method === 'GET'
      ? Promise.resolve({ workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'b'.repeat(64) })
      : prior(url, init))
    confirmDeploy()
    await screen.findByText(/数据库草稿或本地编辑已变化/); expect(mocks.deploy).not.toHaveBeenCalled()
  })

  it.each(['different-pack', null])('blocks deployment before confirmation when DB pack %s differs from Git', async packId => {
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => url.endsWith('/editor-draft') && init?.method === 'GET'
      ? Promise.resolve({ workflowId: mocks.db!.id, packId, specJson: JSON.stringify(mocks.db), draftToken: 'a'.repeat(64) })
      : original(url, init))
    await mount('draft'); click('部署')
    expect(await screen.findByText(/数据库草稿的 Pack 与 Git 文件快照不一致/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(mocks.db!.id)}`)).toBeNull()
  })
  it('allows a null DB pack when the Git Pack is the workflow itself', async () => {
    const id = mocks.db!.id
    const withPack = (source: Snapshot): Snapshot => ({ ...source, packId: id,
      versionFiles: source.versionFiles.map(item => item.path === 'workflow.pack.yaml'
        ? file(item.path, stringify({ id, workflows: [{ id, file: 'flows/custom.yaml' }] })) : item) })
    current = withPack(current); saved = withPack(saved)
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => url.endsWith('/editor-draft') && init?.method === 'GET'
      ? Promise.resolve({ workflowId: id, packId: null, specJson: JSON.stringify(mocks.db), draftToken: 'a'.repeat(64) })
      : original(url, init))
    await mount('draft'); click('部署'); await screen.findByRole('button', { name: '确认部署' })
    confirmDeploy(); await screen.findByText(/部署成功/)
    expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: id, packId: id }))
  })
  it.each([['new workflow', 'conflict'], ['same ID new session', 'conflict'], ['new workflow', 'read failure'], ['same ID new session', 'read failure']] as const)('does not show a late %s / %s error', async (target, failure) => {
    const id = mocks.db!.id, pending = deferred<unknown>()
    const view = render(<Editor embedded initialWorkflowId={id} />)
    await waitFor(() => expect(sessionFor(id).state.versions.current.loading).toBe(false))
    click('部署'); await screen.findByRole('button', { name: '确认部署' })
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => url.endsWith(`/${id}/editor-draft`) && init?.method === 'GET'
      ? pending.promise : original(url, init))
    confirmDeploy()
    await waitFor(() => expect(mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/editor-draft') && init?.method === 'GET')).toHaveLength(2))
    const previousSession = state().sessionId
    const next = { ...mocks.db!, id: target === 'new workflow' ? `${id}-next` : id, title: 'Replacement session' }
    if (target === 'same ID new session') {
      act(() => { state().reset({ force: true }); state().loadSpec(next) })
    } else {
      mocks.db = next
    }
    view.rerender(<Editor embedded initialWorkflowId={next.id} />)
    await waitFor(() => expect(screen.getByText('· Replacement session')).toBeInTheDocument())
    expect(state().sessionId).not.toBe(previousSession)
    await act(async () => {
      if (failure === 'read failure') pending.reject(new Error('late CAS failure'))
      else pending.resolve({ workflowId: id, packId: 'pack', specJson: JSON.stringify({ ...next, id }), draftToken: 'b'.repeat(64) })
      await pending.promise.catch(() => undefined)
    })
    expect(screen.queryByText(/部署已阻止|数据库草稿或本地编辑已变化|late CAS failure/)).not.toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(id)}`)).toBeNull()
  })

  it('refuses to overwrite a different 草稿(wf) head when editing current files', async () => {
    unifiedAvailable = true; current = { ...current, draftHead: B }; await mount('draft')
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    await waitFor(() => expect(sessionFor(mocks.db!.id).state.version).toBe('draft'))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); click('当前版（生效 tag）'); await screen.findByRole('button', { name: 'scripts/run' }); click('scripts/run')
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'current edit' } }); click('保存')
    await screen.findByText(/已有不同的草稿，不能用当前版覆盖/)
    expect(putCalls()).toHaveLength(0)
    expect(screen.getByLabelText('文件内容')).toHaveValue('current edit')
  })
  it('does not send unified save when the preflight DB row identity is invalid', async () => {
    unifiedAvailable = true; await mount('draft'); act(() => state().updateSpecField('title', 'Keep canvas')); await editFile('scripts/run', 'Keep file')
    const original = mocks.fetchJson.getMockImplementation()!
    mocks.fetchJson.mockImplementation((url, init) => url.endsWith('/editor-draft') && init?.method === 'GET'
      ? Promise.resolve({ workflowId: 'wrong', packId: 'pack', specJson: '{}', draftToken: 'b'.repeat(64) }) : original(url, init))
    click('保存'); await screen.findByText(/无法核对数据库草稿/)
    expect(state().isDirty).toBe(true); expect(screen.getByLabelText('文件内容')).toHaveValue('Keep file')
    expect(mocks.db!.title).toBe('Original'); expect(putCalls()).toHaveLength(0)
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toEqual([])
  })

})

it('saves the full wf draft without moving active, then deploys without rewriting wf or local edits', async () => {
  unifiedAvailable = true
  const production = structuredClone(current)
  await mount('draft')
  act(() => state().updateSpecField('title', 'New draft'))
  await editFile('scripts/run', 'new script')
  expect(current).toEqual(production); expect(mocks.db!.title).toBe('Original')
  expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  click('保存'); await waitFor(() => expect(state().isDirty).toBe(false))
  expect(current).toEqual(production)
  expect(saved.baseRef).toBe(`origin/wf/${mocks.db!.id}`)
  expect(atob(saved.versionFiles.find(f => f.path === 'flows/custom.yaml')!.contentBase64)).toContain('New draft')
  expect(saved.versionFiles.find(f => f.path === 'assets/raw')?.contentBase64).toBe('AP8C')
  const draft = structuredClone(saved), pending = deferred<any>()
  mocks.deploy.mockImplementationOnce(() => pending.promise)
  click('部署'); await screen.findByRole('button', { name: '确认部署' })
  expect(screen.getByText(new RegExp(`当前生效 tag：.*deploy/${mocks.db!.id}/#1`))).toBeInTheDocument()
  expect(screen.getByText('更新：scripts/run')).toBeInTheDocument()
  confirmDeploy(); await waitFor(() => expect(mocks.deploy).toHaveBeenCalledTimes(1))
  act(() => state().updateSpecField('title', 'Unsaved during deploy'))
  const input = mocks.deploy.mock.calls[0][0]
  await act(async () => pending.resolve({ ok: true, deployed: true, workflowId: input.workflowId, packId: 'pack', deployNumber: 2, version: 2,
    tagName: `deploy/${input.workflowId}/#2`, snapshotProtocol: 'saved-tree-v2', candidateCommit: C, expectedActiveCommit: A }))
  await screen.findByText(/部署成功/)
  expect(saved).toEqual(draft)
  expect(state().spec!.title).toBe('Unsaved during deploy'); expect(state().isDirty).toBe(true)
  expect(input).toMatchObject({ candidateCommit: C, expectedActiveCommit: A, expectedDraftToken: draftToken, snapshotProtocol: 'saved-tree-v2' })
  expect(mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(1)
})
it.each(['wf-production', 'legacy-draft'] as const)('does not approve deployment from %s', async scenario => {
  await mount('draft')
  if (scenario === 'wf-production') current = { ...current, baseRef: `origin/wf/${mocks.db!.id}` }
  else saved = { ...saved, baseRef: `origin/saved/${mocks.db!.id}` }
  click('部署'); await screen.findByText(/已阻止部署/)
  expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})

it('rejects an old saved response rather than saving it over wf and retains canvas edits', async () => {
  unifiedAvailable = true
  saved = { ...saved, baseRef: `origin/saved/${mocks.db!.id}`, draftHead: null }
  const production = structuredClone(current)
  await mount('draft'); act(() => state().updateSpecField('title', 'Keep local draft'))
  fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('无法读取完整版本文件')
  expect(screen.queryByRole('button', { name: 'scripts/run' })).not.toBeInTheDocument()
  expect(sessionFor(mocks.db!.id).state.versions.draft.snapshot).toBeNull()
  expect(state().spec!.title).toBe('Keep local draft')
  expect(mocks.fetchJson.mock.calls.some(([url, init]) => url.endsWith('/unified-save') && init?.method === 'POST')).toBe(false)
  expect(current).toEqual(production)
})
it('retains local edits when wf post-save verification returns only a legacy snapshot', async () => {
  unifiedAvailable = true
  const original = mocks.fetchJson.getMockImplementation()!
  mocks.fetchJson.mockImplementation(async (url, init) => {
    const response = await original(url, init)
    return url.endsWith('version=draft') && saved.baseCommit === C ? { ...response, baseRef: `origin/saved/${mocks.db!.id}` } : response
  })
  await mount('draft'); act(() => state().updateSpecField('title', 'Keep unconfirmed draft'))
  await editFile('scripts/run', 'unconfirmed files')
  click('保存'); await screen.findByText(/统一保存未能完整确认/)
  const session = sessionFor(mocks.db!.id)
  expect(state().spec!.title).toBe('Keep unconfirmed draft')
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')!.contentBase64).toBe(btoa('unconfirmed files'))
  expect(session.state.postSaveVerificationError).toBeTruthy()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
