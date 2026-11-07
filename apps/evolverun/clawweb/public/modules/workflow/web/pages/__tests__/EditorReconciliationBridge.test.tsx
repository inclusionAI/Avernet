import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { ReconciliationContext } from '../../api/workflowSnapshotReconciliation'

const mocks = vi.hoisted(() => ({ adapter: null as any, current: null as ReconciliationContext | null, history: vi.fn(), fetch: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: { id: 'review-wf', title: 'DB draft', version: '1', nodes: [] }, databaseSpec: { id: 'review-wf', title: 'DB draft', version: '1', nodes: [] }, isLoading: false }),
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
import { UNIFIED_SAVE_TIMEOUT_MS } from '../../api/workflowUnifiedSave'
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { assertWorkflowReconciliation, createWorkflowReconciliationOwner, workflowReconciliationIsCurrent } from '../../api/workflowSnapshotReconciliation'

const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))
function acknowledge() {
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON'))
  fireEvent.click(screen.getByLabelText('我已审阅数据库原始文档'))
  fireEvent.click(screen.getByLabelText('我已审阅 Git 原始 YAML'))
  fireEvent.click(screen.getByLabelText('我已审阅最终文档及其他文件变更，理解 YAML 将被重新生成'))
}
beforeEach(() => {
  vi.resetAllMocks(); useEditorStore.getState().reset({ force: true })
  mocks.history.mockResolvedValue({ active: null, history: [] })
  const db = { id: 'review-wf', title: 'DB draft', version: '1', nodes: [] }, git = { ...db, title: 'old Git' }
  const yaml = stringify(git), script = { path: 'scripts/run', mode: '100755' as const, contentBase64: btoa('echo keep\n') }
  const files = [{ path: 'workflow.yaml', mode: '100644' as const, contentBase64: btoa(yaml) }, script]
  mocks.current = { workflowId: 'review-wf', packId: 'pack', sourceVersion: 'draft', sourceCommit: 'a'.repeat(40),
    expectedSavedHead: 'a'.repeat(40), expectedDraftToken: 'b'.repeat(64), workflowPath: 'workflow.yaml', dbSpecJson: JSON.stringify(db), gitYaml: yaml,
    candidateSpecJson: JSON.stringify(db), changes: [], filesBefore: files, filesAfter: structuredClone(files), revision: 'session:1' }
  const owner = createWorkflowReconciliationOwner()
  mocks.adapter = { error: null, versionError: null, saving: false, loading: false, canWrite: false,
    save: vi.fn().mockRejectedValue(new Error('Git 工作流 YAML 与数据库基线不一致或不可比较；当前安全协议要求先合并并核对，未发送统一保存。')),
    prepareReconciliation: vi.fn(async () => owner.open(mocks.current!)),
    currentReconciliation: vi.fn(() => mocks.current!), cancelReconciliation: vi.fn(() => owner.close()),
    saveReconciled: vi.fn(async approval => {
      assertWorkflowReconciliation(approval, mocks.current!, approval.specJson)
      return { mode: 'unified', dbSaved: true, gitSaved: true, commit: 'c'.repeat(40) }
    }),
  }
})
afterEach(() => { cleanup(); vi.useRealTimers(); useEditorStore.getState().reset({ force: true }); vi.restoreAllMocks() })
async function showConflict() {
  const view = render(<Editor embedded initialWorkflowId="review-wf" />)
  await waitFor(() => expect(useEditorStore.getState().spec?.id).toBe('review-wf'))
  click('保存'); await screen.findByText(/Git 工作流 YAML 与数据库基线不一致或不可比较/)
  return view
}
async function openReview() {
  const view = await showConflict()
  click('核对数据库和 Git 差异…'); await screen.findByRole('region', { name: '工作流快照审阅' })
  return view
}
it('does not offer or fake reconciliation while the hook contract is unavailable', async () => {
  delete mocks.adapter.prepareReconciliation
  render(<Editor embedded initialWorkflowId="review-wf" />)
  await waitFor(() => expect(useEditorStore.getState().spec?.id).toBe('review-wf'))
  click('保存'); expect(await screen.findByText(/差异审阅保存能力尚不可用/)).toBeVisible()
  expect(screen.queryByRole('button', { name: '核对数据库和 Git 差异…' })).not.toBeInTheDocument()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})
