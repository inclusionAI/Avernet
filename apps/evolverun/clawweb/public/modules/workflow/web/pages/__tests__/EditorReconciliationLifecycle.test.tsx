import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ source: null as any, history: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ ...mocks.source, data: mocks.source?.spec, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({
  fetchJson: vi.fn(), api: { workflows: { get: vi.fn(), getHistory: mocks.history, getDeploySnapshot: vi.fn() } },
}))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/ScriptFilesTab', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import Editor from '../Editor'
import { readEditorWorkflowSource } from '../../api/editorWorkflowSource'
import { UNIFIED_SAVE_TIMEOUT_MS } from '../../api/workflowUnifiedSave'
import { useEditorStore } from '../../editor/store'
import { publish, sessionFor } from '../../editor/packVersionSession'
import { packWorkflowBaseline } from '../../editor/packCanvas'

let sequence = 0
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))
const b64 = (text: string) => Buffer.from(text, 'utf8').toString('base64')
beforeEach(() => {
  vi.resetAllMocks(); useEditorStore.getState().reset({ force: true })
  mocks.history.mockResolvedValue({ active: null, history: [] })
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    throw new Error('Unexpected write')
  }))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); useEditorStore.getState().reset({ force: true }) })
async function setup(extra: Record<string, unknown> = {}) {
  const id = `review-lifetime-${++sequence}`
  const db = { id, title: 'DB draft', version: '1', nodes: [], ...extra } as WorkflowSpec
  const git = { ...db, title: 'Saved workflow' }
  const row = { workflowId: id, packId: id, specJson: JSON.stringify(db), draftToken: 'a'.repeat(64) }
  vi.mocked(api.workflows.get).mockResolvedValue(db)
  vi.mocked(fetchJson).mockResolvedValue(row)
  mocks.source = await readEditorWorkflowSource(id)
  useEditorStore.getState().loadSpec(mocks.source.spec, mocks.source.databaseSpec)
  const files = [
    { path: 'workflow.pack.yaml', mode: '100644' as const, contentBase64: b64(stringify({ id, workflows: [id] })) },
    { path: `workflows/${id}.yaml`, mode: '100644' as const, contentBase64: b64(stringify(git)) },
    { path: 'scripts/run.sh', mode: '100755' as const, contentBase64: b64('echo keep\n') },
  ]
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions,
    draft: { snapshot: { workflowId: id, packId: id, baseRef: `origin/wf/${id}`, baseCommit: 'b'.repeat(40), draftHead: 'b'.repeat(40), canWrite: false, versionFiles: files },
      files: structuredClone(files), loading: false, error: null, blocked: false, notice: null } } })
  render(<Editor embedded initialWorkflowId={id} />)
  click('保存'); await screen.findByText(/Git 工作流 YAML 与数据库基线不一致或不可比较/)
  return { id, db, git, row, session, files }
}
function expectNoWrites() {
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  expect(vi.mocked(fetchJson).mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
}
it('real adapter ends pending state and opens the complete review for a normal DB/Git content difference', async () => {
  const ctx = await setup()
  let release!: (value: unknown) => void
  vi.mocked(fetchJson).mockImplementationOnce(() => new Promise(resolve => { release = resolve }) as never)
  click('核对数据库和 Git 差异…')
  expect(screen.getByRole('status', { name: '差异审阅准备状态' })).toBeVisible()
  await act(async () => { release(ctx.row) })
  expect(await screen.findByRole('region', { name: '工作流快照审阅' })).toBeVisible()
  expect(screen.queryByRole('status', { name: '差异审阅准备状态' })).not.toBeInTheDocument()
  for (const document of screen.getAllByText(ctx.row.specJson)) expect(document).toBeVisible()
  expect(screen.getByRole('button', { name: '确认最终文档' })).toBeDisabled()
  expect(ctx.session.state.versions.draft.files).toEqual(ctx.files)
  expect(useEditorStore.getState().spec).toEqual(ctx.db)
  expectNoWrites()
})
it('real adapter times out a pending row read, exposes the error and can retry without another save', async () => {
  const ctx = await setup()
  vi.useFakeTimers()
  vi.mocked(fetchJson).mockImplementationOnce(() => new Promise(() => {}) as never)
  click('核对数据库和 Git 差异…')
  await act(async () => { await vi.advanceTimersByTimeAsync(UNIFIED_SAVE_TIMEOUT_MS) })
  vi.useRealTimers()
  expect(await screen.findByText(/无法准备审阅：读取数据库草稿超时/)).toBeVisible()
  expect(screen.queryByRole('status', { name: '差异审阅准备状态' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '核对数据库和 Git 差异…' })).toBeEnabled()
  click('核对数据库和 Git 差异…')
  expect(await screen.findByRole('region', { name: '工作流快照审阅' })).toBeVisible()
  expect(ctx.session.state.versions.draft.files).toEqual(ctx.files)
  expectNoWrites()
})
it('real adapter shows an editor change during preparation instead of leaving a pending or usable stale review', async () => {
  const ctx = await setup()
  let release!: (value: unknown) => void
  vi.mocked(fetchJson).mockImplementationOnce(() => new Promise(resolve => { release = resolve }) as never)
  click('核对数据库和 Git 差异…')
  act(() => useEditorStore.getState().updateSpecField('title', 'Later edit'))
  await act(async () => { release(ctx.row) })
  await waitFor(() => expect(screen.getByText(/无法准备审阅：.*编辑内容或会话已变化/)).toBeVisible())
  expect(screen.queryByRole('status', { name: '差异审阅准备状态' })).not.toBeInTheDocument()
  expect(screen.queryByRole('region', { name: '工作流快照审阅' })).not.toBeInTheDocument()
  expect(useEditorStore.getState().spec?.title).toBe('Later edit')
  expectNoWrites()
})
it('keeps direct authored content and missing legacy fields raw in both review documents', async () => {
  const ctx = await setup({ version: undefined, content: 'User authored text', custom: { keep: true } })
  expect(packWorkflowBaseline(ctx.files, ctx.id).spec).toEqual(ctx.git)
  expect(packWorkflowBaseline(ctx.files, ctx.id).spec).not.toHaveProperty('version')
  click('核对数据库和 Git 差异…')
  expect(await screen.findByRole('region', { name: '工作流快照审阅' })).toBeVisible()
  for (const document of screen.getAllByText(ctx.row.specJson)) expect(document).toBeVisible()
  expect(useEditorStore.getState()._databaseSpec).toEqual(ctx.db)
  expectNoWrites()
})
