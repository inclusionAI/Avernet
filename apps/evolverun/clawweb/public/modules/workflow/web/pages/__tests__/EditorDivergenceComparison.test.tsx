import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { ReconciliationContext } from '../../api/workflowSnapshotReconciliation'

const mocks = vi.hoisted(() => ({ adapter: null as any, current: null as ReconciliationContext | null, history: vi.fn(), fetch: vi.fn(), dbReceipt: null as any, gitSnapshot: null as any }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: { id: 'review-wf', title: 'DB draft', version: '1', nodes: [{ id: 'start', title: 'Start', phase: 'execute', executor: { type: 'done' } }] }, databaseSpec: { id: 'review-wf', title: 'DB draft', version: '1', nodes: [{ id: 'start', title: 'Start', phase: 'execute', executor: { type: 'done' } }] }, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }), useFacadeBindings: () => ({ data: [] }),
}))
vi.mock('../../editor/usePackCanvasSession', () => ({
  usePackCanvasSession: () => mocks.adapter,
  PackCanvasPartialSaveError: class PackCanvasPartialSaveError extends Error { workflowId = 'review-wf' },
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetch,
  api: { workflows: { getHistory: mocks.history, getDeploySnapshot: vi.fn() } } }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/ScriptFilesTab', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { validateWorkflowSnapshotRequest } from '../../api/validateWorkflowSnapshotRequest'
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { parseWorkflowYaml } from '../../editor/yamlDraft'
import { assertWorkflowReconciliation, createWorkflowReconciliationOwner } from '../../api/workflowSnapshotReconciliation'

const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))
beforeEach(() => {
  vi.resetAllMocks(); useEditorStore.getState().reset({ force: true })
  mocks.history.mockResolvedValue({ active: null, history: [] })
  const db = { id: 'review-wf', title: 'DB draft', version: '1', nodes: [{ id: 'start', title: 'Start', phase: 'execute', executor: { type: 'done' } }] }, git = { ...db, title: 'old Git' }
  expect(validateWorkflowSnapshotRequest(JSON.stringify(parseWorkflowYaml(stringify(git))), git.id)).toEqual(git)
  const yaml = stringify(git), script = { path: 'scripts/run', mode: '100755' as const, contentBase64: btoa('echo keep\n') }
  const files = [{ path: 'workflow.pack.yaml', mode: '100644' as const, contentBase64: btoa(stringify({ id: 'pack', workflows: [{ id: 'review-wf', file: 'workflow.yaml' }] })) },
    { path: 'workflow.yaml', mode: '100644' as const, contentBase64: btoa(yaml) }, script]
  mocks.current = { workflowId: 'review-wf', packId: 'pack', sourceVersion: 'draft', sourceCommit: 'a'.repeat(40),
    expectedSavedHead: 'a'.repeat(40), expectedDraftToken: 'b'.repeat(64), workflowPath: 'workflow.yaml', dbSpecJson: JSON.stringify(db), gitYaml: yaml,
    candidateSpecJson: JSON.stringify(db), changes: [], filesBefore: files, filesAfter: structuredClone(files), revision: 'session:1' }
  mocks.dbReceipt = { workflowId: 'review-wf', packId: 'pack', specJson: JSON.stringify(db), draftToken: 'b'.repeat(64) }
  mocks.gitSnapshot = { workflowId: 'review-wf', packId: 'pack', baseRef: 'origin/wf/review-wf', baseCommit: 'a'.repeat(40), draftHead: 'a'.repeat(40), canWrite: true, versionFiles: structuredClone(files) }
  mocks.fetch.mockImplementation(async (url: string) => {
    if (url === '/api/workflows/review-wf/editor-draft') return structuredClone(mocks.dbReceipt)
    if (url === `/api/workflows/review-wf/pack-version?version=${mocks.current!.sourceVersion}`) return structuredClone(mocks.gitSnapshot)
    throw new Error(`Unexpected fetchJson request: ${url}`)
  })
  const owner = createWorkflowReconciliationOwner()
  mocks.adapter = { error: null, versionError: null, saving: false, loading: false, canWrite: false, baselineComparison: 'different',
    save: vi.fn().mockResolvedValue({ mode: 'unified', dbSaved: true, gitSaved: true, commit: 'c'.repeat(40) }),
    prepareReconciliation: vi.fn(async () => owner.open(mocks.current!)),
    currentReconciliation: vi.fn(() => {
      const current = mocks.current!, live = useEditorStore.getState().spec
      const candidate = JSON.stringify(live)
      return live && candidate !== current.candidateSpecJson ? { ...current, candidateSpecJson: candidate, revision: `${current.revision}:local-edit` } : current
    }), cancelReconciliation: vi.fn(() => owner.close()),
    saveReconciled: vi.fn(async approval => {
      assertWorkflowReconciliation(approval, mocks.current!, approval.specJson)
      return { mode: 'unified', dbSaved: true, gitSaved: true, commit: 'c'.repeat(40) }
    }),
  }
})
afterEach(() => { cleanup(); vi.useRealTimers(); useEditorStore.getState().reset({ force: true }); vi.restoreAllMocks() })
async function openComparison() {
  const view = render(<Editor embedded initialWorkflowId="review-wf" />)
  await waitFor(() => expect(useEditorStore.getState().spec?.id).toBe('review-wf'))
  click('查看差异')
  await screen.findByRole('dialog', { name: '对比数据库和 Git 工作流' })
  return view
}
const dialog = () => screen.getByRole('dialog', { name: '对比数据库和 Git 工作流' })
const gitChoice = () => within(dialog()).getByRole('radio', { name: /使用 Git YAML/ })
const dbChoice = () => within(dialog()).getByRole('radio', { name: /使用数据库工作流.*默认/ })