it('shows the selected YAML and script before submitting only an explicit bound approval', async () => {
  await openReview()
  expect(screen.getByText(/数据库草稿是已持久化的最新真值/)).toBeVisible()
  expect(within(screen.getByRole('region', { name: '候选文件: scripts/run' })).getByText('echo keep')).toBeVisible()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
  acknowledge()
  expect(screen.getByRole('region', { name: '将保存的 YAML 预览' })).toHaveTextContent('DB draft')
  click('确认最终文档'); await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)
  expect(mocks.adapter.saveReconciled).toHaveBeenCalledOnce()
  expect(mocks.adapter.saveReconciled.mock.calls[0][0].specJson).toBe(mocks.current!.dbSpecJson)
  expect(mocks.current!.filesAfter.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('echo keep\n'))
})
it('rejects a stale DB token immediately before submit and keeps drafts untouched', async () => {
  await openReview(); acknowledge()
  mocks.current = { ...mocks.current!, expectedDraftToken: 'c'.repeat(64), revision: 'session:2' }
  click('确认最终文档'); await screen.findByText(/审阅后基线或草稿已变化|合并确认缺失或确认后内容已变化/)
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec?.title).toBe('DB draft')
  expect(mocks.current.filesAfter.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('echo keep\n'))
})
it('cancel invalidates the hook review and never sends the approval', async () => {
  await openReview(); click('取消')
  expect(mocks.adapter.cancelReconciliation).toHaveBeenCalledOnce()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
  expect(screen.queryByRole('region', { name: '工作流快照审阅' })).not.toBeInTheDocument()
})

it('shows real pending progress, blocks duplicate preparation and finishes when the review resolves', async () => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  const prepare = mocks.adapter.prepareReconciliation.getMockImplementation()!
  mocks.adapter.prepareReconciliation.mockImplementation(async () => { await pending; return prepare() })
  await showConflict()
  click('核对数据库和 Git 差异…')
  expect(screen.getByRole('status', { name: '差异审阅准备状态' })).toHaveTextContent('正在核对数据库和 Git 差异')
  expect(screen.getByRole('button', { name: /核对数据库和 Git 差异/ })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: /核对数据库和 Git 差异/ }))
  expect(mocks.adapter.prepareReconciliation).toHaveBeenCalledOnce()
  await act(async () => { release() })
  expect(await screen.findByRole('region', { name: '工作流快照审阅' })).toBeVisible()
  expect(screen.queryByRole('status', { name: '差异审阅准备状态' })).not.toBeInTheDocument()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})
it('shows preparation failures, ends pending progress and permits retry without another save', async () => {
  mocks.adapter.prepareReconciliation.mockRejectedValueOnce(new Error('API 503: {"message":"数据库暂时不可用"}'))
  await showConflict(); click('核对数据库和 Git 差异…')
  expect(await screen.findByText('无法准备审阅：数据库暂时不可用')).toBeVisible()
  expect(screen.queryByRole('status', { name: '差异审阅准备状态' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '核对数据库和 Git 差异…' })).toBeEnabled()
  click('核对数据库和 Git 差异…')
  expect(await screen.findByRole('region', { name: '工作流快照审阅' })).toBeVisible()
  expect(mocks.adapter.prepareReconciliation).toHaveBeenCalledTimes(2)
  expect(mocks.adapter.save).toHaveBeenCalledOnce()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})
it('closes the prepared review if the current baseline check throws and keeps retry visible', async () => {
  mocks.adapter.currentReconciliation.mockImplementationOnce(() => { throw new Error('审阅基线不可用') })
  await showConflict(); click('核对数据库和 Git 差异…')
  expect(await screen.findByText('无法准备审阅：审阅基线不可用')).toBeVisible()
  const review = await mocks.adapter.prepareReconciliation.mock.results[0].value
  expect(mocks.adapter.cancelReconciliation).toHaveBeenCalledWith(review)
  expect(screen.queryByRole('region', { name: '工作流快照审阅' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '核对数据库和 Git 差异…' })).toBeEnabled()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})
it('can reopen a cancelled review without resubmitting the failed save', async () => {
  await openReview(); click('取消')
  click('核对数据库和 Git 差异…')
  expect(await screen.findByRole('region', { name: '工作流快照审阅' })).toBeVisible()
  expect(mocks.adapter.prepareReconciliation).toHaveBeenCalledTimes(2)
  expect(mocks.adapter.save).toHaveBeenCalledOnce()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})
it('discards a late preparation after an editor-session replacement without applying or saving it', async () => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  const prepare = mocks.adapter.prepareReconciliation.getMockImplementation()!
  mocks.adapter.prepareReconciliation.mockImplementation(async () => { await pending; return prepare() })
  await showConflict(); click('核对数据库和 Git 差异…')
  act(() => {
    const state = useEditorStore.getState()
    state.loadSpec(state.spec!, state._databaseSpec)
  })
  await act(async () => { release() })
  await waitFor(() => expect(mocks.adapter.cancelReconciliation).toHaveBeenCalledOnce())
  expect(screen.queryByRole('region', { name: '工作流快照审阅' })).not.toBeInTheDocument()
  expect(screen.queryByText(/无法准备审阅/)).not.toBeInTheDocument()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})

