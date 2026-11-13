import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from '../../editor/store'
import { workflowSpecSignature } from '../../services/workflow-ai-draft'

const mocks = vi.hoisted(() => ({ workflow: vi.fn(), history: vi.fn().mockResolvedValue({ history: [], active: null }) }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: (id: string) => ({
    data: id ? mocks.workflow(id).data : undefined,
    databaseSpec: id ? mocks.workflow(id).data : undefined,
    activeDeployNumber: null,
    isLoading: false,
    error: null,
  }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }),
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
vi.mock('../../editor/usePackCanvasSession', () => ({ usePackCanvasSession: () => ({ saving: false, error: null, versionError: null, baselineComparison: 'same', save: vi.fn().mockResolvedValue(null) }) }))
vi.mock('../useEditorMergeReview', () => ({ useEditorMergeReview: () => ({ mergeAdapter: null, visibleMerge: null, mergeCurrent: null, wantsReview: false, preparing: false, openMergeReview: vi.fn(), cancelMergeReview: vi.fn(), clearMergeReview: vi.fn() }) }))
vi.mock('../../components/WorkflowAiDrawer', () => ({ default: ({ open, spec, onApply }) => open ? (
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
) : null }))

import Editor from '../Editor'

const source: WorkflowSpec = {
  id: 'synthetic-ai-editor', version: '1', title: 'Synthetic AI editor',
  nodes: [{ id: 'analysis', title: '分析', executor: { type: 'embedded-agent' } }],
}

beforeEach(() => {
  vi.clearAllMocks()
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
