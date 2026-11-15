import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from '../../editor/store'
import { workflowSpecSignature } from '../../services/workflow-ai-draft'

const mocks = vi.hoisted(() => ({ drawer: vi.fn(), packSaving: false, deploy: vi.fn(), workflow: vi.fn(), history: vi.fn().mockResolvedValue({ history: [], active: null }) }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: (id: string) => ({
    data: id ? mocks.workflow(id).data : undefined,
    databaseSpec: id ? mocks.workflow(id).data : undefined,
    activeDeployNumber: null,
    isLoading: false,
    error: null,
  }),
  useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { getHistory: mocks.history } } }))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'synthetic-test-user' }) }))
vi.mock('@xyflow/react', () => ({ ReactFlowProvider: ({ children }) => children }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => <div>测试画布</div> }))
vi.mock('../../components/NodePalette', () => ({ default: () => <div>测试节点库</div> }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => <div>测试流程设置</div> }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/ScriptFilesTab', () => ({ default: () => null }))
vi.mock('../../editor/usePackCanvasSession', () => ({ usePackCanvasSession: () => ({ saving: mocks.packSaving, error: null, versionError: null, baselineComparison: 'same', save: vi.fn().mockResolvedValue(null) }) }))
vi.mock('../useEditorMergeReview', () => ({ useEditorMergeReview: () => ({ mergeAdapter: null, visibleMerge: null, mergeCurrent: null, wantsReview: false, preparing: false, openMergeReview: vi.fn(), cancelMergeReview: vi.fn(), clearMergeReview: vi.fn() }) }))
vi.mock('../../components/WorkflowAiDrawer', () => ({ default: (props) => {
  mocks.drawer(props)
  const { open, spec, onApply } = props
  return open ? (
  <aside aria-label="AI 工作流助手">
    <button onClick={() => onApply({
      workflowId: spec.id,
      baseSignature: workflowSpecSignature(spec),
      spec: { ...spec, title: 'AI 修改标题' },
      changes: [], metadataChanges: ['title'], removedNodeIds: [], warnings: [], yaml: '', summary: '',
    })}>应用测试草稿</button>
    <button onClick={() => onApply({ workflowId: spec.id, baseSignature: 'stale', spec: { ...spec, title: '过期标题' } })}>应用过期草稿</button>
    <button onClick={() => onApply({ workflowId: 'other', baseSignature: workflowSpecSignature(spec), spec: { ...spec, id: 'other' } })}>应用跨工作流草稿</button>
  </aside>
) : null } }))

import Editor from '../Editor'

const source: WorkflowSpec = {
  id: 'synthetic-ai-editor', version: '1', title: 'Synthetic AI editor',
  nodes: [{ id: 'analysis', title: '分析', executor: { type: 'embedded-agent' } }],
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.packSaving = false
  mocks.history.mockResolvedValue({ history: [], active: null })
  useEditorStore.getState().reset({ force: true })
  mocks.workflow.mockReturnValue({ data: source, isLoading: false })
})

it('applies a reviewed draft only to current editor memory', async () => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  fireEvent.click(await screen.findByRole('button', { name: /AI 生成/ }))
  fireEvent.click(screen.getByText('应用测试草稿'))
  expect(useEditorStore.getState().spec?.title).toBe('AI 修改标题')
  expect(useEditorStore.getState().isDirty).toBe(true)
})

it.each(['应用过期草稿', '应用跨工作流草稿'])('rejects unsafe drafts: %s', async (button) => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  fireEvent.click(await screen.findByRole('button', { name: /AI 修改/ }))
  fireEvent.click(screen.getByText(button))
  expect(useEditorStore.getState().spec?.id).toBe(source.id)
  expect(useEditorStore.getState().spec?.title).toBe(source.title)
  expect(useEditorStore.getState().isDirty).toBe(false)
})

it('disables AI entry while an unapplied editor draft exists', async () => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  await screen.findByRole('button', { name: /AI 生成/ })
  act(() => useEditorStore.getState().setPendingEdit('yaml', 'synthetic unapplied YAML'))
  expect(screen.getByRole('button', { name: /AI 生成/ })).toBeDisabled()
  expect(screen.getByRole('button', { name: /AI 修改/ })).toBeDisabled()
})

function reviewedDraft(base: WorkflowSpec = useEditorStore.getState().spec!) {
  return { workflowId: base.id, baseSignature: workflowSpecSignature(base), spec: { ...base, title: 'Reviewed AI draft' },
    yaml: '', summary: 'Draft', changes: [], metadataChanges: ['title'], removedNodeIds: [], warnings: [] }
}