it('warns about a difference without blocking save or deployment and defaults to the existing candidate', async () => {
  await openComparison()
  expect(dbChoice()).toBeChecked(); expect(gitChoice()).toBeEnabled()
  expect(within(dialog()).getByRole('region', { name: /数据库工作流.*当前编辑.*内容/ })).toHaveTextContent('DB draft')
  expect(within(dialog()).getByRole('region', { name: 'Git 原始 YAML内容' })).toHaveTextContent('old Git')
  expect(useEditorStore.getState().spec?.title).toBe('DB draft')
  expect(mocks.adapter.save).not.toHaveBeenCalled()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
  click('保留当前内容')
  expect(screen.queryByRole('dialog', { name: '对比数据库和 Git 工作流' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
  expect(screen.getByRole('button', { name: '部署', exact: true })).toBeEnabled()
})

it('requires confirmation before replacing local edits with Git; returning leaves them intact', async () => {
  render(<Editor embedded initialWorkflowId="review-wf" />)
  await waitFor(() => expect(useEditorStore.getState().spec?.id).toBe('review-wf'))
  act(() => useEditorStore.getState().updateSpecField('title', 'Local edit'))
  mocks.current = { ...mocks.current!, candidateSpecJson: JSON.stringify(useEditorStore.getState().spec) }
  click('查看差异'); await screen.findByRole('dialog', { name: '对比数据库和 Git 工作流' })
  fireEvent.click(gitChoice()); click('使用 Git 内容')
  const confirm = screen.getByRole('alertdialog', { name: '确认替换当前编辑' })
  expect(confirm).toHaveTextContent(/替换.*未保存/)
  expect(useEditorStore.getState().spec?.title).toBe('Local edit')
  fireEvent.click(within(confirm).getByRole('button', { name: '返回' }))
  expect(useEditorStore.getState().spec?.title).toBe('Local edit')
  click('使用 Git 内容')
  mocks.fetch.mockClear() // Only reads started after the explicit overwrite confirmation count.
  fireEvent.click(within(screen.getByRole('alertdialog', { name: '确认替换当前编辑' })).getByRole('button', { name: '确认替换' }))
  await waitFor(() => expect(useEditorStore.getState().spec?.title).toBe('old Git'))
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(mocks.fetch).toHaveBeenCalledWith('/api/workflows/review-wf/editor-draft', expect.objectContaining({ method: 'GET' }))
  expect(mocks.fetch.mock.calls.some(([url]) => url === '/api/workflows/review-wf/pack-version?version=draft')).toBe(true)
  expect(mocks.adapter.save).not.toHaveBeenCalled(); expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})

it('closing the comparison leaves the current candidate unchanged', async () => {
  await openComparison(); fireEvent.click(gitChoice()); click('使用 Git 内容')
  fireEvent.click(within(dialog()).getByRole('button', { name: '关闭对比' }))
  expect(screen.queryByRole('dialog', { name: '对比数据库和 Git 工作流' })).not.toBeInTheDocument()
  expect(useEditorStore.getState().spec?.title).toBe('DB draft')
  expect(mocks.adapter.save).not.toHaveBeenCalled()
})

it.each([
  ['invalid YAML', 'id: [unterminated'],
  ['wrong identity', stringify({ id: 'other', title: 'wrong workflow', version: '1', nodes: [{ id: 'start', title: 'Start', phase: 'execute', executor: { type: 'done' } }] })],
  ['unsafe number', `${stringify({ id: 'review-wf', title: 'unsafe', version: '1', nodes: [{ id: 'start', title: 'Start', phase: 'execute', executor: { type: 'done' } }] })}value: 9007199254740993\n`],
])('disables Git for %s while leaving the DB candidate available', async (reason, yaml) => {
  if (reason === 'wrong identity') {
    const spec = parseWorkflowYaml(yaml)
    expect(validateWorkflowSnapshotRequest(JSON.stringify(spec), 'other')).toEqual(spec)
    expect(() => validateWorkflowSnapshotRequest(JSON.stringify(spec), 'review-wf')).toThrow(/身份/)
  }
  mocks.adapter.baselineComparison = 'uncomparable'
  const old = mocks.current!
  const files = old.filesBefore.map(file => file.path === old.workflowPath ? { ...file, contentBase64: btoa(yaml) } : file)
  mocks.current = { ...old, gitYaml: yaml, filesBefore: files, filesAfter: structuredClone(files) }
  await openComparison()
  expect(gitChoice()).toBeDisabled(); expect(dbChoice()).toBeChecked(); expect(dbChoice()).toBeEnabled()
  expect(within(dialog()).getByRole('status')).toHaveTextContent(/Git.*(?:无法|不能|安全)/)
  click('保留当前内容')
  expect(useEditorStore.getState().spec?.title).toBe('DB draft')
  expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
  expect(screen.getByRole('button', { name: '部署', exact: true })).toBeEnabled()
})

it('does not apply an outdated Git choice after the comparison baseline changes', async () => {
  const view = await openComparison()
  fireEvent.click(gitChoice()); click('使用 Git 内容')
  mocks.current = { ...mocks.current!, revision: 'session:2', expectedDraftToken: 'c'.repeat(64) }
  view.rerender(<Editor embedded initialWorkflowId="review-wf" />)
  const confirm = screen.getByRole('alertdialog', { name: '确认替换当前编辑' })
  expect(within(confirm).getByRole('button', { name: '确认替换' })).toBeDisabled()
  expect(within(dialog()).getByRole('alert')).toHaveTextContent(/变化|重新/)
  expect(useEditorStore.getState().spec?.title).toBe('DB draft')
  expect(mocks.adapter.save).not.toHaveBeenCalled(); expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})

it('disables selecting Git when the comparison became stale before a choice', async () => {
  const view = await openComparison()
  mocks.current = { ...mocks.current!, revision: 'session:2', expectedDraftToken: 'c'.repeat(64) }
  view.rerender(<Editor embedded initialWorkflowId="review-wf" />)
  expect(gitChoice()).toBeDisabled()
  expect(within(dialog()).getByRole('alert')).toHaveTextContent(/变化|重新/)
  expect(useEditorStore.getState().spec?.title).toBe('DB draft')
  expect(mocks.adapter.save).not.toHaveBeenCalled()
})

async function prepareGitOverwriteWithLocalEdit() {
  render(<Editor embedded initialWorkflowId="review-wf" />)
  await waitFor(() => expect(useEditorStore.getState().spec?.id).toBe('review-wf'))
  act(() => useEditorStore.getState().updateSpecField('title', 'Keep local edit'))
  mocks.current = { ...mocks.current!, candidateSpecJson: JSON.stringify(useEditorStore.getState().spec) }
  click('查看差异'); await screen.findByRole('dialog', { name: '对比数据库和 Git 工作流' })
  fireEvent.click(gitChoice()); click('使用 Git 内容')
  expect(screen.getByRole('alertdialog', { name: '确认替换当前编辑' })).toBeVisible()
}
function confirmGitOverwrite() {
  fireEvent.click(within(screen.getByRole('alertdialog', { name: '确认替换当前编辑' })).getByRole('button', { name: '确认替换' }))
}
function assertNoGitImport(title = 'Keep local edit') {
  expect(useEditorStore.getState().spec?.title).toBe(title)
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(mocks.adapter.save).not.toHaveBeenCalled()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
}

it('does not import Git when the freshly read DB CAS token changed', async () => {
  await prepareGitOverwriteWithLocalEdit()
  mocks.dbReceipt = { ...mocks.dbReceipt, draftToken: 'c'.repeat(64) }
  confirmGitOverwrite()
  await waitFor(() => expect(mocks.fetch.mock.calls.some(([url]) => url.endsWith('/editor-draft'))).toBe(true))
  expect(await screen.findByText(/^无法载入 Git 工作流：数据库或 Git 基线已变化/, { selector: '[role="alert"]' })).toBeVisible()
  assertNoGitImport()
})

it.each(['commit', 'workflow bytes', 'other file bytes', 'file mode', 'missing file'] as const)(
  'does not import Git when the freshly read Git snapshot has changed %s', async change => {
    await prepareGitOverwriteWithLocalEdit()
    const changed = structuredClone(mocks.gitSnapshot)
    if (change === 'commit') changed.baseCommit = changed.draftHead = 'c'.repeat(40)
    if (change === 'workflow bytes') changed.versionFiles.find((file: any) => file.path === 'workflow.yaml').contentBase64 = btoa('id: review-wf\ntitle: changed\n')
    if (change === 'other file bytes') changed.versionFiles.find((file: any) => file.path === 'scripts/run').contentBase64 = btoa('echo changed\n')
    if (change === 'file mode') changed.versionFiles.find((file: any) => file.path === 'scripts/run').mode = '100644'
    if (change === 'missing file') changed.versionFiles = changed.versionFiles.filter((file: any) => file.path !== 'scripts/run')
    mocks.gitSnapshot = changed
    confirmGitOverwrite()
    await waitFor(() => expect(mocks.fetch.mock.calls.some(([url]) => url.includes('/pack-version?version=draft'))).toBe(true))
    expect(await screen.findByText(/^无法载入 Git 工作流：数据库或 Git 基线已变化/, { selector: '[role="alert"]' })).toBeVisible()
    assertNoGitImport()
  },
)

it.each(['editor-draft', 'pack-version?version=draft'])('keeps edits made while %s re-read is pending instead of importing Git', async endpoint => {
  await prepareGitOverwriteWithLocalEdit()
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  const original = mocks.fetch.getMockImplementation()!
  mocks.fetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.includes(endpoint)) await pending
    return original(url, init)
  })
  confirmGitOverwrite()
  await waitFor(() => expect(mocks.fetch.mock.calls.some(([url]) => url.includes(endpoint))).toBe(true))
  act(() => useEditorStore.getState().updateSpecField('title', 'Newer local edit'))
  await act(async () => { release(); await pending })
  expect(await screen.findByText(/^无法载入 Git 工作流：对比期间工作流或文件已变化/, { selector: '[role="alert"]' })).toBeVisible()
  assertNoGitImport('Newer local edit')
})

