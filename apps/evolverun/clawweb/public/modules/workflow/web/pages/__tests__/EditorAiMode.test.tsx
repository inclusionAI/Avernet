import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from '../../editor/store'
import {
  generateWorkflowAiDraft,
  workflowSpecSignature,
  type WorkflowAiDraft,
  type WorkflowAiMode,
} from '../../services/workflow-ai-draft'

const mocks = vi.hoisted(() => ({
  workflow: vi.fn(),
  save: vi.fn(),
  history: vi.fn().mockResolvedValue({ history: [] }),
  http: vi.fn(() => { throw new Error('Unexpected HTTP request in Editor AI mode regression tests') }),
}))

vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: (id: string) => ({ data: id ? mocks.workflow(id).data : undefined, databaseSpec: id ? mocks.workflow(id).data : undefined, activeDeployNumber: null, isLoading: false, error: null }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useFacadeBindings: () => ({ data: [] }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({
  api: { workflows: { getHistory: mocks.history } },
  fetchJson: mocks.http,
}))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({
  getClientUser: () => ({ userId: 'synthetic-test-user' }),
}))
vi.mock('@xyflow/react', () => ({
  ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('../../components/EditorCanvas', () => ({ default: () => <div>测试画布</div> }))
vi.mock('../../components/NodePalette', () => ({ default: () => <div>测试节点库</div> }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/WorkflowConfigPanel', () => ({ default: () => <div>测试属性面板</div> }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
vi.mock('../../editor/usePackCanvasSession', () => ({ usePackCanvasSession: () => ({ saving: false, error: null, versionError: null, baselineComparison: 'same', save: vi.fn().mockResolvedValue(null) }) }))
vi.mock('../useEditorMergeReview', () => ({ useEditorMergeReview: () => ({ mergeAdapter: null, visibleMerge: null, mergeCurrent: null, wantsReview: false, preparing: false, openMergeReview: vi.fn(), cancelMergeReview: vi.fn(), clearMergeReview: vi.fn() }) }))

// Keep the real signature, drawer, preview and request-lifetime hook.
vi.mock('../../hooks/useWorkflowAiCapabilities', () => ({ useWorkflowAiCapabilities: () => ({ value: { available: true }, loading: false, retry: vi.fn() }) }))

vi.mock('../../services/workflow-ai-draft', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/workflow-ai-draft')>()
  return { ...actual, generateWorkflowAiDraft: vi.fn() }
})

import Editor from '../Editor'

const generate = vi.mocked(generateWorkflowAiDraft)
const source: WorkflowSpec = {
  id: 'synthetic-ai-mode-regression', version: '1', title: 'Synthetic AI mode regression',
  nodes: [{
    id: 'review', title: 'Synthetic review',
    executor: { type: 'embedded-agent', prompt: 'Original synthetic review requirement' },
  }],
}
const modeUi = {
  create: { toolbar: /AI 生成/, button: '增加能力', input: '描述想增加的能力' },
  modify: { toolbar: /^AI 修改$/, button: '修改现有流程', input: '描述对现有流程的修改' },
}

function makeDraft(prompt: string): WorkflowAiDraft {
  const spec: WorkflowSpec = {
    ...source,
    nodes: [{ ...source.nodes[0], executor: { ...source.nodes[0].executor, prompt } }],
  }
  return {
    workflowId: source.id,
    baseSignature: workflowSpecSignature(source),
    spec,
    yaml: stringify(spec),
    summary: `合成草稿：${prompt}`,
    changes: [{ kind: 'update', nodeId: 'review', title: 'Synthetic review', reason: prompt }],
    metadataChanges: [], removedNodeIds: [], warnings: [],
  }
}

function assistant() {
  return within(screen.getByRole('region', { name: 'AI 工作流助手' }))
}

function expectMode(mode: WorkflowAiMode) {
  const pane = assistant()
  for (const item of ['create', 'modify'] as const) {
    expect(pane.getByRole('button', { name: modeUi[item].button, exact: true }))
      .toHaveAttribute('aria-pressed', String(item === mode))
  }
  return pane.getByRole('textbox', { name: modeUi[mode].input, exact: true })
}

function expectFreshSession(mode: WorkflowAiMode) {
  expect(expectMode(mode)).toHaveValue('')
  expect(assistant().getByRole('button', { name: '生成 YAML 草稿' })).toBeDisabled()
  expect(screen.queryByRole('region', { name: 'AI 草稿预览' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '应用草稿到当前 Editor' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '取消生成' })).not.toBeInTheDocument()
  expect(useEditorStore.getState().spec).toEqual(source)
  expect(useEditorStore.getState().isDirty).toBe(false)
}

function submitRequirement(mode: WorkflowAiMode, prompt: string) {
  fireEvent.change(expectMode(mode), { target: { value: prompt } })
  fireEvent.click(assistant().getByRole('button', { name: '生成 YAML 草稿' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  generate.mockReset()
  useEditorStore.getState().reset()
  mocks.workflow.mockReturnValue({ data: source, isLoading: false })
  vi.stubGlobal('fetch', mocks.http)
})
afterEach(() => {
  cleanup()
  useEditorStore.getState().reset()
  vi.unstubAllGlobals()
  expect(mocks.http).not.toHaveBeenCalled()
  expect(mocks.save).not.toHaveBeenCalled()
})

describe.each(['modify', 'create'] as const)('Editor AI toolbar sessions in %s mode', (mode) => {
  it('restores toolbar mode after a pane mode switch and clears the previous requirement and preview', async () => {
    const paneMode: WorkflowAiMode = mode === 'modify' ? 'create' : 'modify'
    const previousDraft = makeDraft('旧会话已完成的修改')
    const nextDraft = makeDraft('新会话的修改')
    generate.mockResolvedValueOnce(previousDraft).mockResolvedValueOnce(nextDraft)
    render(<Editor embedded initialWorkflowId={source.id} />)
    const toolbar = await screen.findByRole('button', { name: modeUi[mode].toolbar })

    fireEvent.click(toolbar)
    expectFreshSession(mode)
    fireEvent.click(assistant().getByRole('button', { name: modeUi[paneMode].button, exact: true }))
    submitRequirement(paneMode, '上一会话的需求')
    expect(await screen.findByRole('region', { name: 'AI 草稿预览' })).toHaveTextContent(previousDraft.summary)
    expect(generate).toHaveBeenNthCalledWith(1, paneMode, '上一会话的需求', source, expect.any(AbortSignal), expect.objectContaining({ requestId: expect.any(String) }))

    // The toolbar prop is unchanged even though the pane changed its local mode.
    fireEvent.click(toolbar)
    expectFreshSession(mode)
    expect(screen.queryByText(previousDraft.summary)).not.toBeInTheDocument()
    expect(generate).toHaveBeenCalledTimes(1)

    submitRequirement(mode, '本次工具栏会话的需求')
    expect(await screen.findByRole('region', { name: 'AI 草稿预览' })).toHaveTextContent(nextDraft.summary)
    expect(generate).toHaveBeenCalledTimes(2)
    expect(generate).toHaveBeenNthCalledWith(2, mode, '本次工具栏会话的需求', source, expect.any(AbortSignal), expect.objectContaining({ requestId: expect.any(String) }))
    expect(useEditorStore.getState().spec).toEqual(source)
    expect(useEditorStore.getState().isDirty).toBe(false)
  })

  it('restarts even for the same toolbar mode, aborting the old request and ignoring its late result', async () => {
    let resolvePrevious!: (draft: WorkflowAiDraft) => void
    const previousDraft = makeDraft('旧会话延迟返回的修改')
    const nextDraft = makeDraft('新会话先完成的修改')
    // Deliberately resolve despite abort to model a non-cooperative transport.
    generate.mockReturnValueOnce(new Promise((resolve) => { resolvePrevious = resolve }))
      .mockResolvedValueOnce(nextDraft)
    render(<Editor embedded initialWorkflowId={source.id} />)
    const toolbar = await screen.findByRole('button', { name: modeUi[mode].toolbar })

    fireEvent.click(toolbar)
    submitRequirement(mode, '仍在生成的旧需求')
    expect(generate).toHaveBeenNthCalledWith(1, mode, '仍在生成的旧需求', source, expect.any(AbortSignal), expect.objectContaining({ requestId: expect.any(String) }))
    const previousSignal = generate.mock.calls[0][3]!
    expect(previousSignal.aborted).toBe(false)
    expect(assistant().getByRole('button', { name: '生成中…' })).toBeDisabled()

    fireEvent.click(toolbar)
    expect(previousSignal.aborted).toBe(true)
    expectFreshSession(mode)
    expect(generate).toHaveBeenCalledTimes(1)

    submitRequirement(mode, '新会话的独立需求')
    const nextPreview = await screen.findByRole('region', { name: 'AI 草稿预览' })
    expect(nextPreview).toHaveTextContent(nextDraft.summary)
    await act(async () => { resolvePrevious(previousDraft) })

    expect(screen.getByRole('region', { name: 'AI 草稿预览' })).toBe(nextPreview)
    expect(nextPreview).toHaveTextContent(nextDraft.summary)
    expect(screen.queryByText(previousDraft.summary)).not.toBeInTheDocument()
    expect(expectMode(mode)).toHaveValue('新会话的独立需求')
    expect(generate).toHaveBeenCalledTimes(2)
    expect(generate).toHaveBeenNthCalledWith(2, mode, '新会话的独立需求', source, expect.any(AbortSignal), expect.objectContaining({ requestId: expect.any(String) }))
    expect(useEditorStore.getState().spec).toEqual(source)
    expect(useEditorStore.getState().isDirty).toBe(false)
  })
})