it('bases AI drafting on the applied-but-unsaved Editor document, not a fresh database source', async () => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  await screen.findByRole('button', { name: /AI 修改/ })
  act(() => useEditorStore.getState().updateSpecField('title', 'Applied local title'))
  fireEvent.click(screen.getByRole('button', { name: /AI 修改/ }))
  expect(mocks.drawer.mock.lastCall![0].spec.title).toBe('Applied local title')
  fireEvent.click(screen.getByText('应用测试草稿'))
  expect(useEditorStore.getState().spec!.title).toBe('AI 修改标题')
  expect(useEditorStore.getState()._databaseSpec!.title).toBe(source.title)
  expect(mocks.deploy).not.toHaveBeenCalled()
})

it.each(['yaml', 'node:analysis', 'workflow:config'])('rejects apply when a pending %s draft appears after preview', async key => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  fireEvent.click(await screen.findByRole('button', { name: /AI 修改/ }))
  const apply = mocks.drawer.mock.lastCall![0].onApply
  const draft = reviewedDraft()
  act(() => {
    useEditorStore.getState().setPendingEdit(key, 'unapplied changes')
    apply(draft)
  })
  expect(useEditorStore.getState().spec!.title).toBe(source.title)
  expect(screen.queryByText(/AI 草稿已通过校验并应用/)).not.toBeInTheDocument()
})

it('rejects a late apply after layout changes in the sidecar even when the YAML is identical', async () => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  fireEvent.click(await screen.findByRole('button', { name: /AI 修改/ }))
  const apply = mocks.drawer.mock.lastCall![0].onApply
  const draft = reviewedDraft()
  act(() => {
    useEditorStore.setState({ positions: { analysis: { x: 300, y: 400 } } })
    apply(draft)
  })
  expect(useEditorStore.getState().spec!.title).toBe(source.title)
  expect(useEditorStore.getState().positions.analysis).toEqual({ x: 300, y: 400 })
})

it('rejects a late apply from an older drawer session, including the same toolbar mode', async () => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  const toolbar = await screen.findByRole('button', { name: /AI 修改/ })
  fireEvent.click(toolbar)
  const apply = mocks.drawer.mock.lastCall![0].onApply
  const draft = reviewedDraft()
  fireEvent.click(toolbar)
  act(() => apply(draft))
  expect(useEditorStore.getState().spec!.title).toBe(source.title)
})

it('rejects a late apply from an older Editor session reloaded with identical YAML', async () => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  fireEvent.click(await screen.findByRole('button', { name: /AI 修改/ }))
  const apply = mocks.drawer.mock.lastCall![0].onApply
  const draft = reviewedDraft()
  act(() => {
    useEditorStore.getState().reset({ force: true })
    useEditorStore.getState().loadSpec(source)
    apply(draft)
  })
  expect(useEditorStore.getState().spec!.title).toBe(source.title)
})

it('blocks application during Pack saving even with an older callback', async () => {
  const view = render(<Editor embedded initialWorkflowId={source.id} />)
  fireEvent.click(await screen.findByRole('button', { name: /AI 修改/ }))
  const apply = mocks.drawer.mock.lastCall![0].onApply
  const draft = reviewedDraft()
  mocks.packSaving = true
  view.rerender(<Editor embedded initialWorkflowId={source.id} />)
  act(() => apply(draft))
  expect(useEditorStore.getState().spec!.title).toBe(source.title)
})

it('never labels an import rejected through store.actionError as applied success', async () => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  fireEvent.click(await screen.findByRole('button', { name: /AI 修改/ }))
  const originalImport = useEditorStore.getState().importYaml
  act(() => useEditorStore.setState({ importYaml: () => useEditorStore.getState().setActionError('拒绝导入：保留原文档') }))
  try {
    fireEvent.click(screen.getByText('应用测试草稿'))
    expect(screen.getAllByText('拒绝导入：保留原文档').length).toBeGreaterThan(0)
    expect(screen.queryByText(/AI 草稿已通过校验并应用/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('AI 工作流助手')).toBeInTheDocument()
    expect(useEditorStore.getState().spec!.title).toBe(source.title)
    expect(useEditorStore.getState().isDirty).toBe(false)
  } finally { act(() => useEditorStore.setState({ importYaml: originalImport })) }
})

it('preserves the workflow version and refuses changed identity on final apply', async () => {
  render(<Editor embedded initialWorkflowId={source.id} />)
  fireEvent.click(await screen.findByRole('button', { name: /AI 修改/ }))
  const apply = mocks.drawer.mock.lastCall![0].onApply
  const draft = reviewedDraft()
  act(() => apply({ ...draft, spec: { ...draft.spec, version: 'next' } }))
  expect(useEditorStore.getState().spec!.version).toBe(source.version)
  expect(screen.queryByText(/AI 草稿已通过校验并应用/)).not.toBeInTheDocument()
})
