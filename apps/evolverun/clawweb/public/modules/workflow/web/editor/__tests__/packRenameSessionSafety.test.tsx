import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import {
  dirty, hasPackVersionEditRisk, hasPackVersionRisk, load, publish, releaseCleanSession,
  sessionFor, updateVersion, type Snapshot, type Version,
} from '../packVersionSession'
import { usePackCanvasSession } from '../usePackCanvasSession'
import { savePackVersion } from '../packVersionSave'
import { useEditorStore } from '../store'
import Editor from '../../pages/Editor'

const ui = vi.hoisted(() => ({ db: null as WorkflowSpec | null, deploy: vi.fn() }))
// Keep the real Editor deployment checks; stub its query hooks and unrelated UI.
// Save, DB verification, Pack transport, reload and session lifecycle remain real.
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: ui.db, databaseSpec: ui.db, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: ui.deploy, isPending: false }),
  useFacadeBindings: () => ({ data: [] }),
}))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))

const A = 'a'.repeat(40), B = 'b'.repeat(40)
let sequence = 0
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
async function readBody(init: RequestInit) {
  const text = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = reject
    reader.readAsText(init.body as Blob)
  })
  return JSON.parse(text)
}

afterEach(() => {
  cleanup()
  useEditorStore.getState().reset({ force: true })
  vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.resetAllMocks()
})