it('clears the retained review intent after a later successful ordinary save', async () => {
  await openReview(); click('取消')
  mocks.adapter.save.mockResolvedValueOnce({ mode: 'unified', dbSaved: true, gitSaved: true, commit: 'd'.repeat(40) })
  click('保存')
  expect(await screen.findByText(/数据库和草稿\(wf\)分支已确认保存/)).toBeVisible()
  expect(screen.queryByRole('button', { name: '核对数据库和 Git 差异…' })).not.toBeInTheDocument()
})
it('shows a safe unavailable fallback if the optional adapter disappears after opening a review', async () => {
  const view = await openReview()
  delete mocks.adapter.saveReconciled
  view.rerender(<Editor embedded initialWorkflowId="review-wf" />)
  expect(await screen.findByText(/差异审阅保存能力尚不可用/)).toBeVisible()
  expect(screen.queryByRole('region', { name: '工作流快照审阅' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '确认最终文档' })).not.toBeInTheDocument()
})
it('keeps the preparation failure visible even if cancelling the rejected review throws', async () => {
  mocks.adapter.currentReconciliation.mockImplementationOnce(() => { throw new Error('基线已变化') })
  mocks.adapter.cancelReconciliation.mockImplementationOnce(() => { throw new Error('取消回调失败') })
  await showConflict(); click('核对数据库和 Git 差异…')
  expect(await screen.findByText('无法准备审阅：基线已变化')).toBeVisible()
  expect(screen.queryByRole('status', { name: '差异审阅准备状态' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '核对数据库和 Git 差异…' })).toBeEnabled()
})

it('bounds a nonresponding adapter, retains retry and cancels late review without unlocking a newer request', async () => {
  await showConflict()
  vi.useFakeTimers()
  let oldRelease!: (review: any) => void, newRelease!: (review: any) => void
  const oldReview = createWorkflowReconciliationOwner().open(mocks.current!)
  const newReview = createWorkflowReconciliationOwner().open(mocks.current!)
  mocks.adapter.cancelReconciliation.mockImplementation(() => {})
  mocks.adapter.prepareReconciliation
    .mockImplementationOnce(() => new Promise(resolve => { oldRelease = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { newRelease = resolve }))
  click('核对数据库和 Git 差异…')
  await act(async () => { await vi.advanceTimersByTimeAsync(UNIFIED_SAVE_TIMEOUT_MS) })
  expect(screen.getByRole('alert')).toHaveTextContent(/审阅.*超时/)
  expect(screen.queryByRole('status', { name: '差异审阅准备状态' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '核对数据库和 Git 差异…' })).toBeEnabled()
  click('核对数据库和 Git 差异…')
  await act(async () => { oldRelease(oldReview) })
  expect(mocks.adapter.cancelReconciliation).toHaveBeenCalledWith(oldReview)
  expect(workflowReconciliationIsCurrent(oldReview, mocks.current!)).toBe(false)
  expect(screen.getByRole('status', { name: '差异审阅准备状态' })).toBeVisible()
  expect(screen.getByRole('button', { name: '核对数据库和 Git 差异…' })).toBeDisabled()
  expect(screen.getByRole('button', { name: /^(保存|正在保存…)$/ })).toBeDisabled()
  await act(async () => { newRelease(newReview) })
  expect(screen.getByRole('region', { name: '工作流快照审阅' })).toBeVisible()
  expect(mocks.adapter.saveReconciled).not.toHaveBeenCalled()
})
it('cancels a superseded preparation without letting its late completion unlock the replacement session', async () => {
  await showConflict()
  let oldRelease!: (review: any) => void, newRelease!: (review: any) => void
  const oldReview = createWorkflowReconciliationOwner().open(mocks.current!)
  const newReview = createWorkflowReconciliationOwner().open(mocks.current!)
  mocks.adapter.cancelReconciliation.mockImplementation(() => {})
  mocks.adapter.prepareReconciliation
    .mockImplementationOnce(() => new Promise(resolve => { oldRelease = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { newRelease = resolve }))
  click('核对数据库和 Git 差异…')
  act(() => { const state = useEditorStore.getState(); state.loadSpec(state.spec!, state._databaseSpec) })
  expect(screen.queryByRole('status', { name: '差异审阅准备状态' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
  click('保存')
  await waitFor(() => expect(mocks.adapter.save).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByRole('button', { name: '核对数据库和 Git 差异…' })).toBeEnabled())
  click('核对数据库和 Git 差异…')
  await act(async () => { oldRelease(oldReview) })
  expect(mocks.adapter.cancelReconciliation).toHaveBeenCalledWith(oldReview)
  expect(workflowReconciliationIsCurrent(oldReview, mocks.current!)).toBe(false)
  expect(screen.getByRole('status', { name: '差异审阅准备状态' })).toBeVisible()
  expect(screen.getByRole('button', { name: /^(保存|正在保存…)$/ })).toBeDisabled()
  await act(async () => { newRelease(newReview) })
  expect(screen.getByRole('region', { name: '工作流快照审阅' })).toBeVisible()
})
