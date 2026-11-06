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
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { assertWorkflowReconciliation, createWorkflowReconciliationOwner } from '../../api/workflowSnapshotReconciliation'

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
afterEach(() => { cleanup(); useEditorStore.getState().reset({ force: true }); vi.restoreAllMocks() })
async function openReview() {
  render(<Editor embedded initialWorkflowId="review-wf" />)
  await waitFor(() => expect(useEditorStore.getState().spec?.id).toBe('review-wf'))
  click('保存'); await screen.findByText(/Git 工作流 YAML 与数据库基线不一致或不可比较/)
  click('核对数据库和 Git 差异…'); await screen.findByRole('region', { name: '工作流快照审阅' })
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
  click('确认最终文档'); await screen.findByText(/数据库和 saved 分支已确认保存/)
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
