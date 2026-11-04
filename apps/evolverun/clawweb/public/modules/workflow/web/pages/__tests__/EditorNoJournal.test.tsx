import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import { stringify } from 'yaml'
const mocks = vi.hoisted(() => ({ spec: null as any, snapshot: null as any, fetchJson: vi.fn(), get: vi.fn(), deploy: vi.fn() }))
vi.mock('../../api/hooks', () => ({ useEditorWorkflowDocument: () => ({ data: mocks.spec, isLoading: false }), useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, mutate: (input: any, callbacks: any) => { void mocks.deploy(input).then(callbacks.onSuccess, callbacks.onError) }, isPending: false }), useAutoHealDiagnose: () => ({ mutate: (_: any, callbacks: any) => callbacks.onSuccess({ ok: true, workflowId: mocks.spec.id, diagnosisId: 'd', summary: 'diagnosis', errorChain: [], fixSuggestions: [], fixedYaml: '', fixedSpec: mocks.spec, diff: [], rawResponse: '' }) }), useAutoHealSave: () => ({ mutate: (_: any, callbacks: any) => callbacks.onSuccess({ ok: true, saved: true, workflowId: mocks.spec.id, packId: 'pack' }) }) }))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => null }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson, api: { workflows: { get: mocks.get, getHistory: vi.fn().mockResolvedValue({ history: [], active: null }), getDeploySnapshot: vi.fn() } } }))
vi.mock('../../api/useAutoHealSourceRun', () => ({ useAutoHealSourceRun: () => ({ mutate: vi.fn(), isPending: false }) }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { useEditorStore } from '../../editor/store'
import { sessionFor, publish, releaseCleanSession } from '../../editor/packVersionSession'
import { createWorkflowPack } from '../../editor/newWorkflowSave'
import Editor from '../Editor'
import AutoHealPanel from '../../components/AutoHealPanel'
let sequence = 0
function setup(source: 'draft' | 'current' = 'draft', fresh = false, existingId?: string) {
  const id = existingId ?? `deploy-db-${++sequence}`, commit = 'a'.repeat(40)
  mocks.spec = { id, title: 'DB draft', version: '1', nodes: [] }
  mocks.snapshot = { workflowId: id, packId: 'pack', canWrite: true, baseRef: `origin/saved/${id}`, baseCommit: commit, draftHead: commit,
    versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' },
      { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify({ ...mocks.spec, title: 'Old Git' })), mode: '100644' }] }
  if (source === 'current') mocks.snapshot = { ...mocks.snapshot, baseRef: `origin/wf/${id}`, draftHead: null }
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec(mocks.spec)
  const session = sessionFor(id)
  if (!fresh) publish(session, { ...session.state, version: source, versions: { ...session.state.versions,
    [source]: { snapshot: mocks.snapshot, files: structuredClone(mocks.snapshot.versionFiles), loading: false, blocked: false, error: null, notice: null } } })
  mocks.fetchJson.mockReset(); mocks.deploy.mockReset(); mocks.get.mockReset()
  mocks.fetchJson.mockImplementation(async (url: string) => {
    if (fresh && url.endsWith('version=current')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    if (source === 'current' && url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    return mocks.snapshot
  }); mocks.get.mockResolvedValue(mocks.spec)
  render(<MemoryRouter><Editor embedded initialWorkflowId={id} /></MemoryRouter>)
  return { id, session }
}
beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.clearAllMocks() })
it('blocks deployment of unsaved canvas without issuing a request', async () => {
  setup(); await act(async () => {}); act(() => useEditorStore.getState().updateSpecField('title', 'Unsaved'))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(screen.getByText(/画布有未保存改动/)).toBeInTheDocument(); expect(mocks.deploy).not.toHaveBeenCalled()
})
it('deploys the saved files and DB draft even when Git YAML differs', async () => {
  const { id } = setup()
  mocks.deploy.mockResolvedValue({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 2, version: 2, tagName: 'wf-tag' })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认部署' }))
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: id, candidateCommit: 'a'.repeat(40) })))
  expect(mocks.fetchJson.mock.calls.map(([url]) => url)).toEqual([`/api/workflows/${id}/pack-version?version=draft`])
  expect(await screen.findByText(/部署成功/)).toBeInTheDocument()
})
it('keeps unknown deployment visible and never offers journal recovery or retries', async () => {
  setup(); mocks.deploy.mockRejectedValue(new Error('lost'))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认部署' }))
  expect(await screen.findByText(/没有日志恢复或安全自动重试/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /恢复部署/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(mocks.deploy).toHaveBeenCalledTimes(1)
  expect(mocks.fetchJson.mock.calls.every(([url]) => !url.includes('pack-operations'))).toBe(true)
})
it('falls back to the verified current wf only when saved is absent', async () => {
  const { id } = setup('current')
  mocks.deploy.mockResolvedValue({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 2, version: 2, tagName: 'wf-tag' })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  expect(screen.getByText(/确认时没有 saved 分支/)).toBeInTheDocument()
  expect(screen.getByText(/当前 wf commit/)).toHaveTextContent('a'.repeat(40))
  expect(mocks.fetchJson.mock.calls.map(([url]) => url)).toEqual([
    `/api/workflows/${id}/pack-version?version=draft`, `/api/workflows/${id}/pack-version?version=current`,
  ])
  fireEvent.click(confirm)
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: id, candidateCommit: 'a'.repeat(40) })))
  expect(await screen.findByText(/部署成功/)).toBeInTheDocument()
})
it.each([
  ['generic 404', { status: 404, body: 'not found' }],
  ['wrong 404 code', { status: 404, body: '{"code":"OTHER"}' }],
  ['forbidden', { status: 403, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }],
  ['unavailable', { status: 503, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }],
  ['network failure', new Error('offline')],
])('never falls back on %s', async (_label, failure) => {
  const { id } = setup('current')
  mocks.fetchJson.mockRejectedValue(failure)
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/已阻止部署/)).toBeInTheDocument()
  expect(mocks.fetchJson.mock.calls.map(([url]) => url)).toEqual([`/api/workflows/${id}/pack-version?version=draft`])
  expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('refuses current when saved exists or appeared after the draft 404', async () => {
  setup('current')
  mocks.fetchJson.mockImplementation(async (url: string) => {
    if (url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    return { ...mocks.snapshot, draftHead: 'b'.repeat(40) }
  })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/saved 分支存在或已出现/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('refuses a changed current baseline even if no saved ref exists', async () => {
  setup('current')
  mocks.fetchJson.mockImplementation(async (url: string) => {
    if (url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    return { ...mocks.snapshot, baseCommit: 'b'.repeat(40) }
  })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/当前文件快照与已读取基线不一致/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('passes the current commit as a precondition and does not claim success on a race', async () => {
  const { id } = setup('current')
  mocks.deploy.mockRejectedValue({ status: 409, body: '{"code":"CANDIDATE_NOT_SAVED"}' })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认部署' }))
  await waitFor(() => expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: id, candidateCommit: 'a'.repeat(40) })))
  expect(await screen.findByText(/部署失败或结果未知/)).toBeInTheDocument()
  expect(screen.queryByText(/部署成功/)).not.toBeInTheDocument()
})
it('rejects a current baseline that was read while saved existed', async () => {
  const { session } = setup('current')
  act(() => {
    const current = session.state.versions.current
    publish(session, { ...session.state, versions: { ...session.state.versions,
      current: { ...current, snapshot: { ...current.snapshot!, draftHead: 'b'.repeat(40) } } } })
  })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/当前文件快照与已读取基线不一致/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('blocks fallback confirmation if local file edits happen during the fresh read', async () => {
  const { session } = setup('current')
  let resolve!: (value: unknown) => void
  const waiting = new Promise(r => { resolve = r })
  mocks.fetchJson.mockImplementation(async (url: string) => {
    if (url.endsWith('version=draft')) throw { status: 404, body: '{"code":"SNAPSHOT_NOT_FOUND"}' }
    return waiting
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
function openPanel(id: string) { return render(<AutoHealPanel run={panelRun(id)} onClose={() => {}} />) }
async function diagnoseAndSave() {
  fireEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  fireEvent.click(await screen.findByRole('button', { name: '保存修复' }))
  return screen.findByRole('button', { name: '部署修复' })
}
it('deploys a reopened saved-only workflow without manually loading draft', async () => {
  const id = `created-before-reload-${++sequence}`, commit = 'a'.repeat(40)
  const spec = { id, title: 'DB draft', version: '1', nodes: [] }
  const saved = { workflowId: id, packId: 'pack', canWrite: true, baseRef: `origin/saved/${id}`, baseCommit: commit, draftHead: commit,
    versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' },
      { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' }] }
  mocks.fetchJson.mockImplementation(async (_url, init) => init?.method === 'POST'
    ? { ok: true, saved: true, workflowId: id, packId: 'pack', branch: `saved/${id}`, commit } : saved)
  mocks.get.mockResolvedValue(spec)
  await createWorkflowPack(spec, 'pack')
  expect(sessionFor(id).state.creation?.phase).toBe('completed')
  // Release the in-memory session to model a page reload; storage is retained.
  releaseCleanSession(id, sessionFor(id))
  const { session } = setup('draft', true, id)
  await waitFor(() => expect(session.state.versions.current.loading).toBe(false))
  expect(session.state.versions.current.snapshot).toBeNull()
  expect(session.state.versions.draft.snapshot).toBeNull()
  mocks.deploy.mockResolvedValue({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 1, version: 1, tagName: 'v1' })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认部署' }))
  expect(await screen.findByText(/部署成功/)).toBeInTheDocument()
  expect(mocks.deploy).toHaveBeenCalledWith(expect.objectContaining({ workflowId: id, candidateCommit: 'a'.repeat(40) }))
  expect(localStorage.getItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(id)}`)).toBeNull()
})
it('blocks AutoHeal after Editor loses its acknowledgement and the panel remounts', async () => {
  const { id } = setup(); mocks.deploy.mockRejectedValue(new Error('lost'))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认部署' }))
  await screen.findByText(/没有日志恢复/)
  cleanup(); openPanel(id)
  expect(await screen.findByRole('alert')).toHaveTextContent('存在未确认的操作')
  fireEvent.click(screen.getByRole('button', { name: '开始诊断' }))
  expect(await screen.findByRole('button', { name: '保存修复' })).toBeDisabled()
  cleanup(); openPanel(id)
  expect(await screen.findByRole('alert')).toHaveTextContent('存在未确认的操作')
  expect(mocks.deploy).toHaveBeenCalledTimes(1)
})
it('blocks Editor after AutoHeal loses its acknowledgement and the editor remounts', async () => {
  const { id } = setup(); cleanup(); openPanel(id)
  mocks.deploy.mockRejectedValue(new Error('lost'))
  fireEvent.click(await diagnoseAndSave())
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
  const { id } = setup('draft', false, workflowId)
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
  const { id } = setup()
  mocks.deploy.mockResolvedValue({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 3, version: 3, tagName: 'wf-tag-3' })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  const marker = `clawweb.autoheal.review-blocked.v1.${encodeURIComponent(id)}`
  const removeItemSpy = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {})
  fireEvent.click(confirm)

  const recovery = await screen.findByRole('button', { name: '我已人工核对，清除当前工作流标记…' })
  expect(screen.getByRole('alert', { name: '部署操作' })).toHaveTextContent('部署已确认成功，但无法清除浏览器核对标记')
  expect(localStorage.getItem(marker)).toBe('1')
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
  setup()
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  const storageSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  fireEvent.click(confirm)
  expect(await screen.findByText(/部署未发送/)).toBeInTheDocument()
  storageSpy.mockRestore()
  expect(mocks.deploy).not.toHaveBeenCalled()
})

it('does not treat a fresh saved read as a baseline when local files exist without a snapshot', async () => {
  const { session } = setup('draft', true)
  await waitFor(() => expect(session.state.versions.current.loading).toBe(false))
  act(() => publish(session, { ...session.state, versions: { ...session.state.versions,
    draft: { ...session.state.versions.draft, files: [{ path: 'orphan', contentBase64: 'eA==', mode: '100644' }] } } }))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/saved文件快照与已读取基线不一致/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('does not bypass an existing conflicting baseline with a fresh saved read', async () => {
  setup()
  mocks.fetchJson.mockResolvedValue({ ...mocks.snapshot, baseCommit: 'b'.repeat(40), draftHead: 'b'.repeat(40) })
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  expect(await screen.findByText(/saved文件快照与已读取基线不一致/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('rechecks the shared marker when confirming an already opened Editor dialog', async () => {
  const { id } = setup()
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  localStorage.setItem(`clawweb.autoheal.review-blocked.v1.${encodeURIComponent(id)}`, '1')
  fireEvent.click(confirm)
  expect(await screen.findByText(/本浏览器存在未确认的部署/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
it('does not send AutoHeal deployment if persisting the shared marker fails', async () => {
  const { id } = setup(); cleanup(); openPanel(id)
  const deploy = await diagnoseAndSave()
  const storageSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  try {
    fireEvent.click(deploy)
    expect(await screen.findByText(/部署未发送/)).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  } finally { storageSpy.mockRestore() }
})
it('rejects the fresh saved-only baseline if layout edits appear before confirmation', async () => {
  const { session } = setup('draft', true)
  act(() => {
    mocks.spec = { ...mocks.spec, nodes: [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' } }] }
    mocks.get.mockResolvedValue(mocks.spec)
    useEditorStore.getState().loadSpec(mocks.spec)
  })
  await waitFor(() => expect(session.state.versions.current.loading).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '部署' }))
  const confirm = await screen.findByRole('button', { name: '确认部署' })
  act(() => useEditorStore.getState().moveNode('done', 600, 300))
  fireEvent.click(confirm)
  expect(await screen.findByText(/确认期间编辑内容已变化/)).toBeInTheDocument()
  expect(mocks.deploy).not.toHaveBeenCalled()
})