function setup({ rename = true, failDb = false, originRejected = true } = {}) {
  const id = `rename-session-${++sequence}`
  const spec: WorkflowSpec = { id, title: 'Workflow', version: '1', nodes: [] }
  ui.db = spec
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec(spec); useEditorStore.setState({ _databasePackId: 'pack' })
  const file = (path: string, text: string) => ({ path, contentBase64: btoa(text), mode: '100644' as const })
  const files = [file('workflow.pack.yaml', stringify({ id: 'pack', workflows: [id] })),
    file(`workflows/${id}.yaml`, stringify(spec)), file('scripts/run.sh', 'original')]
  const current: Snapshot = { workflowId: id, packId: 'pack', baseRef: `refs/tags/deploy/${id}/#1`,
    baseCommit: A, draftHead: B, canWrite: true, versionFiles: files }
  let draft: Snapshot = { ...current, baseRef: `origin/wf/${id}`, baseCommit: B }
  const session = sessionFor(id)
  updateVersion(session, 'current', { snapshot: current, files: structuredClone(files) })
  updateVersion(session, 'draft', { snapshot: draft, files: files.map(f => f.path === 'workflow.pack.yaml' && rename
    ? { ...f, contentBase64: btoa(stringify({ id: 'renamed', workflows: [id] })) }
    : f.path === 'scripts/run.sh' ? { ...f, contentBase64: btoa('edited'), mode: '100755' } : f) })
  publish(session, { ...session.state, version: 'draft' })
  let databasePackId = 'pack'
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === `/api/workflows/${id}/unified-save` && init?.method === 'GET') return json({ protocol: 'workflow-unified-save-v1' })
    if (url === `/api/workflows/${id}/unified-save` && init?.method === 'POST' && originRejected) return json({ code: 'ORIGIN_NOT_ALLOWED' }, 403)
    if (url === `/api/workflows/${id}`) return json(ui.db)
    if (url === `/api/workflows/${id}/editor-draft` && init?.method === 'GET') return json({ workflowId: id, packId: databasePackId, specJson: JSON.stringify(ui.db), draftToken: 'a'.repeat(64) })
    if (url.startsWith(`/api/workflows/${id}/history?`)) return json({ workflowId: id, history: [], active: null })
    if (url === '/api/auto-heal/save') {
      if (failDb) throw new TypeError('DB response lost')
      const input = await readBody(init!)
      ui.db = input.spec; databasePackId = input.packId ?? databasePackId
      return json({ ok: true, saved: true, workflowId: id, packId: databasePackId })
    }
    if (url === `/api/workflows/${id}/pack-version` && init?.method === 'PUT') {
      if (originRejected) return json({ code: 'ORIGIN_NOT_ALLOWED' }, 403)
      const input = await readBody(init)
      draft = { ...draft, packId: databasePackId, versionFiles: structuredClone(session.state.versions.draft.files) }
      return json({ branch: `wf/${id}`, commit: B, requestId: input.requestId, version: 2, packId: databasePackId })
    }
    if (url === `/api/workflows/${id}/pack-version?version=current`) return json(current)
    if (url === `/api/workflows/${id}/pack-version?version=draft`) return json(draft)
    throw new Error(`Unexpected HTTP request: ${init?.method ?? 'GET'} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  const hook = renderHook(() => usePackCanvasSession(id, spec))
  return { id, spec, session, hook, fetchMock, databasePackId: () => databasePackId, setDatabasePackId: (value: string) => { databasePackId = value } }
}

/** Rehydrate the exact session-level guard produced by the former rename path.
 * This intentionally does not claim that the now-disabled path can create it. */
function seedHistoricalRename(ctx: ReturnType<typeof setup>, failDb = false) {
  const dbState = failDb ? '已发送，结果未知' : '已确认 Pack ID renamed，文档已核对'
  const gitState = failDb ? 'Git 保存未发起' : 'Git 保存已发起，结果未确认；请核对 草稿(wf)分支和完整候选'
  const message = `Pack 重命名 pack → renamed 未完整完成：数据库${dbState}；${gitState}。请先人工核对数据库和 Git，不能自动重试。本页会话已锁定；刷新会丢失本地草稿和此锁定状态。`
  const files = structuredClone(ctx.session.state.versions.draft.files)
  ctx.setDatabasePackId(failDb ? 'pack' : 'renamed')
  act(() => {
    publish(ctx.session, { ...ctx.session.state, uncertainRename: message })
    for (const version of ['current', 'draft'] as const) updateVersion(ctx.session, version, { blocked: true, error: message })
  })
  expect(ctx.session.state.versions.draft.files).toEqual(files)
  expect(ctx.session.state.saving).toBe(false)
  expect(ctx.session.state.reconciliation).toBeNull()
  expect(ctx.fetchMock).not.toHaveBeenCalled()
}

async function reloadBoth(ctx: ReturnType<typeof setup>) {
  for (const version of ['current', 'draft'] as Version[]) {
    act(() => { publish(ctx.session, { ...ctx.session.state, version }); load(ctx.session, ctx.id, version) })
    await waitFor(() => expect(ctx.session.state.versions[version].loading).toBe(false))
    expect(ctx.session.state.versions[version].snapshot?.packId).toBe('pack')
    expect(dirty(ctx.session.state.versions[version])).toBe(false)
    expect(ctx.session.state.versions[version].blocked).toBe(false)
  }
}

// Fails when safety depends only on per-version blocked/dirty state, which a
// successful snapshot reload legitimately clears without settling the rename.
it.each([false, true])('retains rename risk after real reload of both versions (DB response lost: %s)', async failDb => {
  const ctx = setup({ failDb })
  seedHistoricalRename(ctx, failDb)
  if (!failDb) expect(ctx.databasePackId()).toBe('renamed')
  const message = ctx.hook.result.current.error
  expect(message).toContain('Pack 重命名')
  await reloadBoth(ctx)
  expect(hasPackVersionEditRisk(ctx.id)).toBe(true)
  expect(hasPackVersionRisk(ctx.id)).toBe(true)
  expect(ctx.hook.result.current.error).toBe(message)
  const requests = ctx.fetchMock.mock.calls.length
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不能自动重试') })
  expect(ctx.fetchMock).toHaveBeenCalledTimes(requests)
})

// Fails if a clean-file session can be released and lose its rename guard on a
// component unmount/remount, including the hook's queued releaseCleanSession.
it('retains a clean-file rename session through release, unmount and remount', async () => {
  const ctx = setup()
  seedHistoricalRename(ctx); await reloadBoth(ctx)
  expect(ctx.session.canvasDirty).toBe(false)
  await act(async () => { ctx.hook.unmount(); await Promise.resolve() })
  expect(ctx.session.listeners.size).toBe(0)
  releaseCleanSession(ctx.id, ctx.session)
  expect(sessionFor(ctx.id)).toBe(ctx.session)
  const remounted = renderHook(() => usePackCanvasSession(ctx.id, ctx.spec))
  expect(remounted.result.current.error).toContain('Pack 重命名')
  const requests = ctx.fetchMock.mock.calls.length
  await act(async () => { await expect(remounted.result.current.save()).rejects.toThrow('不能自动重试') })
  expect(ctx.fetchMock).toHaveBeenCalledTimes(requests)
  expect(hasPackVersionEditRisk(ctx.id)).toBe(true)
})

it('blocks real Editor deployment before confirmation after rename rejection, reload and remount', async () => {
  const ctx = setup()
  seedHistoricalRename(ctx); await reloadBoth(ctx)
  await act(async () => { ctx.hook.unmount(); await Promise.resolve() })
  render(<Editor embedded initialWorkflowId={ctx.id} />)
  await act(async () => { await Promise.resolve() })
  const requests = ctx.fetchMock.mock.calls.length
  fireEvent.click(screen.getByRole('button', { name: '部署', exact: true }))
  await screen.findByText('版本文件存在未保存更改或保存状态不确定，请先处理后再部署。')
  expect(screen.queryByRole('button', { name: '确认部署', exact: true })).not.toBeInTheDocument()
  expect(ui.deploy).not.toHaveBeenCalled()
  expect(ctx.fetchMock).toHaveBeenCalledTimes(requests)
  expect(sessionFor(ctx.id)).toBe(ctx.session)
})

it('shows an independent snapshot read failure without hiding the persistent rename warning', async () => {
  const ctx = setup()
  seedHistoricalRename(ctx); await reloadBoth(ctx)
  await act(async () => { ctx.hook.unmount(); await Promise.resolve() })
  render(<Editor embedded initialWorkflowId={ctx.id} />)
  fireEvent.click(screen.getByRole('tab', { name: '脚本文件', exact: true }))
  await screen.findByText(/Pack 重命名 pack → renamed 未完整完成/)
  ctx.fetchMock.mockResolvedValueOnce(json({ code: 'SERVICE_UNAVAILABLE' }, 503))
  fireEvent.click(screen.getByRole('button', { name: '重新读取', exact: true }))
  await waitFor(() => expect(ctx.session.state.versions.draft.loading).toBe(false))
  const readError = ctx.session.state.versions.draft.error!
  expect(readError).not.toContain('Pack 重命名')
  expect(screen.getByText(readError, { exact: true })).toHaveAttribute('role', 'alert')
  expect(screen.getByText(/Pack 重命名 pack → renamed 未完整完成/)).toHaveAttribute('role', 'alert')
  expect(hasPackVersionEditRisk(ctx.id)).toBe(true)
})

it('refuses shared save without sending a write after a locked rename is reloaded', async () => {
  const ctx = setup()
  seedHistoricalRename(ctx); await reloadBoth(ctx)
  await act(async () => { ctx.hook.unmount(); await Promise.resolve() })
  render(<Editor embedded initialWorkflowId={ctx.id} />)
  fireEvent.click(screen.getByRole('tab', { name: '脚本文件', exact: true }))
  fireEvent.click(await screen.findByRole('button', { name: 'scripts/run.sh', exact: true }))
  fireEvent.change(screen.getByRole('textbox', { name: '文件内容' }), { target: { value: 'new candidate after reload' } })
  expect(dirty(ctx.session.state.versions.draft)).toBe(true)
  expect(screen.queryByRole('button', { name: '保存到草稿', exact: true })).not.toBeInTheDocument()
  const requests = ctx.fetchMock.mock.calls.length
  const files = structuredClone(ctx.session.state.versions.draft.files)
  const guard = ctx.session.state.uncertainRename
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '保存', exact: true })); await Promise.resolve() })
  expect(ctx.fetchMock).toHaveBeenCalledTimes(requests)
  expect(ctx.session.state.versions.draft.files).toEqual(files)
  expect(ctx.session.state.uncertainRename).toBe(guard)
  expect(screen.getByRole('alert')).toHaveTextContent('Pack 重命名')
  expect(screen.getByRole('button', { name: '重新读取', exact: true })).toBeEnabled()
})

it('keeps the historical guard when a version is unblocked and the selection changes', async () => {
  const ctx = setup(); seedHistoricalRename(ctx, true)
  const message = ctx.session.state.uncertainRename
  act(() => { updateVersion(ctx.session, 'current', { blocked: false }); publish(ctx.session, { ...ctx.session.state, version: 'current' }) })
  expect(ctx.session.state.versions.current.blocked).toBe(false)
  expect(ctx.hook.result.current.error).toBe(message)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不能自动重试') })
  expect(ctx.fetchMock).not.toHaveBeenCalled()
  expect(ctx.session.state.uncertainRename).toBe(message)
  expect(hasPackVersionRisk(ctx.id)).toBe(true)
})

it('blocks standalone Pack saves after rename rejection even when both versions reload clean', async () => {
  const ctx = setup()
  seedHistoricalRename(ctx); await reloadBoth(ctx)
  const requests = ctx.fetchMock.mock.calls.length
  const state = ctx.session.state
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  for (const version of ['current', 'draft'] as Version[]) {
    const files = ctx.session.state.versions[version].files.map(file => file.path === 'scripts/run.sh'
      ? { ...file, contentBase64: btoa('new standalone candidate') } : file)
    await act(async () => { expect(await savePackVersion(ctx.session, ctx.id, version, files)).toBeNull() })
  }
  expect(ctx.fetchMock).toHaveBeenCalledTimes(requests)
  expect(confirm).not.toHaveBeenCalled()
  expect(ctx.session.state).toBe(state)
  expect(ctx.session.state.reconciliation).toBeNull()
  expect(hasPackVersionEditRisk(ctx.id)).toBe(true)
})

it('does not give a non-rename origin rejection a session-level lock', async () => {
  const ctx = setup({ rename: false })
  const candidate = structuredClone(ctx.session.state.versions.draft.files)
  await act(async () => { expect(await savePackVersion(ctx.session, ctx.id, 'draft')).toBeNull() })
  expect(ctx.fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([[`/api/workflows/${ctx.id}/pack-version`, 'PUT']])
  expect(ctx.session.state.versions.draft.error).toContain('版本文件保存请求被来源校验拒绝')
  expect(ctx.session.state.versions.draft.files).toEqual(candidate)
  expect(dirty(ctx.session.state.versions.draft)).toBe(true)
  expect(ctx.session.state.uncertainRename).toBeNull()
  expect(ctx.session.state.reconciliation).toBeNull()
  expect(ctx.session.state.versions.draft.blocked).toBe(false)
  await reloadBoth(ctx)
  expect(hasPackVersionEditRisk(ctx.id)).toBe(false)
  expect(hasPackVersionRisk(ctx.id)).toBe(false)
  await act(async () => { ctx.hook.unmount(); await Promise.resolve() })
  expect(sessionFor(ctx.id)).not.toBe(ctx.session)
})

it('quarantines a non-rename unified save after an origin rejection without origin-bound proof', async () => {
  const ctx = setup({ rename: false })
  const candidate = structuredClone(ctx.session.state.versions.draft.files)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('统一保存未能完整确认') })
  expect(ctx.session.state.uncertainRename).toBeNull()
  expect(ctx.session.state.postSaveVerificationError).toContain('勿重复提交')
  expect(ctx.session.state.versions.draft.files).toEqual(candidate)
  expect(dirty(ctx.session.state.versions.draft)).toBe(true)
  expect(ctx.fetchMock.mock.calls.filter(([url, init]) => url === `/api/workflows/${ctx.id}/unified-save` && init?.method === 'POST')).toHaveLength(1)
  const requests = ctx.fetchMock.mock.calls.length
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不要重试') })
  expect(ctx.fetchMock).toHaveBeenCalledTimes(requests)
})

it('blocks a new rename before any write without creating a historical guard', async () => {
  const ctx = setup({ originRejected: false })
  const candidate = structuredClone(ctx.session.state.versions.draft.files)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('暂不支持') })
  expect(ctx.fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([[`/api/workflows/${ctx.id}/unified-save`, 'GET']])
  expect(ctx.databasePackId()).toBe('pack')
  expect(ctx.session.state.uncertainRename).toBeNull()
  expect(ctx.session.state.versions.draft.files).toEqual(candidate)
  expect(dirty(ctx.session.state.versions.draft)).toBe(true)
})

it('isolates a historical rename guard from a different workflow save', async () => {
  const ctx = setup(); seedHistoricalRename(ctx); await reloadBoth(ctx)
  const otherId = `${ctx.id}-other`, other = sessionFor(otherId)
  const otherSpec = { ...ctx.spec, id: otherId }
  const files = [
    { path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'other-pack', workflows: [otherId] })), mode: '100644' as const },
    { path: `workflows/${otherId}.yaml`, contentBase64: btoa(stringify(otherSpec)), mode: '100644' as const },
    { path: 'scripts/run.sh', contentBase64: btoa('original'), mode: '100644' as const },
  ]
  const otherSnapshot: Snapshot = { workflowId: otherId, packId: 'other-pack', baseRef: `origin/wf/${otherId}`,
    baseCommit: A, draftHead: A, canWrite: true, versionFiles: files }
  const candidate = files.map(file => file.path === 'scripts/run.sh' ? { ...file, contentBase64: btoa('other edit') } : file)
  updateVersion(other, 'draft', { snapshot: otherSnapshot, files: candidate })
  const original = ctx.fetchMock.getMockImplementation()!
  ctx.fetchMock.mockImplementation(async (url, init) => {
    if (url === `/api/workflows/${otherId}/pack-version` && init?.method === 'PUT') {
      const input = await readBody(init)
      return json({ branch: `wf/${otherId}`, commit: B, requestId: input.requestId, version: 2, packId: 'other-pack' })
    }
    return original(url, init)
  })
  expect(other.state.uncertainRename).toBeNull()
  await act(async () => { expect(await savePackVersion(other, otherId, 'draft', candidate)).toMatchObject({ commit: B, packId: 'other-pack' }) })
  expect(ctx.fetchMock.mock.calls.filter(([url, init]) => url === `/api/workflows/${otherId}/pack-version` && init?.method === 'PUT')).toHaveLength(1)
  expect(ctx.session.state.uncertainRename).toContain('不能自动重试')
  expect(hasPackVersionRisk(ctx.id)).toBe(true); expect(hasPackVersionRisk(otherId)).toBe(false)
})
