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
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec(spec)
  const file = (path: string, text: string) => ({ path, contentBase64: btoa(text), mode: '100644' as const })
  const files = [file('workflow.pack.yaml', stringify({ id: 'pack', workflows: [id] })),
    file(`workflows/${id}.yaml`, stringify(spec)), file('scripts/run.sh', 'original')]
  const current: Snapshot = { workflowId: id, packId: 'pack', baseRef: `origin/wf/${id}`,
    baseCommit: A, draftHead: B, canWrite: true, versionFiles: files }
  let draft: Snapshot = { ...current, baseRef: `origin/saved/${id}`, baseCommit: B }
  const session = sessionFor(id)
  updateVersion(session, 'current', { snapshot: current, files: structuredClone(files) })
  updateVersion(session, 'draft', { snapshot: draft, files: files.map(f => f.path === 'workflow.pack.yaml' && rename
    ? { ...f, contentBase64: btoa(stringify({ id: 'renamed', workflows: [id] })) }
    : f.path === 'scripts/run.sh' ? { ...f, contentBase64: btoa('edited'), mode: '100755' } : f) })
  publish(session, { ...session.state, version: 'draft' })
  let databasePackId = 'pack'
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === `/api/workflows/${id}`) return json(ui.db)
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
      return json({ branch: `saved/${id}`, commit: B, requestId: input.requestId, version: 2, packId: databasePackId })
    }
    if (url === `/api/workflows/${id}/pack-version?version=current`) return json(current)
    if (url === `/api/workflows/${id}/pack-version?version=draft`) return json(draft)
    throw new Error(`Unexpected HTTP request: ${init?.method ?? 'GET'} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  const hook = renderHook(() => usePackCanvasSession(id, spec))
  return { id, spec, session, hook, fetchMock, databasePackId: () => databasePackId }
}

async function rejectRename(ctx: ReturnType<typeof setup>) {
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不能自动重试') })
  expect(ctx.session.state.saving).toBe(false)
  expect(ctx.session.state.reconciliation).toBeNull()
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
  await rejectRename(ctx)
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
  await rejectRename(ctx); await reloadBoth(ctx)
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
  await rejectRename(ctx); await reloadBoth(ctx)
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
  await rejectRename(ctx); await reloadBoth(ctx)
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

it('keeps the save button disabled for fresh edits after a locked rename is reloaded', async () => {
  const ctx = setup()
  await rejectRename(ctx); await reloadBoth(ctx)
  await act(async () => { ctx.hook.unmount(); await Promise.resolve() })
  render(<Editor embedded initialWorkflowId={ctx.id} />)
  fireEvent.click(screen.getByRole('tab', { name: '脚本文件', exact: true }))
  fireEvent.click(await screen.findByRole('button', { name: 'scripts/run.sh', exact: true }))
  fireEvent.change(screen.getByRole('textbox', { name: '文件内容' }), { target: { value: 'new candidate after reload' } })
  expect(dirty(ctx.session.state.versions.draft)).toBe(true)
  expect(screen.getByRole('button', { name: '保存到草稿', exact: true })).toBeDisabled()
  expect(screen.getByRole('button', { name: '重新读取', exact: true })).toBeEnabled()
})

it('blocks standalone Pack saves after rename rejection even when both versions reload clean', async () => {
  const ctx = setup()
  await rejectRename(ctx); await reloadBoth(ctx)
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
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('版本文件保存请求被来源校验拒绝') })
  expect(ctx.session.state.reconciliation).toBeNull()
  expect(ctx.session.state.versions.draft.blocked).toBe(false)
  await reloadBoth(ctx)
  expect(hasPackVersionEditRisk(ctx.id)).toBe(false)
  expect(hasPackVersionRisk(ctx.id)).toBe(false)
  await act(async () => { ctx.hook.unmount(); await Promise.resolve() })
  expect(sessionFor(ctx.id)).not.toBe(ctx.session)
})

it('does not retain a rename guard after both DB and Git confirm success', async () => {
  const ctx = setup({ originRejected: false })
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  expect(ctx.databasePackId()).toBe('renamed')
  expect(ctx.session.state.versions.draft.snapshot?.packId).toBe('renamed')
  expect(hasPackVersionEditRisk(ctx.id)).toBe(false)
  expect(hasPackVersionRisk(ctx.id)).toBe(false)
  await act(async () => { ctx.hook.unmount(); await Promise.resolve() })
  expect(sessionFor(ctx.id)).not.toBe(ctx.session)
})