it('keeps the editor unchanged if a remote re-read fails', async () => {
  await prepareGitOverwriteWithLocalEdit()
  const original = mocks.fetch.getMockImplementation()!
  mocks.fetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.includes('/pack-version?version=draft')) throw new Error('snapshot read failed')
    return original(url, init)
  })
  confirmGitOverwrite()
  await waitFor(() => expect(mocks.fetch.mock.calls.some(([url]) => url.includes('/pack-version?version=draft'))).toBe(true))
  expect(await screen.findByText(/无法载入|snapshot read failed/, {}, { timeout: 5_000 })).toBeVisible()
  assertNoGitImport()
})

it('re-reads the current tag rather than the draft when comparing current', async () => {
  mocks.current = { ...mocks.current!, sourceVersion: 'current', expectedSavedHead: null }
  mocks.gitSnapshot = { ...mocks.gitSnapshot, baseRef: 'refs/tags/deploy/review-wf/#1', draftHead: null }
  await prepareGitOverwriteWithLocalEdit()
  confirmGitOverwrite()
  await waitFor(() => expect(useEditorStore.getState().spec?.title).toBe('old Git'))
  expect(mocks.fetch.mock.calls.some(([url]) => url === '/api/workflows/review-wf/editor-draft')).toBe(true)
  expect(mocks.fetch.mock.calls.some(([url]) => url === '/api/workflows/review-wf/pack-version?version=current')).toBe(true)
  expect(mocks.fetch.mock.calls.some(([url]) => url === '/api/workflows/review-wf/pack-version?version=draft')).toBe(false)
  expect(mocks.adapter.save).not.toHaveBeenCalled()
})

it.each(['editor-draft', 'pack-version?version=draft'])('waits for the %s re-read before replacing the editor', async endpoint => {
  await prepareGitOverwriteWithLocalEdit()
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  const original = mocks.fetch.getMockImplementation()!
  mocks.fetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.includes(endpoint)) await pending
    return original(url, init)
  })
  confirmGitOverwrite()
  await waitFor(() => expect(mocks.fetch.mock.calls.some(([url]) => url.includes(endpoint))).toBe(true))
  assertNoGitImport()
  await act(async () => { release(); await pending })
  await waitFor(() => expect(useEditorStore.getState().spec?.title).toBe('old Git'))
  expect(mocks.fetch.mock.calls.some(([url]) => url === '/api/workflows/review-wf/editor-draft')).toBe(true)
  expect(mocks.fetch.mock.calls.some(([url]) => url === '/api/workflows/review-wf/pack-version?version=draft')).toBe(true)
  expect(mocks.adapter.save).not.toHaveBeenCalled(); expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})
